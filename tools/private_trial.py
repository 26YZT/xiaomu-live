"""One bounded, paid Vidu trial recorded locally in OBS. Requires explicit --run.

No public streaming, outfit changes, retries, or purchases. Evidence is saved under
recordings/private-tests/. Audio timing uses the OBS meter; it is not speech recognition.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime
import json
import math
from pathlib import Path
import time
from zoneinfo import ZoneInfo

import aiohttp

from obs_control import OBS, PRIVATE_SCENE, SERVER

ROOT = Path(__file__).resolve().parents[1]


async def run():
    folder = ROOT / "recordings/private-tests" / datetime.now(ZoneInfo("Asia/Shanghai")).strftime("vidu-%Y%m%d-%H%M%S")
    folder.mkdir(parents=True, exist_ok=False)
    report = {"kind": "real_vidu_private_trial", "started_at": time.time(), "commands": [], "recording": None}
    state, tasks = {}, []
    last_loud = 0.0
    recording = started = False
    original_idle = None
    obs = meter = None

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as http:
        async def api(path, body=None, timeout=70):
            async with http.request("GET" if body is None else "POST", SERVER + path, json=body,
                                    timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                value = await response.json()
                if response.status >= 400:
                    raise RuntimeError(f"{path}: {response.status} {value.get('error', value)}")
                return value

        async def events():
            nonlocal state
            async with http.get(SERVER + "/api/events", timeout=aiohttp.ClientTimeout(total=None)) as response:
                kind = ""
                with (folder / "events.jsonl").open("w") as output:
                    async for raw in response.content:
                        line = raw.decode().strip()
                        if line.startswith("event:"):
                            kind = line[6:].strip()
                        elif line.startswith("data:"):
                            data, now = json.loads(line[5:]), time.time()
                            output.write(json.dumps({"t": now, "event": kind, "data": data}, ensure_ascii=False) + "\n")
                            output.flush()
                            if kind == "state":
                                state = data
                                if data.get("rtc_ready"):
                                    report.setdefault("ready_at", now)
                                if data.get("stage_joined"):
                                    report.setdefault("rtc_joined_at", now)
                            elif kind == "log" and data.get("message") == "舞台页收到数字人画面":
                                report.setdefault("first_video_signal_at", now)
                            elif kind == "subtitle":
                                report.setdefault("transcripts", []).append({"t": now, "text": data.get("text")})
                                for command in reversed(report["commands"]):
                                    if command["kind"] in ("text_tts", "text_msg") and "subtitle_at" not in command:
                                        command["subtitle_at"] = now
                                        command["subtitle"] = data.get("text")
                                        break

        async def audio_events():
            nonlocal last_loud
            with (folder / "audio-levels.jsonl").open("w") as output:
                async for message in meter.ws:
                    if message.type != aiohttp.WSMsgType.TEXT:
                        continue
                    event = json.loads(message.data).get("d", {})
                    if event.get("eventType") != "InputVolumeMeters":
                        continue
                    for item in event["eventData"]["inputs"]:
                        if item["inputName"] != "数字人画面":
                            continue
                        linear = max((channel[1] for channel in item["inputLevelsMul"]), default=0)
                        db = 20 * math.log10(linear) if linear > 0 else -200
                        now = time.time()
                        output.write(json.dumps({"t": now, "db": round(db, 2)}) + "\n")
                        report["audio_peak_db"] = max(report.get("audio_peak_db", -200), db)
                        if db > -55:
                            last_loud = now
                            report.setdefault("first_audio_at", now)
                            for command in reversed(report["commands"]):
                                if command["kind"] in ("text_tts", "text_msg") and "audio_at" not in command:
                                    command["audio_at"] = now
                                    break

        try:
            state = await api("/api/state")
            if state.get("status") not in ("idle", "ended", "error"):
                raise RuntimeError("已有数字人会话，未开始试播")
            if not state.get("has_api_key") or state.get("output_mode") != "obs":
                raise RuntimeError("需要已保存的 Vidu Key 和 OBS 输出模式")
            if state.get("auto_recover") or state.get("max_session_minutes", 999) > 2 or state.get("budget_credits", 99999) > 200:
                raise RuntimeError("先设置最多 2 分钟 / 200 积分，并关闭自动恢复")
            await api("/api/platform/verify-vidu", {})
            obs = await OBS.connect(http)
            if (await obs.call("GetSceneCollectionList"))["currentSceneCollectionName"] != PRIVATE_SCENE:
                raise RuntimeError("OBS 未选择专用私下试播场景集合")
            if (await obs.call("GetCurrentProgramScene"))["currentProgramSceneName"] != PRIVATE_SCENE:
                raise RuntimeError("OBS 未选择数字人私下试播场景")
            for req in ("GetStreamStatus", "GetRecordStatus", "GetVirtualCamStatus"):
                if (await obs.call(req)).get("outputActive"):
                    raise RuntimeError("OBS 已有输出，未开始新的试播")
            if any((await obs.call("GetSpecialInputs")).values()):
                raise RuntimeError("OBS 仍有全局音频输入，先关闭麦克风/桌面声混入")
            original_idle = state["idle_prompt_after_seconds"]
            await api("/api/settings", {"idle_prompt_after_seconds": 0})
            meter = await OBS.connect(http, events=1 << 16)
            tasks = [asyncio.create_task(events()), asyncio.create_task(audio_events())]
            await obs.call("StartRecord")
            recording = True
            for _ in range(60):
                if (await obs.call("GetRecordStatus")).get("outputDuration", 0) > 500:
                    break
                await asyncio.sleep(.25)
            else:
                raise RuntimeError("OBS 录制计时没有启动")
            report["create_requested_at"] = time.time()
            started = True
            print("OBS recording; creating one Vidu session", flush=True)
            state = await api("/api/session/start", {"dry_run": False, "model": "vidu-s2", "profile": "xiaoshen"}, timeout=120)
            report["live_id"] = state.get("live_id")
            shot_at, announced = 0.0, 0.0
            # The default qwen_omni voice does not support type-100 direct TTS.
            fixtures = [(12, "text_msg", "请读出这句话：这是一次本地试播，现在测试声音和口型，一、二、三、四、五。"),
                        (38, "text_msg", "请用一句话介绍你自己，并推荐一件今天可以做的小事。"),
                        (68, "text_action", "微笑着向镜头缓慢挥挥右手")]
            while time.time() - report["create_requested_at"] < 180:
                now = time.time()
                state = await api("/api/state", timeout=5)
                report["live_id"] = state.get("live_id") or report.get("live_id")
                if state["status"] in ("ended", "error", "idle"):
                    report["early_end"] = state.get("end_reason")
                    break
                ready = report.get("ready_at")
                if ready and now - ready >= 110:
                    break
                if now - announced > 15:
                    print(json.dumps({"status": state["status"], "elapsed": state.get("elapsed"), "audio_peak_db": report.get("audio_peak_db")}), flush=True)
                    announced = now
                if state.get("stage_video_received") and now - shot_at > 15:
                    shot = await obs.call("GetSourceScreenshot", {"sourceName": PRIVATE_SCENE, "imageFormat": "png", "imageWidth": 648})
                    (folder / f"frame-{int(now)}.png").write_bytes(base64.b64decode(shot["imageData"].split(",", 1)[1]))
                    shot_at = now
                if ready and state.get("stage_video_received") and fixtures:
                    offset, kind, content = fixtures[0]
                    previous = report["commands"][-1]["sent_at"] if report["commands"] else 0
                    if now - ready >= offset and now - last_loud > 2 and now - previous > 22:
                        command = {"kind": kind, "content": content, "sent_at": now, "quiet_before_seconds": now - last_loud if last_loud else None}
                        report["commands"].append(command)
                        await api("/api/manual", {"kind": kind, "content": content}, timeout=10)
                        fixtures.pop(0)
                        print("Sent " + kind, flush=True)
                await asyncio.sleep(.5)
        except Exception as error:
            report["error"] = str(error)
            print(str(error), flush=True)
        finally:
            if started:
                try:
                    report["final_state"] = await api("/api/session/stop", {}, timeout=120)
                except Exception as error:
                    report["stop_error"] = str(error)
            if recording:
                try:
                    report["recording"] = await obs.call("StopRecord")
                    report["obs_stats"] = await obs.call("GetStats")
                except Exception as error:
                    report["obs_stop_error"] = str(error)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if original_idle is not None:
                try:
                    await api("/api/settings", {"idle_prompt_after_seconds": original_idle})
                except Exception as error:
                    report["restore_settings_error"] = str(error)
            for connection in (obs, meter):
                if connection:
                    await connection.ws.close()
            report["finished_at"] = time.time()
            for command in report["commands"]:
                for key in ("subtitle", "audio"):
                    if key + "_at" in command:
                        command[key + "_latency_seconds"] = round(command[key + "_at"] - command["sent_at"], 3)
            (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            print("Report: " + str(folder / "report.json"), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="启动一次可能计费的真实数字人会话")
    if not parser.parse_args().run:
        parser.error("真实试播可能计费；只有显式传入 --run 才启动")
    asyncio.run(run())
