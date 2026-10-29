#!/bin/zsh
set -e
cd -- "$(dirname -- "$0")"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import aiohttp, certifi, PIL, numpy' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.lock.txt
fi
if [[ ! -s static/vendor/aliyun-rtc-sdk-7.1.9.js ]]; then
  ./tools/fetch_vendor.sh
fi
.venv/bin/python tools/launch.py start
