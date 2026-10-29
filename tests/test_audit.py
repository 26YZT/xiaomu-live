"""Regression tests for lifecycle/account/stream issues found in the product audit."""
import asyncio
import json
import shutil
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import WSServerHandshakeError

from tests import test_e2e
from tests import test_platform
from live.router import Command


class LifecycleAudit(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_e2e.EndToEnd.asyncSetUp
    asyncTearDown = test_e2e.EndToEnd.asyncTearDown
    post = test_e2e.EndToEnd.post
    wait_for = test_e2e.EndToEnd.wait_for
    frames = test_e2e.EndToEnd.frames

    async def start_ready(self):
        await self.post("/api/session/start", {"model": "vidu-s2"})
        self.assertTrue(await self.wait_for(lambda: self.room.status == "waiting_stage"))

    async def test_stop_during_create_retires_the_late_session(self):
        original = self.room.vidu.create_live
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(body):
            result = await original(body)
            entered.set()
            await release.wait()
            return result

        self.room.vidu.create_live = delayed
        start = asyncio.create_task(self.post("/api/session/start", {}))
        await asyncio.wait_for(entered.wait(), 5)
        stop = asyncio.create_task(self.post("/api/session/stop", {}))
        await asyncio.sleep(0.1)
        release.set()
        await asyncio.wait_for(asyncio.gather(start, stop), 10)
        self.assertEqual(self.room.status, "ended")
        self.assertEqual({f["live_id"] for f in self.frames(5)}, set(self.mock_app[test_e2e.LIVES]))

    async def test_new_start_cannot_overtake_final_billing(self):
        await self.start_ready()
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.room._refresh_billing

        async def delayed():
            entered.set()
            await release.wait()
            return await original()

        self.room._refresh_billing = delayed
        stop = asyncio.create_task(self.post("/api/session/stop", {}))
        await asyncio.wait_for(entered.wait(), 5)
        try:
            self.assertEqual(self.room.status, "ending")
            status, _ = await self.post("/api/session/start", {})
            self.assertEqual(status, 400)
            self.assertEqual(len(self.mock_app[test_e2e.LIVES]), 1)
        finally:
            release.set()
            await asyncio.wait_for(stop, 5)

    async def test_stop_during_outfit_handover_closes_both_sessions(self):
        await self.start_ready()
        first = self.room.live["id"]
        cmd = Command("观众", "换夹克", "outfit", 0, item="夹克", asset={"name": "夹克", "file": str(test_e2e.ROOT / "assets/avatars/xiaoshen.jpg")})
        # Use the scheduler's tracked task, as real queued commands do.
        self.room._spawn(self.room._execute(cmd))
        self.assertTrue(await self.wait_for(lambda: self.room.live["id"] != first, timeout=10))
        await self.post("/api/session/stop", {})
        self.assertEqual({f["live_id"] for f in self.frames(5)}, set(self.mock_app[test_e2e.LIVES]))

    async def test_stop_while_outfit_is_preparing_does_not_wait_for_ready_timeout(self):
        await self.start_ready()
        original = self.room._new_channel
        prepared = asyncio.Event()

        def channel_without_ready(*args, **kwargs):
            channel = original(*args, **kwargs)
            channel._retry_init = AsyncMock()  # mock's first NOT_READY is never followed by success
            prepared.set()
            return channel

        self.room._new_channel = channel_without_ready
        cmd = Command("观众", "换夹克", "outfit", 0, item="夹克", asset={"name": "夹克", "file": str(test_e2e.ROOT / "assets/avatars/xiaoshen.jpg")})
        self.room._spawn(self.room._execute(cmd))
        await asyncio.wait_for(prepared.wait(), 5)
        await asyncio.wait_for(self.post("/api/session/stop", {}), 5)
        self.assertEqual(self.room.status, "ended")
        self.assertEqual({f["live_id"] for f in self.frames(5)}, set(self.mock_app[test_e2e.LIVES]))

    async def test_stop_closes_a_reconnecting_control_socket(self):
        await self.start_ready()
        candidates = []
        original = self.room._new_channel
        def capture(*args, **kwargs):
            candidate = original(*args, **kwargs)
            candidates.append(candidate)
            return candidate
        self.room._new_channel = capture
        async with self.http.post(self.mock_url + "/_drop"):
            pass
        self.assertTrue(await self.wait_for(lambda: candidates and candidates[0].ws is not None, timeout=5))
        await self.post("/api/session/stop", {})
        self.assertTrue(candidates[0].ws.closed)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "requires media tools")
    async def test_real_webm_ingest_records_h264_and_audible_aac(self):
        from live.broadcast import Broadcaster
        import array
        source = self.tmp / "signal.webm"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=25",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "2", "-c:v", "libvpx",
                        "-deadline", "realtime", "-c:a", "libopus", str(source)], check=True, capture_output=True, timeout=15)
        cfg = {**self.room.cfg["broadcast"], "width": 216, "height": 384, "video_bitrate": "300k", "video_bufsize": "600k"}
        self.room.broadcaster = Broadcaster(self.room, self.tmp, cfg)
        await self.post("/api/output", {"mode": "builtin", "target": "file"})
        await self.start_ready()
        await self.post("/api/stage/claim", {"live_id": self.room.live["id"], "client_id": "media-test", "role": "broadcast"})
        await self.post("/api/stage", {"live_id": self.room.live["id"], "event": "joined", "client_id": self.room.stage_owner})
        hello = {"client_id": "media-test", "live_id": self.room.live["id"], "mime": "video/webm", "hasAudio": True}
        async with self.http.ws_connect(self.url + "/api/broadcast/ingest") as ws:
            await ws.send_json({**hello, "client_id": "unclaimed-page"})
            await ws.receive(timeout=3)
            self.assertEqual(ws.close_code, 1008)
        async with self.http.ws_connect(self.url + "/api/broadcast/ingest") as ws:
            await ws.send_json(hello)
            raw = source.read_bytes()
            for offset in range(0, len(raw), 16384):
                await ws.send_bytes(raw[offset:offset + 16384])
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.5)
        self.assertTrue(await self.wait_for(lambda: not self.room.broadcaster.receiving, timeout=8))
        await asyncio.sleep(3)
        encoder = self.room.broadcaster._encoder
        threads = list(self.room.broadcaster._threads)
        await self.post("/api/session/stop", {})
        self.assertTrue(encoder.stderr.closed)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        output = next((self.tmp / "logs").glob("broadcast-*.flv"))
        probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)], timeout=10))
        self.assertEqual({s["codec_name"] for s in probe["streams"]}, {"h264", "aac"})
        self.assertGreater(float(probe["format"]["duration"]), 2)
        audio = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(output), "-map", "0:a", "-f", "s16le", "-"], timeout=10)
        samples = array.array("h", audio)
        self.assertGreater(max(abs(x) for x in samples), 500, "test tone must reach the recorded output")

    async def test_avatar_cache_is_scoped_to_credentials(self):
        await self.room._ensure_avatar()
        self.room.vidu.api_key = "vda_second_account"
        self.room.vidu.upload_avatar = AsyncMock(return_value={"id": "second-account-avatar"})
        self.room.vidu.get_avatar = AsyncMock(return_value={"status": "success"})
        result = await self.room._ensure_avatar()
        self.assertEqual(result["id"], "second-account-avatar")

    async def test_unconfirmed_cloud_end_blocks_another_paid_create(self):
        await self.start_ready()
        original = self.room.vidu.get_live
        async def still_running(live_id):
            result = await original(live_id)
            result["live"]["status"] = "on_live"
            return result
        self.room.vidu.get_live = still_running
        await self.post("/api/session/stop", {})
        self.assertTrue(self.room.usage_ledger.unconfirmed())
        status, _ = await self.post("/api/session/start", {})
        self.assertEqual(status, 400)
        self.assertEqual(len(self.mock_app[test_e2e.LIVES]), 1)
        self.room.vidu.get_live = original
        status, _ = await self.post("/api/session/start", {})
        self.assertEqual(status, 200)
        self.assertEqual(len(self.mock_app[test_e2e.LIVES]), 2)

    async def test_parallel_avatar_uploads_preserve_both_cache_entries(self):
        image_a, image_b = self.tmp / "a.jpg", self.tmp / "b.jpg"
        image_a.write_bytes(b"mock-image-a")
        image_b.write_bytes(b"mock-image-b")
        index = 0

        async def upload(*args):
            nonlocal index
            index += 1
            value = index
            await asyncio.sleep(0.01)
            return {"id": str(value)}

        self.room.vidu.upload_avatar = upload
        self.room.vidu.get_avatar = AsyncMock(return_value={"status": "success"})
        await asyncio.gather(self.room._ensure_avatar(image_a), self.room._ensure_avatar(image_b))
        cache = json.loads((self.tmp / ".state/avatars.json").read_text())
        self.assertEqual(len(cache), 2)

    async def test_unbudgeted_experimental_paid_route_is_disabled(self):
        self.room.cfg["safety"]["daily_budget_credits"] = 1
        status, _ = await self.post("/api/trial/start", {"mode": "component", "image": "assets/avatars/xiaoshen.jpg"})
        self.assertEqual(status, 403)

    async def test_manual_restore_uses_the_base_wardrobe_photo(self):
        wardrobe = self.tmp / "outfits.json"
        image = str(test_e2e.ROOT / "assets/avatars/xiaoshen.jpg")
        wardrobe.write_text(json.dumps({"items": [{"name": "底图", "file": image, "base": True}, {"name": "夹克", "file": image}]}))
        self.room.cfg["avatars"]["xiaoshen"]["outfits"] = str(wardrobe)
        await self.post("/api/session/start", {"dry_run": True})
        self.room.outfit = "夹克"
        await self.post("/api/manual", {"kind": "reset", "content": ""})
        self.assertEqual(self.room.outfit, "底图")


class PlatformAudit(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_platform.PlatformAPI.asyncSetUp
    asyncTearDown = test_platform.PlatformAPI.asyncTearDown
    start_app = test_platform.PlatformAPI.start_app
    post = test_platform.PlatformAPI.post

    async def test_new_demo_does_not_execute_messages_from_an_earlier_queue(self):
        import server
        room = self.app[server.ROOM]
        room.cfg["schedule"]["demo_interval_seconds"] = 60
        await self.post("/api/danmaku", {"user": "之前的观众", "text": "之前的问题"})
        await self.post("/api/platform/demo", {})
        self.assertEqual(len(room.queue.waiting()), 3)
        self.assertTrue(any(c.text == "之前的问题" and c.status == "expired" for c in room.history))

    def cli_vidu_key(self):
        import server
        legacy = self.root / "legacy.key"
        legacy.write_text("vda_legacy_test")
        (self.root / "config.json").write_text(json.dumps({"vidu": {"api_key_file": str(legacy)}, "generator": {"api_key_file": str(self.root / "none")}}))
        with patch.object(server, "ROOT", self.root), patch.object(sys, "argv", ["server.py"]), \
             patch.dict(os.environ, {"VIDU_API_KEY": "", "EVOLINK_API_KEY": ""}), \
             patch.object(server, "build_app") as build, patch.object(server.web, "run_app"), patch("builtins.print"):
            server.main()
            return build.call_args.args[1]

    async def test_saved_key_wins_over_legacy_config_after_restart(self):
        await self.post("/api/platform/credentials", {"provider": "vidu", "value": "vda_saved_test"})
        self.assertEqual(self.cli_vidu_key(), "vda_saved_test")

    async def test_removed_key_does_not_reappear_from_legacy_config(self):
        await self.post("/api/platform/credentials", {"provider": "vidu", "value": "vda_saved_test"})
        await self.post("/api/platform/credentials", {"provider": "vidu", "remove": True})
        self.assertIsNone(self.cli_vidu_key())

    async def test_evolink_key_cannot_change_under_running_generation(self):
        import server
        self.app[server.WB].jobs.append({"id": "pending", "status": "running"})
        status, _ = await self.post("/api/platform/credentials", {"provider": "evolink", "value": "test-new-key"})
        self.assertEqual(status, 409)

    async def test_websocket_cross_origin_is_rejected_before_upgrade(self):
        try:
            async with self.client.ws_connect(self.url + "/api/broadcast/ingest", origin="https://unrelated.example"):
                self.fail("cross-origin WebSocket was accepted")
        except WSServerHandshakeError as error:
            self.assertEqual(error.status, 403)


class ProcessAudit(unittest.TestCase):
    def test_duplicate_server_does_not_rewrite_active_usage(self):
        from live.platform import UsageLedger
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "static").mkdir()
            shutil.copy(test_e2e.ROOT / "moderation.json", root / "moderation.json")
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            cfg = {"server": {"host": "127.0.0.1", "port": port},
                   "vidu": {"api_key_file": str(root / "no-key")},
                   "generator": {"enabled": False, "api_key_file": str(root / "no-key")},
                   "broadcast": {"youtube_key_file": str(root / "no-key")}}
            (root / "config.json").write_text(json.dumps(cfg))
            script = "from pathlib import Path; import server,sys; server.ROOT=Path(sys.argv.pop()); server.main()"
            command = [sys.executable, "-c", script, str(root)]
            env = {k: v for k, v in os.environ.items() if k not in ("VIDU_API_KEY", "EVOLINK_API_KEY")}
            first = subprocess.Popen(command, cwd=test_e2e.ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/platform", timeout=0.2):
                            break
                    except OSError:
                        if time.monotonic() > deadline:
                            self.fail("test server did not start")
                        time.sleep(0.05)
                ledger = UsageLedger(root)
                ledger.start("still-running", "主播", 1.5)
                second = subprocess.run(command, cwd=test_e2e.ROOT, env=env, capture_output=True, timeout=10)
                self.assertNotEqual(second.returncode, 0)
                with sqlite3.connect(root / ".state/usage.sqlite3") as db:
                    status = db.execute("SELECT status FROM sessions WHERE id='still-running'").fetchone()[0]
                self.assertEqual(status, "active")
            finally:
                first.terminate()
                first.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
