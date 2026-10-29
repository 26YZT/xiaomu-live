"""Platform acceptance checks: no paid APIs, restart persistence and budget accounting."""
import copy
import json
import stat
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from aiohttp import ClientSession, web

import server
from live.platform import UsageLedger, TZ


class UsageLedgerTest(unittest.TestCase):
    def test_cost_survives_restart_and_new_session(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            now = datetime.now(TZ).replace(hour=12, minute=0, second=0, microsecond=0).timestamp()
            with patch("live.platform.time.time", return_value=now):
                ledger = UsageLedger(root)
                ledger.start("first", "主播", 1.5)
            with patch("live.platform.time.time", return_value=now + 10):
                ledger.update("first", 12, close=True)
                reopened = UsageLedger(root)
                reopened.start("second", "主播", 1.5)
            result = reopened.summary(0.03125, now + 20)
            self.assertAlmostEqual(result["today_credits"], 30, delta=0.1)
            self.assertEqual(len(result["sessions"]), 2)

    def test_demo_is_free_and_interrupted_cost_does_not_keep_growing(self):
        with tempfile.TemporaryDirectory() as folder:
            root, now = Path(folder), datetime.now(TZ).replace(hour=12, minute=0, second=0, microsecond=0).timestamp()
            with patch("live.platform.time.time", return_value=now):
                ledger = UsageLedger(root)
                ledger.start("demo", "示例", 1.5, "dry_run")
                ledger.start("cloud", "主播", 1.5)
            with patch("live.platform.time.time", return_value=now + 8):
                ledger.update("cloud")
                ledger.update("demo", close=True)
                ledger = UsageLedger(root)
            result = ledger.summary(0.03125, now + 180)
            self.assertAlmostEqual(result["today_credits"], 12, delta=0.1)
            self.assertEqual(next(s for s in result["sessions"] if s["id"] == "cloud")["status"], "unconfirmed")

    def test_session_crossing_shanghai_midnight_is_split_by_duration(self):
        with tempfile.TemporaryDirectory() as folder:
            midnight = datetime(2026, 10, 23, tzinfo=TZ).timestamp()
            with patch("live.platform.time.time", return_value=midnight - 10):
                ledger = UsageLedger(Path(folder))
                ledger.start("overnight", "主播", 1.5)
            self.assertAlmostEqual(ledger.summary(0.03125, midnight + 10)["today_credits"], 15)


class PlatformAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "assets").mkdir()
        self.client = ClientSession()
        self.cfg = copy.deepcopy(server.DEFAULTS)
        self.cfg["vidu"]["base_url"] = "http://127.0.0.1:1"
        self.cfg["generator"]["enabled"] = False
        self.cfg["schedule"].update(demo_interval_seconds=0.02, idle_prompt_after_seconds=0)
        self.cfg["safety"].update(daily_budget_credits=3200, gift_priority_enabled=False, automatic_outfits=False, large_actions=False)
        await self.start_app()

    async def start_app(self):
        self.app = server.build_app(copy.deepcopy(self.cfg), None, None, assets_dir=self.root / "assets", work_dir=self.root)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])

    async def asyncTearDown(self):
        await self.runner.cleanup()
        await self.client.close()
        self.tmp.cleanup()

    async def post(self, path, body, headers=None):
        async with self.client.post(self.url + path, json=body, headers=headers) as response:
            return response.status, await response.json()

    async def test_write_only_credential_permissions_and_removal(self):
        secret = "vda_fixture_test_000"
        status, data = await self.post("/api/platform/credentials", {"provider": "vidu", "value": secret})
        self.assertEqual(status, 200)
        path = self.root / ".secrets/vidu.key"
        self.assertEqual(path.read_text(), secret)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        async with self.client.get(self.url + "/api/platform") as response:
            text = await response.text()
        self.assertNotIn(secret, text + json.dumps(data) + json.dumps(list(self.app[server.ROOM].logs)))
        status, _ = await self.post("/api/platform/credentials", {"provider": "vidu", "remove": True})
        self.assertEqual(status, 200)
        self.assertIsNone(self.app[server.ROOM].vidu)
        self.assertFalse(path.exists())

    async def test_cross_site_credential_write_and_invalid_values_are_rejected(self):
        status, _ = await self.post("/api/platform/credentials", {"provider": "vidu", "value": "vda_test"}, {"Origin": "https://unrelated.example"})
        self.assertEqual(status, 403)
        self.assertFalse((self.root / ".secrets/vidu.key").exists())
        for body in ({"provider": "../escape", "value": "abc"}, {"provider": "vidu", "value": "wrong"}):
            status, _ = await self.post("/api/platform/credentials", body)
            self.assertEqual(status, 400)

    async def test_preferences_survive_fresh_application(self):
        await self.post("/api/platform/preferences", {"daily_budget_credits": 2222, "auto_recover": True})
        await self.post("/api/settings", {"interval_seconds": 25, "policy": "fifo"})
        await self.post("/api/safety", {"budget_credits": 500, "max_session_minutes": 5})
        await self.runner.cleanup()
        await self.start_app()
        room = self.app[server.ROOM]
        self.assertEqual(room.cfg["safety"]["daily_budget_credits"], 2222)
        self.assertEqual(room.cfg["safety"]["budget_credits"], 500)
        self.assertTrue(room.cfg["schedule"]["auto_recover"])
        self.assertEqual(room.cfg["schedule"]["interval_seconds"], 25)
        self.assertEqual(room.queue.policy, "fifo")

    async def test_demo_works_without_key_and_blocks_credential_change_while_running(self):
        import asyncio
        status, data = await self.post("/api/platform/demo", {})
        self.assertEqual((status, data["accepted"], data["filtered"]), (200, 3, 1))
        await asyncio.sleep(0.15)
        room = self.app[server.ROOM]
        self.assertIsNone(room.vidu)
        self.assertEqual(room.mode, "dry_run")
        self.assertIn("演示反馈", room.subtitle)
        status, _ = await self.post("/api/platform/credentials", {"provider": "vidu", "value": "vda_test"})
        self.assertEqual(status, 409)
        await self.post("/api/session/stop", {})
        records = self.app[server.PLATFORM].status()["usage"]
        self.assertEqual(records["today_credits"], 0)
        self.assertEqual(records["sessions"][0]["status"], "ended")
        status, data = await self.post("/api/platform/demo", {})
        self.assertEqual((status, data["accepted"], data["filtered"]), (200, 3, 1))

    async def test_graceful_shutdown_finishes_active_demo_before_http_cleanup(self):
        await self.post("/api/platform/demo", {})
        await self.runner.cleanup()
        self.assertEqual(self.app[server.ROOM].status, "ended")
        self.assertEqual(self.app[server.PLATFORM].status()["usage"]["sessions"][0]["status"], "ended")

    async def test_daily_budget_prevents_another_paid_session_and_gifts_are_not_prioritized(self):
        runtime = self.app[server.PLATFORM]
        runtime.ledger.start("previous", "主播", 1.5)
        runtime.ledger.update("previous", 4000, close=True)
        with self.assertRaisesRegex(RuntimeError, "今日预算"):
            self.app[server.ROOM]._check_daily_budget()
        status, _ = await self.post("/api/settings", {"policy": "gift_first_fifo"})
        self.assertEqual(status, 400)
        _, result = await self.post("/api/danmaku", {"user": "viewer", "text": "你好", "gift": 100})
        self.assertEqual(result["command"]["gift"], 0)
        _, result = await self.post("/api/danmaku", {"user": "viewer", "text": "跳个舞"})
        self.assertEqual(result["command"]["kind"], "chat")


if __name__ == "__main__":
    unittest.main()
