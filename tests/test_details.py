"""Second source-level audit: isolated regressions, never calls the real Vidu account."""
import asyncio
import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_e2e, test_workbench
from live.workbench import WorkbenchError
from live.platform import UsageLedger
from live.vidu import ViduError


class SessionDetails(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_e2e.EndToEnd.asyncSetUp
    asyncTearDown = test_e2e.EndToEnd.asyncTearDown
    post = test_e2e.EndToEnd.post
    wait_for = test_e2e.EndToEnd.wait_for
    frames = test_e2e.EndToEnd.frames

    async def test_stop_clears_current_subtitle_immediately(self):
        await self.post('/api/session/start', {'dry_run':True})
        await self.post('/api/manual', {'kind':'text_msg','content':'字幕清理验收'})
        self.assertTrue(self.room.snapshot()['subtitle'])
        await self.post('/api/session/stop', {})
        self.assertEqual(self.room.snapshot()['subtitle'], '')
        self.assertIsNone(self.room.snapshot()['current'])

    async def test_direct_tts_is_rejected_for_qwen_but_supported_for_doubao(self):
        await self.ready()
        code, data = await self.post('/api/manual', {'kind':'text_tts','content':'测试'})
        self.assertEqual(code, 400)
        self.assertIn('豆包', data['error'])
        self.assertEqual(self.frames(100), [])
        self.room.avatar_cfg['voice_model'] = {'provider':'doubao'}
        code, _ = await self.post('/api/manual', {'kind':'text_tts','content':'测试'})
        self.assertEqual(code, 200)
        self.assertTrue(await self.wait_for(lambda: bool(self.frames(100))))

    async def test_server_cleanup_cancels_media_workers_and_keeps_provider_task_for_resume(self):
        import json
        import server
        wb = self.runner.app[server.WB]
        wb.poll_seconds['image'] = .001
        entered = asyncio.Event()
        async def blocked_poll(*args, **kwargs):
            entered.set(); await asyncio.Event().wait()
        job = wb._new_job('image', 'test', 'mock')
        job['task_id'] = 'existing-provider-task'
        with patch.object(wb, '_evolink', side_effect=blocked_poll):
            wb._spawn(wb._run_evolink(job, None))
            await asyncio.wait_for(entered.wait(), 1)
            pending = list(wb._tasks)
            try:
                await self.runner.cleanup()
                self.assertTrue(all(task.done() for task in pending), 'provider workers survived HTTP session cleanup')
                saved = json.loads(wb.jobs_file.read_text())['jobs'][0]
                self.assertEqual(saved['task_id'], 'existing-provider-task')
                self.assertEqual(saved['status'], 'running')
            finally:
                for task in pending: task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

    async def ready(self):
        await self.post('/api/session/start', {})
        self.assertTrue(await self.wait_for(lambda: self.room.status == 'waiting_stage'))
        self.room.claim_stage('current-page', 'preview')

    async def test_old_stage_events_cannot_change_current_session(self):
        await self.ready()
        code, _ = await self.post('/api/stage', {'event':'joined', 'client_id':'current-page', 'live_id':'old-live'})
        self.assertEqual(code, 409)
        self.assertFalse(self.room.stage_joined)
        code, _ = await self.post('/api/stage', {'event':'joined', 'client_id':'other-page', 'live_id':self.room.live['id']})
        self.assertEqual(code, 409)

    async def test_old_page_release_does_not_release_same_page_new_session(self):
        await self.ready()
        await self.post('/api/stage/release', {'client_id':'current-page', 'live_id':'old-live'})
        self.assertEqual(self.room.stage_owner, 'current-page')
        _, data = await self.post('/api/stage/heartbeat', {'client_id':'current-page','live_id':'old-live'})
        self.assertFalse(data['owner'])

    async def test_new_start_archives_commands_from_stopped_show(self):
        await self.post('/api/session/start', {'dry_run':True})
        await self.post('/api/danmaku', {'user':'old-viewer','text':'讲个笑话'})
        old = self.room.queue.waiting()[0]
        await self.post('/api/session/stop', {})
        await self.post('/api/session/start', {'dry_run':True})
        self.assertEqual(self.room.queue.waiting(), [])
        self.assertIn(old, self.room.history)
        self.assertEqual(old.status, 'expired')

    async def test_legacy_settings_rejects_string_auto_recover(self):
        before = copy.deepcopy(self.room.cfg['schedule'])
        code, _ = await self.post('/api/settings', {'auto_recover':'false','interval_seconds':77})
        self.assertEqual(code, 400)
        self.assertEqual(self.room.cfg['schedule'], before)

    async def test_slow_billing_query_cannot_delay_budget_hangup(self):
        await self.ready()
        for task in self.room._tasks:
            if task.get_coro().__name__ == '_watchdog':
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        entered, release = asyncio.Event(), asyncio.Event()
        original_bill, original_sleep = self.room._refresh_billing, asyncio.sleep
        async def slow_bill():
            entered.set()
            await release.wait()
            return await original_bill()
        async def fast_guard(seconds):
            await original_sleep(.01 if seconds in (1, 15) else seconds)
        self.room._refresh_billing = slow_bill
        with patch('live.session.asyncio.sleep', side_effect=fast_guard):
            task = asyncio.create_task(self.room._watchdog())
            try:
                await asyncio.wait_for(entered.wait(), 1)
                self.room.cfg['safety']['budget_credits'] = 1
                self.room.ready_at = time.time() - 100
                self.assertTrue(await self.wait_for(lambda: bool(self.frames(5)), timeout=.4), 'budget hangup waited for billing HTTP')
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_delayed_old_billing_does_not_overwrite_new_live(self):
        await self.ready()
        original = self.room.live
        entered, release = asyncio.Event(), asyncio.Event()
        async def old_bill(live_id):
            entered.set(); await release.wait()
            return {'live': {'id':live_id,'credits_cost':3,'billed_seconds':2,'status':'on_live'}}
        with patch.object(self.room.vidu, 'get_live', side_effect=old_bill):
            task = asyncio.create_task(self.room._refresh_billing())
            await entered.wait()
            self.room.live = {'id':'new-live'}
            self.room.billing['credits_cost'] = 42
            release.set(); await task
            try:
                self.assertEqual(self.room.billing['credits_cost'], 42)
            finally:
                self.room.live = original


class ProfileDetails(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_workbench.WorkbenchAPI.asyncSetUp
    asyncTearDown = test_workbench.WorkbenchAPI.asyncTearDown

    async def test_invalid_edit_does_not_partially_change_profile_or_persona(self):
        key = next(iter(self.room.cfg['avatars']))
        before = copy.deepcopy(self.room.cfg['avatars'][key])
        with self.assertRaises(WorkbenchError):
            self.wb.update_profile(key, {'name':'不应保存的名字','voice':'bad!voice'})
        self.assertEqual(self.room.cfg['avatars'][key], before)

    async def test_late_validation_failure_does_not_write_persona(self):
        key = next(iter(self.room.cfg['avatars']))
        target = self.wb.root / 'personas' / f'{key}.md'
        before = target.read_text() if target.exists() else None
        with self.assertRaises(WorkbenchError):
            self.wb.update_profile(key, {'persona_text':'这一段人设文字虽然有效，但是后面的动作参数无效，不能保存任何部分。', 'idle_motions':42})
        self.assertEqual(target.read_text() if target.exists() else None, before)

    async def test_invalid_new_profile_leaves_no_partial_character(self):
        source = self.wb.gallery.items[0]['id']
        with self.assertRaises(WorkbenchError):
            self.wb.update_profile('new_invalid', {'name':'新角色','base_gallery_id':source,'persona_text':'短'})
        self.assertNotIn('new_invalid', self.room.cfg['avatars'])
        self.assertFalse((self.wb.root / 'assets/avatars/new_invalid').exists())


class BillingDetails(unittest.TestCase):
    def test_overlap_cost_counts_both_live_ids_but_not_previous_shows(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('live.platform.time.time', return_value=1000):
                ledger = UsageLedger(Path(folder))
                ledger.start('prior', 'same host', 1.5)
                ledger.start('old-outfit', 'same host', 1.5)
            with patch('live.platform.time.time', return_value=1010):
                ledger.start('new-outfit', 'same host', 1.5)
            with patch('live.platform.time.time', return_value=1020):
                self.assertEqual(ledger.total_credits({'old-outfit','new-outfit'}),45)

    def test_credit_failure_explains_next_step_and_keeps_trace_for_diagnostics(self):
        error = ViduError(400, '{"reason":"CreditInsufficient","metadata":{"trace_id":"trace-1"}}')
        self.assertIn('积分不足', str(error))
        self.assertIn('S2', str(error))
        self.assertEqual(error.trace_id, 'trace-1')
