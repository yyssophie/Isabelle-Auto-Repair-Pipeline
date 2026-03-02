"""
Session selection helpers.

ALL_SOURCE_SESSIONS is populated automatically by invoking a shell command
that lists every immediate subdirectory under SOURCE_AFP. You can edit the
SESSIONS_TO_REPAIR list below to control which sessions the drivers process.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent))
from setup import SOURCE_AFP, OUTPUT_ROOT


def _list_directories(root: Path) -> List[str]:
    """
    Use the `ls` command to list direct subdirectories under SOURCE_AFP.
    Falls back to pathlib scanning if the command fails (e.g. non-Unix host).
    """
    cmd = ["ls", "-1"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        raw_entries = [line.strip() for line in proc.stdout.splitlines()]
    except Exception as exc:
        print(f"[session_list] WARNING: `{' '.join(cmd)}` failed ({exc}), falling back to pathlib.")
        raw_entries = [entry.name for entry in root.iterdir()]

    result: List[str] = []
    for name in raw_entries:
        if not name:
            continue
        path = root / name
        if path.is_dir():
            result.append(name)
    return sorted(result)


SOURCE_AFP_PATH = Path(SOURCE_AFP)
if not SOURCE_AFP_PATH.is_dir():
    raise FileNotFoundError(f"SOURCE_AFP path does not exist: {SOURCE_AFP}")

# Automatically populated directory listing (visible for inspection).
ALL_SOURCE_SESSIONS: List[str] = _list_directories(SOURCE_AFP_PATH)

OUTPUT_ROOT_PATH = Path(OUTPUT_ROOT)
if not OUTPUT_ROOT_PATH.is_dir():
    raise FileNotFoundError(f"OUTPUT_ROOT path does not exist: {OUTPUT_ROOT}")

SESSIONS_TO_REPAIR: List[str] = _list_directories(OUTPUT_ROOT_PATH)
