#!/usr/bin/env bash
set -euo pipefail
python3 -m unittest discover -s tests -v
python3 -m compileall -q zudo_agent tests
python3 scripts/check-skills.py
if command -v node >/dev/null 2>&1; then
  node --check zudo_agent/web/app.js
fi
