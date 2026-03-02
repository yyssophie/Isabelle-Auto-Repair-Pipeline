from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "llm-repair"))
sys.path.insert(0, str(Path(__file__).parent))

from failure_extractor import failure_extractor
from llm_repairer_multiline import llm_repairer_multiline
from attempt_writer import attempt_writer
from classifier import CannotBeFixed, classifier
from handler import handler
from mirabelle_runner import mirabelle_runner
from parser import parser
from repair_driver import iter_theory_targets
from session_lister import SESSIONS_TO_REPAIR


class driver:
    def __init__(self) -> None:
        self._parser = parser()
        self._failure_extractor = failure_extractor()
        self._repairer = llm_repairer_multiline()
        self._classifier = classifier()
        self._classifier.handler = handler()
        self._mirabelle_runner = mirabelle_runner()
        self._attempt_writer = attempt_writer()

    def drive_sledgehammer_fix(self, session: str, theory: str) -> None:
        print(f"====== starting to fix {session}/{theory} ======")
        start_time = time.monotonic()
        error_message = ""
        wrote_result = False
        made_backup = False

        try:
            error_message = self._failure_extractor.extract_error_message(session, theory)
            blocks = self._parser.parse_error_merssage(error_message)
            print(f"[driver] Extracted {len(blocks)} block(s) from error message.")

            processed_lines: List[int] = []
            encountered_cannot_fix = False

            for idx, block in enumerate(blocks, 1):
                print(f"[driver] Processing block {idx}/{len(blocks)}")
                try:
                    if not made_backup:
                        self._repairer.backup_and_copy(session, theory)
                        print(f"[driver] Backed up target theory for editing.")
                        made_backup = True
                    line = self._classifier.classify(session, theory, block)
                    print(f"[driver] Block {idx} classified as line {line}.")
                    processed_lines.append(line)
                except CannotBeFixed as exc:
                    print(f"[sledgehammer][block {idx}] Cannot be fixed: {exc}")
                    encountered_cannot_fix = True
                    break
                except Exception as exc:
                    raise RuntimeError(
                        f"Error while processing block {idx} for {session}/{theory}"
                    ) from exc

            if encountered_cannot_fix:
                self._attempt_writer.write_attempt_4(
                    session=session,
                    theory=theory,
                    error_message=error_message,
                    fixes={1: "This error cannot be solved by sledgehammer"},
                    status="fail",
                    elapsed_seconds=time.monotonic() - start_time,
                )
                wrote_result = True
                print("[driver] Aborting due to CannotBeFixed; attempt recorded as fail.")
                return

            log_path: Path | None = None
            for line in processed_lines:
                try:
                    print(f"[driver] Running Mirabelle on line {line}...")
                    log_path = Path(self._mirabelle_runner.run_mirabelle(session, theory, line))
                    print(f"[driver] Mirabelle completed for line {line}, log at {log_path}.")
                except Exception as exc:
                    raise RuntimeError(
                        f"Mirabelle processing failed for line {line} in {session}/{theory}"
                    ) from exc

            if log_path is None:
                print("[driver] No lines were processed; skipping log parsing.")
                fixes: Dict[int, str] = {}
                status = "fail"
            else:
                print(f"[driver] Parsing Mirabelle log {log_path} for lines {processed_lines}.")
                fixes, status = self._parser.parse_mirabelle_log(log_path, processed_lines)

            self._attempt_writer.write_attempt_4(
                session=session,
                theory=theory,
                error_message=error_message,
                fixes=fixes,
                status=status,
                elapsed_seconds=time.monotonic() - start_time,
            )
            wrote_result = True
            print(f"[driver] Attempt recorded with status '{status}'.")
            if fixes:
                for ln, fix in fixes.items():
                    print(f"[driver] Suggested fix for line {ln}: {fix}")
            else:
                print("[driver] No fixes extracted from Mirabelle log.")
        finally:
            if made_backup:
                self._restore_target_file(session, theory)
                print(f"[driver] Restoration complete for {session}/{theory}.")
            if not wrote_result:
                try:
                    self._attempt_writer.write_attempt_4(
                        session=session,
                        theory=theory,
                        error_message=error_message,
                        fixes={},
                        status="fail",
                        elapsed_seconds=time.monotonic() - start_time,
                    )
                except Exception as e:
                    print(f"[driver] WARNING: failed to write excel log: {e!r}")


    def _restore_target_file(self, session: str, theory: str) -> None:
        try:
            self._repairer.restore(session, theory)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to restore {session}/{theory} after attempting repairs"
            ) from exc


def fix_session(
    d: driver,
    session_name: str,
) -> List[Tuple[str, str, str]]:
    """
    Fix every theory in this session by running sledgehammer repair.
    Returns a list of failures as (session_path, theory, error_message) tuples.
    """
    failures: List[Tuple[str, str, str]] = []
    for session_path, theory in iter_theory_targets(session_name):
        print(f"[driver] fixing {session_path}/{theory}")
        try:
            d.drive_sledgehammer_fix(session=session_path, theory=theory)
        except Exception as exc:
            msg = f"{exc.__class__.__name__}: {exc}"
            print(f"[driver] ERROR fixing {session_path}/{theory}: {msg}")
            failures.append((session_path, theory, msg))
    return failures


def fix_sessions_from_list(
    d: driver,
    sessions: Iterable[str],
) -> List[Tuple[str, str, str]]:
    all_failures: List[Tuple[str, str, str]] = []
    for session in sessions:
        print(f"\n[driver] === Starting session: {session} ===")
        all_failures.extend(fix_session(d, session))
    return all_failures


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Sledgehammer-repair Isabelle sessions and theories automatically.",
    )
    p.add_argument(
        "--session",
        help="Fix a single session (e.g. ADS_Functor or CakeML/Tests).",
    )
    p.add_argument(
        "--show-configured-list",
        action="store_true",
        help="Show the SESSIONS_TO_REPAIR list from session_lister.py and exit.",
    )
    return p.parse_args()


def main():
    args = parse_args()

    if args.show_configured_list:
        print("[driver] Sessions configured for batch repair (session_lister.py):")
        for name in SESSIONS_TO_REPAIR:
            print(f"  - {name}")
        if not SESSIONS_TO_REPAIR:
            print("  (empty list)")
        return

    d = driver()

    if args.session:
        failures = fix_session(d, args.session)
    else:
        if not SESSIONS_TO_REPAIR:
            raise SystemExit(
                "SESSIONS_TO_REPAIR is empty. Edit session_lister.py "
                "to select the sessions you want to process."
            )
        print("[driver] Using sessions defined in session_lister.py:")
        for name in SESSIONS_TO_REPAIR:
            print(f"  - {name}")
        failures = fix_sessions_from_list(d, SESSIONS_TO_REPAIR)

    if failures:
        print("\n[driver] completed with errors:")
        for session_path, theory, msg in failures:
            print(f"    - {session_path}/{theory}: {msg}")
    else:
        print("\n[driver] completed without errors")


if __name__ == "__main__":
    main()
