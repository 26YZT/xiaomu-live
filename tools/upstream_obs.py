"""OBS adapter for the original stage. Never starts recording or streaming."""
import asyncio
import json
import aiohttp
from obs_control import OBS, prepare_scene

SCENE = "原版小沐直播"
INPUT = "原版小沐画面"

async def main():
    async with aiohttp.ClientSession() as http:
        obs = await OBS.connect(http)
        try:
            collection = await obs.call("GetSceneCollectionList")
            profile = await obs.call("GetProfileList")
            if collection["currentSceneCollectionName"] != "数字人私下试播" or profile["currentProfileName"] != "数字人私下试播":
                raise SystemExit("请先在 OBS 选择“数字人私下试播”配置和场景集合；未修改其他用途的配置。")
            add = await prepare_scene(obs, SCENE, (1296, 1080))
            item = await add(INPUT, "browser_source", {
                "url": "http://127.0.0.1:8795/stage?obs=1", "width": 1296, "height": 1080,
                "reroute_audio": True, "shutdown": True, "restart_when_active": False,
                "css": "body { margin: 0; overflow: hidden; }",
            })
            await obs.call("SetSceneItemTransform", {"sceneName": SCENE, "sceneItemId": item,
                "sceneItemTransform": {"positionX": 0, "positionY": 0, "scaleX": 1, "scaleY": 1}})
            await obs.call("SetInputMute", {"inputName": INPUT, "inputMuted": False})
            await obs.call("SetInputVolume", {"inputName": INPUT, "inputVolumeMul": 1})
            await obs.call("SetInputAudioMonitorType", {"inputName": INPUT, "monitorType": "OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT"})
            print(json.dumps({"scene": SCENE, "url": "http://127.0.0.1:8795/stage?obs=1",
                "streaming": (await obs.call("GetStreamStatus"))["outputActive"],
                "recording": (await obs.call("GetRecordStatus"))["outputActive"]}, ensure_ascii=False))
        finally:
            await obs.ws.close()

if __name__ == "__main__":
    asyncio.run(main())
