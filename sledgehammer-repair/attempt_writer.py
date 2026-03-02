import sys
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).parent.parent / "llm-repair"))

from excel_creater import *

class attempt_writer:
    def __init__(self):
        pass

    def write_attempt_4(
        self,
        session: str,
        theory: str,
        error_message: str,
        fixes: Dict[int, str],
        status: str,
        elapsed_seconds: float | None = None):

        lines_sorted = sorted(fixes.keys())
        fixes_text = "\n".join([f"line {ln}: {fixes[ln]}" for ln in lines_sorted])

        ec = excel_creater()
        ec.append_row(
            session=session,
            theory=theory,
            attempt=1,
            error_message=error_message,
            fixes_text=fixes_text,
            status=status,
            elapsed_seconds=elapsed_seconds,
            sheet_name="sledgehammer",
        )
    