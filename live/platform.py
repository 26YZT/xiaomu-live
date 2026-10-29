"""Local installation, credentials, preferences and conservative usage accounting."""
from __future__ import annotations

import json
import os
import platform as system_platform
import shutil
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from aiohttp import web

from .vidu import ViduClient

APP_ID = "local-avatar-studio"
TZ = ZoneInfo("Asia/Shanghai")
CREDENTIAL_FILES = {"vidu": "vidu.key", "evolink": "evolink.key", "youtube": "youtube.key", "obs": "obs.password"}


def managed_secret(root: Path, provider: str) -> tuple[bool, str | None]:
    """A UI save/removal takes precedence over legacy config and shell environment."""
    marker = root / ".state/credential-overrides.json"
    managed = json.loads(marker.read_text()).get(provider, False) if marker.exists() else False
    path = root / ".secrets" / CREDENTIAL_FILES[provider]
    if managed or path.exists():
        return True, path.read_text().strip() if path.exists() else None
    return False, None


@contextmanager
def server_instance(root: Path):
    """Claim this data directory before startup can recover/mutate its usage ledger."""
    import fcntl
    folder = root / ".state"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "server.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("当前项目已有服务运行或正在退出，请等待原进程结束。") from None
        try:
            (folder / "server.pid").write_text(str(os.getpid()))
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def load_preferences(root: Path, cfg: dict) -> None:
    path = root / ".state/preferences.json"
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        for group in ("schedule", "safety"):
            cfg[group].update(saved.get(group, {}))


class UsageLedger:
    """Local estimates persist across reconnects and restarts. Not a provider invoice."""

    def __init__(self, root: Path):
        self.path = root / ".state/usage.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, profile TEXT, mode TEXT, started REAL, updated REAL,
                ended REAL, rate REAL, estimated REAL DEFAULT 0, reported REAL DEFAULT 0,
                status TEXT, reason TEXT DEFAULT '')""")
            # A previous process cannot prove that the provider has stopped billing.
            db.execute("UPDATE sessions SET status='unconfirmed', ended=updated, reason='服务中断，真实费用请核对 Vidu 账单' WHERE status='active'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def start(self, session_id: str, profile: str, rate: float, mode: str = "vidu") -> None:
        now = time.time()
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO sessions (id,profile,mode,started,updated,rate,status) VALUES (?,?,?,?,?,?,?)",
                       (session_id, profile, mode, now, now, rate if mode == "vidu" else 0, "active"))

    def update(self, session_id: str, reported: float | None = None, *, close: bool = False, reason: str = "", confirmed: bool = True, ended_at: float | None = None) -> None:
        now = time.time()
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                return
            estimate = max(row["estimated"], (now - row["started"]) * row["rate"]) if row["status"] == "active" else row["estimated"]
            db.execute("UPDATE sessions SET updated=?,ended=?,estimated=?,reported=?,status=?,reason=? WHERE id=?",
                       (now, max(row["started"], min(now, float(ended_at or now))) if close else row["ended"], estimate,
                        max(row["reported"], float(reported or 0)), ("ended" if confirmed else "unconfirmed") if close else row["status"], reason or row["reason"], session_id))

    def unconfirmed(self) -> list[str]:
        with self.connect() as db:
            return [row[0] for row in db.execute("SELECT id FROM sessions WHERE status='unconfirmed' AND mode='vidu' ORDER BY started")]

    def total_credits(self, session_ids: set[str]) -> float:
        """Include overlapping outfit sessions before their final provider invoices arrive."""
        if not session_ids:
            return 0.0
        now = time.time()
        with self.connect() as db:
            rows = db.execute(f"SELECT * FROM sessions WHERE id IN ({','.join('?' for _ in session_ids)})", tuple(session_ids)).fetchall()
        return round(sum(max(row["reported"], row["estimated"],
                             ((now if row["status"] == "active" else row["ended"] or row["updated"]) - row["started"]) * row["rate"])
                         for row in rows), 2)

    def summary(self, price: float, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        midnight = datetime.fromtimestamp(now, TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        day_start, day_end = midnight.timestamp(), (midnight + timedelta(days=1)).timestamp()
        today = 0.0
        recent = []
        with self.connect() as db:
            rows = db.execute("SELECT * FROM sessions ORDER BY started DESC").fetchall()
        for row in rows:
            item = dict(row)
            end = now if item["status"] == "active" else (item["ended"] or item["updated"])
            estimate = max(item["estimated"], (end - item["started"]) * item["rate"])
            guard = max(estimate, item["reported"])
            duration = max(1e-9, end - item["started"])
            overlap = max(0, min(end, day_end) - max(item["started"], day_start))
            share = 1 if day_start <= item["started"] <= end < day_end else min(1, overlap / duration)
            today += guard * share
            if len(recent) < 30:
                recent.append({**item, "estimated": round(guard, 2), "rmb": round(guard * price, 2),
                               "elapsed": max(0, int(end - item["started"]))})
        return {"today_credits": round(today, 2), "today_rmb": round(today * price, 2),
                "date": midnight.date().isoformat(), "timezone": "Asia/Shanghai", "sessions": recent,
                "basis": "本地保守估算，包含会话准备时间；真实扣费以 Vidu 账单为准"}


@web.middleware
async def local_requests(request, handler):
    host = urlsplit("//" + request.host).hostname
    if host not in ("localhost", "127.0.0.1", "::1"):
        return web.json_response({"error": "仅接受本机访问"}, status=403)
    if request.method not in ("GET", "HEAD", "OPTIONS") or request.headers.get("Upgrade", "").lower() == "websocket":
        origin = request.headers.get("Origin")
        expected = f"{request.scheme}://{request.host}"
        if (origin and origin != expected) or request.headers.get("Sec-Fetch-Site") == "cross-site":
            return web.json_response({"error": "请从本机工作台提交操作"}, status=403)
    try:
        return await handler(request)
    except (ValueError, TypeError, json.JSONDecodeError):
        return web.json_response({"error": "输入格式不正确，请检查填写内容"}, status=400)


class PlatformRuntime:
    PROVIDERS = CREDENTIAL_FILES

    def __init__(self, root: Path, room, workbench):
        self.root, self.room, self.workbench = root, room, workbench
        self.secret_dir = root / ".secrets"
        self.ledger = UsageLedger(root)
        self.demo_round = 0
        room.usage_ledger = self.ledger

    def persist(self):
        write_json(self.root / ".state/preferences.json", {
            "schedule": {**self.room.cfg["schedule"], "policy": self.room.queue.policy},
            "safety": self.room.cfg["safety"],
        })

    def status(self):
        room = self.room
        usage = self.ledger.summary(room.cfg["vidu"]["credit_price_rmb"])
        return {"app_id": APP_ID, "version": "0.1.3", "pid": os.getpid(), "root": str(self.root.resolve()),
                "services": {"vidu": bool(room.api_key), "evolink": bool(self.workbench.key),
                             "youtube": bool(room.broadcaster.youtube_key()), "obs": (self.secret_dir / "obs.password").exists()},
                "checks": {"python": system_platform.python_version(), "ffmpeg": bool(shutil.which("ffmpeg")),
                           "rtc_sdk": (room.root / "static/vendor/aliyun-rtc-sdk-7.1.9.js").exists(),
                           "obs_installed": Path("/Applications/OBS.app").exists() or bool(shutil.which("obs"))},
                "usage": usage, "daily_budget_credits": room.cfg["safety"].get("daily_budget_credits", 3200),
                "auto_recover": bool(room.cfg["schedule"].get("auto_recover")),
                "automatic_outfits": bool(room.cfg["safety"].get("automatic_outfits", False)),
                "inbound": getattr(room, "last_external_message", None),
                "ready": bool(room.api_key and (room.root / room.avatar_cfg["image"]).exists())}

    def save_secret(self, provider: str, value: str, remove: bool = False):
        if provider not in self.PROVIDERS:
            raise ValueError("unknown provider")
        if self.room.status in ("creating", "connecting", "waiting_stage", "live", "ending"):
            raise web.HTTPConflict(text=json.dumps({"error": "请先停止数字人，再修改连接配置"}, ensure_ascii=False), content_type="application/json")
        if provider == "evolink" and any(job.get("status") in ("queued", "running") for job in self.workbench.jobs):
            raise web.HTTPConflict(text=json.dumps({"error": "素材任务还在运行，请等待完成后再更换或移除 Evolink 配置"}, ensure_ascii=False), content_type="application/json")
        value = value.strip()
        if not remove and (not value or len(value) > 4096 or any(c.isspace() for c in value)):
            raise ValueError("invalid credential")
        if provider == "vidu" and not remove and not value.startswith("vda_"):
            raise ValueError("invalid Vidu key prefix")
        self.secret_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.secret_dir.chmod(0o700)
        path = self.secret_dir / self.PROVIDERS[provider]
        if remove:
            path.unlink(missing_ok=True)
        else:
            with tempfile.NamedTemporaryFile("w", dir=self.secret_dir, prefix=".key-", delete=False) as stream:
                temp = Path(stream.name)
                stream.write(value)
            try:
                temp.chmod(0o600)
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)
        marker = self.root / ".state/credential-overrides.json"
        overrides = json.loads(marker.read_text()) if marker.exists() else {}
        overrides[provider] = True
        write_json(marker, overrides)
        key = None if remove else value
        if provider == "vidu":
            self.room.api_key = key
            self.room.vidu = ViduClient(self.room.http, key, self.room.cfg["vidu"]["env"], self.room.cfg["vidu"].get("base_url")) if key else None
        elif provider == "evolink":
            self.workbench.key = key
            # Library generation is explicitly requested by the operator; viewers cannot generate outfits.
            self.room.resolver.generator = None
        elif provider == "youtube":
            self.room.broadcaster.cfg["youtube_key_file"] = str(path)
        self.room.log("info", f"{provider} 连接配置已{'移除' if remove else '保存'}")
        self.room.broadcast("state")


def add_platform_routes(app, runtime_key):
    routes = web.RouteTableDef()

    @routes.get("/api/platform")
    async def status(request):
        return web.json_response(request.app[runtime_key].status(), headers={"Cache-Control": "no-store"})

    @routes.post("/api/platform/credentials")
    async def credentials(request):
        body = await request.json()
        if not isinstance(body.get("remove", False), bool):
            raise ValueError("remove must be a boolean")
        runtime = request.app[runtime_key]
        runtime.save_secret(str(body.get("provider", "")), str(body.get("value", "")), bool(body.get("remove")))
        return web.json_response({"ok": True, "services": runtime.status()["services"]})

    @routes.post("/api/platform/verify-vidu")
    async def verify(request):
        room = request.app[runtime_key].room
        if not room.vidu:
            return web.json_response({"error": "请先保存 Vidu API Key"}, status=400)
        try:
            await room.vidu.request("GET", "/live/v1/voices")
        except Exception as error:
            return web.json_response({"error": str(error)[:200]}, status=502)
        return web.json_response({"ok": True, "message": "音色查询成功；尚未创建付费数字人会话"})

    @routes.post("/api/platform/preferences")
    async def preferences(request):
        runtime = request.app[runtime_key]
        body = await request.json()
        if "daily_budget_credits" in body:
            runtime.room.cfg["safety"]["daily_budget_credits"] = max(1, min(100000, int(body["daily_budget_credits"])))
        if "auto_recover" in body:
            if not isinstance(body["auto_recover"], bool):
                raise ValueError("invalid boolean")
            runtime.room.cfg["schedule"]["auto_recover"] = body["auto_recover"]
        runtime.persist()
        runtime.room.broadcast("state")
        return web.json_response(runtime.status())

    @routes.post("/api/platform/demo")
    async def demo(request):
        runtime = request.app[runtime_key]
        room = runtime.room
        if room.status in ("creating", "connecting", "waiting_stage", "live", "ending"):
            return web.json_response({"error": "请先停止当前会话，再开始演练"}, status=409)
        previous = list(room.queue.waiting())
        await room.start(dry_run=True)
        runtime.demo_round += 1
        viewer = f"体验观众 {runtime.demo_round}"
        examples = [(viewer, "你好呀"), (viewer, "比个心"), ("广告测试", "加我微信"), (viewer, "你是真人吗")]
        results = [await room.ingest(user, text, source="demo") for user, text in examples]
        return web.json_response({"ok": True, "accepted": sum(bool(r["accepted"]) for r in results),
                                  "filtered": sum(not r["accepted"] for r in results), "archived": len(previous)})

    app.add_routes(routes)
