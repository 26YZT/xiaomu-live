"""Provision and launch the real cyber-xiaoya-live checkout on this Mac.

User data and legacy files are retained. Never starts a Vidu session.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCE = ROOT / "upstream/cyber-xiaoya-live"
URL = "http://127.0.0.1:8795"
APP = "cyber-xiaoya-live-local"


def read_json(path, fallback=None):
    return json.loads(path.read_text()) if path.exists() else fallback or {}


def provision():
    target = SOURCE / "config.json"
    if target.exists():
        return
    from live.platform import managed_secret
    old = read_json(ROOT / "config.json")
    profiles = read_json(ROOT / "assets/avatars/profiles.json")
    key = old.get("avatar_profile", "xiaoshen")
    profile = profiles.get(key) or next(iter(profiles.values()))
    cfg = read_json(SOURCE / "config.example.json")
    _, vidu_key = managed_secret(ROOT, "vidu")
    cfg["vidu"]["api_key"] = vidu_key or ""
    picture = ROOT / profile["image"]
    avatar_id = ""
    if vidu_key:
        account = hashlib.sha256(vidu_key.encode()).hexdigest()[:24]
        digest = hashlib.sha256(picture.read_bytes()).hexdigest()
        for cache_key, value in read_json(ROOT / ".state/avatars.json").items():
            if cache_key.endswith(f":{account}:{digest}") and time.time() - value.get("created", 0) < 80 * 86400:
                avatar_id = str(value["id"])
                break
    cfg["avatar"] = {"name": profile.get("name", "小沐"), "avatar_id": avatar_id,
                     "image_uri": None if avatar_id else "data:image/png;base64," + base64.b64encode(picture.read_bytes()).decode()}
    cfg["llm"].update(key_file=str(ROOT / ".secrets/llm.key"), max_tokens=160,
                       system_prompt=(ROOT / profile["persona_file"]).read_text())
    files = ROOT / "assets/live-audio"
    files.mkdir(parents=True, exist_ok=True)
    cfg["tts"].update(engine="macos", voice="Tingting", files_dir=str(files),
                       voices_dir=str(ROOT / "assets/local-voices"))
    Path(cfg["tts"]["voices_dir"]).mkdir(parents=True, exist_ok=True)
    cfg["rtc"].update(managed_recording=False, record_dir=str(ROOT / "recordings/upstream"))
    safety = read_json(ROOT / ".state/preferences.json").get("safety", {})
    cfg["session"].update(max_duration_seconds=min(120, int(safety.get("max_session_minutes", 2)) * 60),
                           idle_timeout_seconds=120, greeting="大家好，我是小沐。这是一场本地试播，可以跟我聊聊天。")
    cfg["local"] = {"budget_credits": min(200, int(safety.get("budget_credits", 200))),
                    "daily_budget_credits": int(safety.get("daily_budget_credits", 3200)),
                    "credits_per_second": 1.5, "credit_price_rmb": .03125}
    cfg["bridge"]["port"] = 8795
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f: json.dump(cfg, f, ensure_ascii=False, indent=2)
    print("已迁入小沐形象与人设、Vidu 配置和试播上限；大模型 Key 等待本机填写。")


def get(url):
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.load(response)


def status():
    try:
        data = get(URL + "/api/local/status")
        return data if data.get("app_id") == APP and data.get("workspace") == str(ROOT) else {"other": True}
    except Exception:
        try:
            with socket.create_connection(("127.0.0.1", 8795), timeout=.3):
                return {"other": True}
        except PermissionError as error:
            raise RuntimeError("无法检查本机服务：当前环境不允许连接 localhost；未启动或停止任何进程") from error
        except ConnectionRefusedError:
            return None
        except OSError as error:
            raise RuntimeError("无法检查本机服务：网络检查未完成；未启动或停止任何进程") from error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("setup", "start", "stop", "status"), default="start", nargs="?")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.action == "setup":
        provision(); return
    running = status()
    if running and running.get("other"):
        raise SystemExit("8795 端口由其他服务占用；未修改该进程。")
    if args.action == "status":
        print("原版已运行：" + URL + "/stage" if running else "原版未运行")
        return
    if args.action == "stop":
        if running:
            os.kill(int(running["pid"]), signal.SIGTERM)
            for _ in range(150):
                if status() is None:
                    print("原版已停止。"); return
                time.sleep(.2)
            raise SystemExit("原版仍在清理会话，请稍后查看日志。")
        print("原版已经停止。"); return
    if not running:
        try:
            legacy = get("http://127.0.0.1:28900/api/platform")
        except Exception:
            legacy = None
        if legacy and legacy.get("root") == str(ROOT):
            state = get("http://127.0.0.1:28900/api/state")
            if state["status"] not in ("idle", "ended", "error"):
                raise SystemExit("旧版有会话在运行，请先停止；未中断它。")
            subprocess.run([str(ROOT / ".venv/bin/python"), str(ROOT / "tools/launch.py"), "stop"], check=True, cwd=ROOT)
        provision()
        python = SOURCE / ".venv/bin/python"
        if not python.exists():
            raise SystemExit("原版独立环境尚未安装。")
        env = os.environ.copy()
        env["PATH"] = str(Path.home() / ".local/bin") + ":/usr/local/bin:/usr/bin:" + env.get("PATH", "")
        folder = ROOT / "logs"; folder.mkdir(exist_ok=True)
        with (folder / "upstream-launch.log").open("ab") as log:
            process = subprocess.Popen([str(python), "-u", "-m", "src.local_main"], cwd=SOURCE, env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        for _ in range(100):
            if process.poll() is not None:
                raise SystemExit("原版启动失败，请查看 logs/upstream-launch.log。")
            running = status()
            if running and not running.get("other"):
                break
            time.sleep(.2)
        else:
            raise SystemExit("原版启动检查超时，请查看日志。")
    print("原版已运行：" + URL + "/stage")
    if not args.no_browser:
        webbrowser.open(URL + ("/stage" if running.get("llm_ready") else "/stage?setup=1"))


if __name__ == "__main__":
    main()
