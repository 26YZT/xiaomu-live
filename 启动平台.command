#!/bin/zsh
set -e
cd -- "$(dirname -- "$0")"
.venv/bin/python tools/upstream_local.py start
