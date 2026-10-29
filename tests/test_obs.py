"""Prevent scene configuration from interrupting an existing OBS recording."""
import unittest
from tools.obs_control import prepare_scene


class OBSGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_recording_blocks_scene_and_canvas_changes(self):
        class RecordingOBS:
            def __init__(self):
                self.requests = []

            async def call(self, request, data=None):
                self.requests.append(request)
                if request == "GetStreamStatus":
                    return {"outputActive": False}
                if request == "GetRecordStatus":
                    return {"outputActive": True}
                raise AssertionError(f"Configuration must not continue: {request}")

        obs = RecordingOBS()
        with self.assertRaisesRegex(SystemExit, "正在录制"):
            await prepare_scene(obs, "test", (1296, 1080))
        self.assertNotIn("SetVideoSettings", obs.requests)
        self.assertNotIn("RemoveSceneItem", obs.requests)


if __name__ == "__main__":
    unittest.main()
