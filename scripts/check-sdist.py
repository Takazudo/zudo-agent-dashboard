#!/usr/bin/env python3
"""Check that an sdist contains editor source, its lockfile, and bundled notices."""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath
from tarfile import open as open_tar


REQUIRED = {
    "editor-src/composer.js",
    "package.json",
    "package-lock.json",
    "zudo_agent/web/editor.js",
    "zudo_agent/web/commands.js",
    "zudo_agent/web/THIRD_PARTY_NOTICES.txt",
}


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: check-sdist.py PATH_TO_SDIST.tar.gz")
    archive_path = Path(sys.argv[1])
    with open_tar(archive_path, "r:gz") as archive:
        present = {
            "/".join(PurePosixPath(member.name).parts[1:])
            for member in archive.getmembers()
            if len(PurePosixPath(member.name).parts) > 1
        }
    missing = sorted(REQUIRED - present)
    if missing:
        raise SystemExit(f"{archive_path.name} is missing required editor inputs/assets: {', '.join(missing)}")
    print(f"Source archive verified: {archive_path.name} includes editor source, lockfile, bundle, and notices.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
