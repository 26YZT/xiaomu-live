#!/bin/zsh
set -e
cd -- "$(dirname -- "$0")"
exec upstream/cyber-xiaoya-live/.venv/bin/python upstream/cyber-xiaoya-danmaku/reader.py --no-tray "$@"
