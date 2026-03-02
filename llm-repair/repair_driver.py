from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, Iterator, List, Tuple
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_repairer_multiline import *
from session_lister import ALL_SOURCE_SESSIONS, SESSIONS_TO_REPAIR
from setup import SOURCE_AFP, OUTPUT_ROOT


def session_root_name(session_path: str) -> str:
    normalized = session_path.replace("\\", "/").lstrip("/")
    if not normalized:
        return session_path
    return normalized.split("/", 1)[0]


def iter_theory_targets(session_name: str) -> Iterator[Tuple[str, str]]:
    """
    Yield (session_path, theory_file) tuples by scanning OUTPUT_ROOT/{root_session}/
    for JSON files belonging to session_name (or any sub-session of it).

    JSON filenames follow the pattern:
      {session_with_slashes_as_@}@{theory_stem}.json
    e.g. CakeML@Tests@Compiler_Test.json -> ("CakeML/Tests", "Compiler_Test.thy")
    """
    root_session = session_name.split("/")[0]
    session_dir = Path(OUTPUT_ROOT) / root_session
    session_prefix = session_name.replace("/", "@")

    for entry in sorted(session_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_file() or entry.suffix.lower() != ".json":
            continue
        stem = entry.stem  # e.g. "CakeML@Tests@Compiler_Test"
        at_idx = stem.rfind("@") # find the last occurrences of @
        if at_idx == -1:
            continue
        file_session = stem[:at_idx]   # e.g. "CakeML@Tests"
        theory_stem  = stem[at_idx + 1:]  # e.g. "Compiler_Test"

        # Only include files whose session is session_name or a sub-session of it.
        if file_session != session_prefix and not file_session.startswith(session_prefix + "@"):
            continue

        yield file_session.replace("@", "/"), theory_stem + ".thy"


def repair_session(
    repairer: llm_repairer_multiline,
    session_name: str,
    additional_info: str | None = None,
) -> List[Tuple[str, str, str]]:
    """
    Repair every theory contained in this session (including nested directories).
    Returns a list of failures as (session_path, theory, error_message) tuples.
    """
    root_session = session_root_name(session_name)
    session_dir = Path(OUTPUT_ROOT) / root_session
    if not session_dir.is_dir():
        raise FileNotFoundError(f"Session directory not found: {session_dir}")

    print(f"[driver] building root session {root_session} before attempting repairs")
    initial_build = repairer.build(root_session)
    if initial_build != "success":
        print(f"[driver] initial build failed for {session_name}; skipping repairs")
        return [(session_name, "<initial build>", initial_build)]

    failures: List[Tuple[str, str, str]] = []
    for session_path, theory in iter_theory_targets(session_name):
        print(f"[driver] repairing {session_path}/{theory}")
        try:
            repairer.repair_session_theory(session=session_path, theory=theory, additional_info=additional_info)
        except Exception as exc:
            msg = f"{exc.__class__.__name__}: {exc}"
            print(f"[driver] ERROR repairing {session_path}/{theory}: {msg}")
            failures.append((session_path, theory, msg))

    return failures


def repair_sessions_from_list(
    repairer: llm_repairer_multiline,
    sessions: Iterable[str],
) -> List[Tuple[str, str, str]]:
    all_failures: List[Tuple[str, str, str]] = []
    for session in sessions:
        print(f"\n[driver] === Starting session: {session} ===")
        all_failures.extend(repair_session(repairer, session))
    return all_failures



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair Isabelle sessions and theories automatically.",
    )
    parser.add_argument(
        "--session",
        help="Repair a single session (path relative to SOURCE_AFP).",
    )

    parser.add_argument(
        "--show-configured-list",
        action="store_true",
        help="Show the SESSIONS_TO_REPAIR list from session_lister.py and exit.",
    )
    parser.add_argument(
        "--additional_info",
        help="Additional info about the changes between versions",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    source_root = Path(SOURCE_AFP)
    if not source_root.is_dir():
        raise SystemExit(f"SOURCE_AFP path does not exist: {SOURCE_AFP}")


    if args.show_configured_list:
        print("[driver] Sessions configured for batch repair (session_lister.py):")
        for name in SESSIONS_TO_REPAIR:
            print(f"  - {name}")
        if not SESSIONS_TO_REPAIR:
            print("  (empty list)")
        return

    repairer = llm_repairer_multiline()

    # repair one session 
    if args.session:
        failures = repair_session(repairer, args.session, args.additional_info)
    # repair all the sessions in SESSION_TO_REPAIR
    else:
        if not SESSIONS_TO_REPAIR:
            raise SystemExit(
                "SESSIONS_TO_REPAIR is empty. Edit auto_repair_pipeline/session_lister.py "
                "to select the sessions you want to process."
            )
        print("[driver] Using sessions defined in session_lister.py:")
        for name in SESSIONS_TO_REPAIR:
            print(f"  - {name}")
        failures = repair_sessions_from_list(repairer, SESSIONS_TO_REPAIR)

    if failures:
        print("\n[driver] completed with errors:")
        for session_path, theory, msg in failures:
            print(f"    - {session_path}/{theory}: {msg}")
    else:
        print("\n[driver] completed without errors")


if __name__ == "__main__":
    main()
