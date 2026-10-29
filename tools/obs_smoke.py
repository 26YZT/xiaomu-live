"""Record an eight-second synthetic browser A/V check. No Vidu calls or public stream.

Run after `tools/obs_control.py setup private`. The temporary source is removed and
the original scene restored even when the check fails. Results are explicitly synthetic.
"""
from __future__ import annotations

import asyncio
import base64
import json
import math
import time
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web

from obs_control import OBS, PRIVATE_SCENE, SERVER, current_scene

ROOT = Path(__file__).resolve().parents[1]
HTML = """<!doctype html><html><meta charset="utf-8"><style>
body{margin:0;background:#102d42;color:white;font:40px system-ui}canvas{display:block}
</style><canvas width="1296" height="1080"></canvas><script>
const c=document.querySelector('canvas'),x=c.getContext('2d'),start=performance.now();
function draw(){let t=(performance.now()-start)/1000;x.fillStyle='#102d42';x.fillRect(0,0,c.width,c.height);
x.fillStyle='#fff';x.font='48px system-ui';x.fillText('OBS LOCAL A/V CHECK',90,240);
x.font='32px system-ui';x.fillText('Synthetic picture + 440 Hz tone | No Vidu API',90,320);
x.fillText(t.toFixed(2)+' seconds',90,400);x.fillStyle='#43ddac';x.fillRect(90+(t*170)%950,520,110,220);
requestAnimationFrame(draw)}draw();
const a=new AudioContext(),o=a.createOscillator(),g=a.createGain();o.frequency.value=440;g.gain.value=0.04;
o.connect(g);g.connect(a.destination);o.start();a.resume().then(()=>fetch('/ready',{
method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({audio_state:a.state})}));
</script></html>"""


async def main():
    ready = asyncio.Event()
    result = {"kind": "synthetic_obs_browser_check", "vidu_called": False, "api_cost_yuan": 0}

    async def index(_):
        return web.Response(text=HTML, content_type="text/html")

    async def mark_ready(request):
        result["browser"] = await request.json()
        ready.set()
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_post("/ready", mark_ready)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    tag = "OBS 合成链路自检 " + uuid.uuid4().hex[:8]
    source = tag + " 浏览器"
    folder = ROOT / "recordings/private-tests"
    folder.mkdir(parents=True, exist_ok=True)
    report = folder / (time.strftime("obs-smoke-%Y%m%d-%H%M%S") + ".json")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as http:
            async with http.get(SERVER + "/api/state") as response:
                response.raise_for_status()
                state = await response.json()
                if state.get("status") not in ("idle", "ended", "error"):
                    raise RuntimeError("请先停止数字人，再运行合成自检；不会切换正在使用的画面")
            obs = await OBS.connect(http, events=1 << 16)
            previous = await current_scene(obs)
            created_scene = created_source = recording = False
            try:
                profile = await obs.call("GetProfileList")
                collection = await obs.call("GetSceneCollectionList")
                if profile["currentProfileName"] != PRIVATE_SCENE or collection["currentSceneCollectionName"] != PRIVATE_SCENE:
                    raise RuntimeError("先运行 tools/obs_control.py setup private")
                for req in ("GetStreamStatus", "GetRecordStatus", "GetVirtualCamStatus"):
                    if (await obs.call(req)).get("outputActive"):
                        raise RuntimeError("已有 OBS 输出在运行，未开始自检")
                if any((await obs.call("GetSpecialInputs")).values()):
                    raise RuntimeError("私下试播集合仍有全局音频设备，请先关闭")
                await obs.call("CreateScene", {"sceneName": tag})
                created_scene = True
                await obs.call("CreateInput", {"sceneName": tag, "inputName": source, "inputKind": "browser_source",
                    "inputSettings": {"url": f"http://127.0.0.1:{port}/", "width": 1296, "height": 1080,
                                      "reroute_audio": True, "shutdown": True}, "sceneItemEnabled": True})
                created_source = True
                await obs.call("SetInputAudioMonitorType", {"inputName": source, "monitorType": "OBS_MONITORING_TYPE_NONE"})
                await obs.call("SetCurrentProgramScene", {"sceneName": tag})
                await asyncio.wait_for(ready.wait(), timeout=20)
                if result.get("browser", {}).get("audio_state") != "running":
                    raise RuntimeError("浏览器声音未启动")
                before = await obs.call("GetStats")
                await obs.call("StartRecord")
                recording = True
                # StartRecord acknowledges the request before the encoder is necessarily ready.
                # Measure actual output duration so startup latency cannot shorten the sample.
                end, peak = asyncio.get_running_loop().time() + 30, -200.0
                while asyncio.get_running_loop().time() < end:
                    try:
                        msg = await asyncio.wait_for(obs.ws.receive_json(), timeout=1)
                    except asyncio.TimeoutError:
                        continue
                    event = msg.get("d", {})
                    if event.get("eventType") == "InputVolumeMeters":
                        for inp in event["eventData"]["inputs"]:
                            if inp["inputName"] == source:
                                for channel in inp["inputLevelsMul"]:
                                    if channel[1] > 0:
                                        peak = max(peak, 20 * math.log10(channel[1]))
                    recorded = await obs.call("GetRecordStatus")
                    if recorded.get("outputDuration", 0) >= 8000:
                        result["recorded_duration_ms"] = recorded["outputDuration"]
                        break
                else:
                    raise RuntimeError("30 秒内没有录满 8 秒，请检查 OBS 编码器")
                result["audio_peak_db"] = round(peak, 2)
                screenshot = await obs.call("GetSourceScreenshot", {"sourceName": source, "imageFormat": "png", "imageWidth": 648})
                report.with_suffix(".png").write_bytes(base64.b64decode(screenshot["imageData"].split(",", 1)[1]))
                result["recording"] = await obs.call("StopRecord")
                recording = False
                after = await obs.call("GetStats")
                # Rendering counters span the app lifetime; output counters reset for each recording.
                result["frames"] = {k: after.get(k, 0) - before.get(k, 0) for k in
                                    ("renderTotalFrames", "renderSkippedFrames")}
                result["frames"].update({k: after.get(k, 0) for k in ("outputTotalFrames", "outputSkippedFrames")})
                result["passed_browser_audio"] = peak > -60
            finally:
                if recording:
                    result["recording"] = await obs.call("StopRecord")
                if created_scene:
                    await obs.call("SetCurrentProgramScene", {"sceneName": previous})
                if created_source:
                    await obs.call("RemoveInput", {"inputName": source})
                if created_scene:
                    await obs.call("RemoveScene", {"sceneName": tag})
                await obs.ws.close()
    except Exception as error:
        result["error"] = str(error)
        raise
    finally:
        await runner.cleanup()
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(report)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
