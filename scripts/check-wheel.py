#!/usr/bin/env python3
"""Check that a built wheel contains the local UI and its required notices."""

from __future__ import annotations

import sys
from pathlib import Path
from zipfile import ZipFile


REQUIRED = {
    "zudo_agent/web/index.html",
    "zudo_agent/web/app.js",
    "zudo_agent/web/commands.js",
    "zudo_agent/web/console.html",
    "zudo_agent/web/console.js",
    "zudo_agent/web/preferences.js",
    "zudo_agent/web/tokens.css",
    "zudo_agent/web/dashboard.css",
    "zudo_agent/web/style.css",
    "zudo_agent/web/detail.css",
    "zudo_agent/web/editor.js",
    "zudo_agent/web/THIRD_PARTY_NOTICES.txt",
    "zudo_agent/web/favicon.svg",
}


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: check-wheel.py PATH_TO_WHEEL.whl")
    wheel = Path(sys.argv[1])
    with ZipFile(wheel) as archive:
        present = set(archive.namelist())
    missing = sorted(REQUIRED - present)
    if missing:
        raise SystemExit(f"{wheel.name} is missing required dashboard assets: {', '.join(missing)}")
    print(f"Wheel assets verified: {wheel.name} includes {len(REQUIRED)} local UI files and notices.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
