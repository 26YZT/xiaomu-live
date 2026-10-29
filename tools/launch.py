"""Start/stop this checkout's local service without touching another app's port."""
from __future__ import annotations
import argparse
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
from urllib.parse import urlsplit
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:28900"


def status():
    try:
        with urllib.request.urlopen(URL + "/api/platform", timeout=1) as response:
            data = json.load(response)
        return data if isinstance(data, dict) and data.get("app_id") == "local-avatar-studio" and data.get("root") == str(ROOT.resolve()) else {"other": True}
    except urllib.error.HTTPError:
        return {"other": True}
    except ValueError:
        return {"other": True}
    except OSError:
        address = urlsplit(URL)
        try:
            with socket.create_connection((address.hostname, address.port), timeout=0.3):
                return {"unresponsive": True}
        except OSError:
            pass
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "status"], nargs="?", default="start")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    current = status()
    if current and current.get("other"):
        raise SystemExit("28900 端口已由其他服务使用。请先检查该服务，本工具不会关闭它。")
    if current and current.get("unresponsive"):
        raise SystemExit("端口仍被占用，但服务暂未响应；无法确认已停止。请查看 logs/server.log。")
    if args.action == "status":
        print("运行中：" + URL + "/workbench" if current else "平台未运行")
        return
    if args.action == "stop":
        if current:
            os.kill(int(current["pid"]), signal.SIGTERM)
            for _ in range(100):
                process_alive = True
                try:
                    os.kill(int(current["pid"]), 0)
                except ProcessLookupError:
                    process_alive = False
                if status() is None and not process_alive:
                    print("平台已停止。")
                    return
                time.sleep(0.2)
            raise SystemExit("服务正在清理会话，请稍后再次检查。")
        print("平台已经停止。")
        return
    if not current:
        from setup_local import setup
        setup()
        env = os.environ.copy()
        # Desktop launchers may not inherit the user's interactive shell PATH.
        env["PATH"] = ":".join([str(Path.home() / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin", env.get("PATH", "")])
        with (ROOT / "logs/server.log").open("ab") as log:
            child = subprocess.Popen([sys.executable, "-u", str(ROOT / "server.py")], cwd=ROOT,
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, env=env)
        for _ in range(100):
            if child.poll() is not None:
                raise SystemExit("启动失败，请查看 logs/server.log。")
            current = status()
            if current and not current.get("other"):
                break
            time.sleep(0.2)
        else:
            raise SystemExit("启动检查超时，请查看 logs/server.log。")
    print("平台已运行：" + URL + "/workbench")
    if not args.no_browser:
        webbrowser.open(URL + "/workbench")


if __name__ == "__main__":
    main()
