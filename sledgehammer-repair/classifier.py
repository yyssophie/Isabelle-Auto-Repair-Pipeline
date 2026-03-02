import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from handler import *

class CannotBeFixed(Exception):
    """Raised when the block is not eligible for automated sledgehammer-style repair."""
    pass

class classifier:

    """
    Classifies an Isabelle/Mirabelle error block and dispatches to a handler.

    Expected handler interface:
    - handler.handle_apply_by(session: str, theory: str, line: int) -> int
    - handler.handle_proof_qed(session: str, theory: str, line: int) -> int

    classify(...) returns the line number where the handler inserted the repair.
    """

    # ---- Patterns (keep them fairly strict to avoid false positives) ----
    _re_line = re.compile(r"(?m)^\s*\[\d+\]\s*line\s+(\d+)\s*:\s*$")
    _re_at_command = re.compile(r'At command\s+"([^"]+)"\s*\(line\s+(\d+)\)', re.IGNORECASE)
    _re_ml_error = re.compile(r"\bML error\b", re.IGNORECASE)
    _re_no_such_file = re.compile(r"\bNo such file\b", re.IGNORECASE)

    _re_timeout = re.compile(r"\btimeout\b|\btimed\s*out\b", re.IGNORECASE)

    # Things you consider "cannot be fixed" if they are the failing command.
    _unfixable_cmds = {
        "lemma", "theorem", "sublocale", "proposition",
        "<malformed>", "term", "definition", "declare",
    }

    # "Direct" sledgehammer targets.
    _direct_cmds = {"by", "apply"}

    def __init__(self):
        self.handler: handler = handler()

    def _extract_line(self, block: str) -> Optional[int]:
        """
        Prefer 'At command "... (line N)"', otherwise fall back to '[k] line N:'.
        If both exist and disagree, prefer the 'At command' line since it's
        more directly tied to the failing command.
        """
        m_cmd = self._re_at_command.search(block)
        if m_cmd:
            return int(m_cmd.group(2))

        m = self._re_line.search(block)
        if m:
            return int(m.group(1))

        return None

    def _extract_command(self, block: str) -> Optional[str]:
        m = self._re_at_command.search(block)
        if not m:
            return None
        return m.group(1).strip()

    def _contains_unfixable_markers(self, block: str) -> bool:
        if self._re_ml_error.search(block):
            return True
        if self._re_no_such_file.search(block):
            return True
        return False
    
    def _is_timeout_block(self, block: str) -> bool:
        return self._re_timeout.search(block) is not None

    def classify(self, session: str, theory: str, block: str) -> int:
        """
        Category 1:
          - block contains "ML error" OR "No such file" OR
          - failing command is one of:
              lemma/theorem/sublocale/proposition/<malformed>/term/definition/declare
          => raise CannotBeFixed

        Category 2:
          - failing command is "by" or "apply"
          => call handle_apply_by(session, theory, line)

        Category 3:
          - otherwise:
              try handle_apply_by first
              if it raises => try handle_proof_qed
              else => raise CannotBeFixed
        """
        line = self._extract_line(block)
        cmd = self._extract_command(block)
        if line is None:
            raise CannotBeFixed("Cannot locate a line number in the error block.")

        # (1) Hard-unfixable markers in the block
        if self._contains_unfixable_markers(block):
            raise CannotBeFixed("Block contains ML-level error or missing file; not repairable here.")

        # (1) Unfixable command types (based on the failing command)
        if cmd is not None:
            cmd_lc = cmd.lower()
            if cmd_lc in self._unfixable_cmds:
                raise CannotBeFixed(f'Failing command "{cmd}" is not eligible for sledgehammer-style repair.')

        # (2) Direct sledgehammer targets
        if cmd is not None and cmd.lower() in self._direct_cmds:
            return self.handler.handle_apply_by(session, theory, line)
        
        # (3) Time out
        if self._is_timeout_block(block):
            try:
                return self.handler.handle_apply_by(session, theory, line)
            except Exception:
                pass

            try:
                return self.handler.handle_proof_qed(session, theory, line)
            except Exception:
                raise CannotBeFixed("Timeout error be fixed with sledgehammer: both apply...by and proof..qed repair failed.")
        

        # (4) Fallback strategy
        # try:
        #     return self.handler.handle_apply_by(session, theory, line)
        # except Exception:
        #     pass

        try:
            return self.handler.handle_proof_qed(session, theory, line)
        except Exception:
            raise CannotBeFixed("Cannot be fixed with sledgehammer: proof..qed repair failed.")
        
