"""
static_proof_check.py  (new-context-repair version)

Same proof-state analysis as Parse_Theory_Utils/static_proof_check.py, but
reads the already-parsed theory from the pre-computed JSON files in OUTPUT_ROOT
instead of spawning an Isabelle server.

JSON location:
    OUTPUT_ROOT / {root_session} / {session_with_@}@{theory_stem}.json
e.g.
    .../output/2024-label-2023/Actuarial_Mathematics/Actuarial_Mathematics@Survival_Model.json

JSON schema (relevant fields):
    { "cmds": [ {"lineNum": int, "cmdType": str, "cmd": str}, ... ], ... }

Public API (same as original; isabelle_home / parsetheory_dir accepted but ignored):
    check_proof_state(theory_file, target_line, **ignored) -> ProofCheckResult
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "llm-repair"))

from setup import OUTPUT_ROOT, SOURCE_AFP


# ─────────────────────────────────────────────────────────────────────────────
# JSON loading
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _Cmd:
    line: int
    kind: str   # cmdType from JSON (already parsed by Isabelle)
    cmd:  str


def _json_path_for_theory(theory_file: Path) -> Path:
    """
    Derive the OUTPUT_ROOT JSON path for a theory file.
        OUTPUT_ROOT / session[0] / session.replace('/', '@')@theory_stem.json
    where session is the relative directory path from SOURCE_AFP to the theory.
    """
    session     = str(theory_file.relative_to(Path(SOURCE_AFP)).parent)
    root        = session.split("/")[0]
    prefix      = session.replace("/", "@")
    theory_stem = theory_file.stem
    return Path(OUTPUT_ROOT) / root / f"{prefix}@{theory_stem}.json"


def _load_cmds(theory_file: Path) -> list[_Cmd]:
    json_path = _json_path_for_theory(theory_file)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    return [
        _Cmd(line=entry["lineNum"], kind=entry["cmdType"], cmd=entry["cmd"])
        for entry in data.get("cmds", [])
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Command classification
# ─────────────────────────────────────────────────────────────────────────────

PROOF_OPENERS: frozenset[str] = frozenset({
    "lemma", "theorem", "corollary", "proposition", "schematic_goal",
    "function", "termination",
    "global_interpretation", "sublocale", "interpretation",
    "have", "show", "hence", "thus", "obtain", "consider", "suffices",
})

PROOF_CLOSERS: frozenset[str] = frozenset({"qed", "done", "by", "sorry", "oops", ".", ".."})

CONTEXT_OPENERS: frozenset[str] = frozenset({"context", "locale"})

_OPENER_ALT = "|".join(re.escape(c) for c in sorted(PROOF_OPENERS, key=len, reverse=True))

_CONTEXT_NAME_RE = re.compile(r"^\s*(?:context|locale)\s+(?!begin\b)([\w][\w.']*)")
_IN_LOCALE_RE    = re.compile(
    r"^\s*(?:" + _OPENER_ALT + r")\b\s*\(in\s+([\w][\w.']*)\s*\)"
)
_FACT_NAME_RE    = re.compile(
    r"^\s*(?:" + _OPENER_ALT + r")\b"
    r"(?:\s*\([^)]*\))*"
    r"\s+([\w][^\s:\"]*)"
)


# ─────────────────────────────────────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProofCheckResult:
    in_proof:       bool
    open_stmt_line: Optional[int]
    open_stmt:      Optional[str]
    fact_name:      Optional[str]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_fact_name(cmd: str, context_prefix: str = "") -> Optional[str]:
    first_line = cmd.splitlines()[0] if cmd else ""
    m_in      = _IN_LOCALE_RE.match(first_line)
    qualifier = m_in.group(1) if m_in else context_prefix
    m = _FACT_NAME_RE.match(first_line)
    if not m:
        return None
    local_name = m.group(1)
    return f"{qualifier}.{local_name}" if qualifier else local_name


# ─────────────────────────────────────────────────────────────────────────────
# Core
# ─────────────────────────────────────────────────────────────────────────────

def check_proof_state(
    theory_file: Path,
    target_line: int,
    **__,   # accepts isabelle_home, parsetheory_dir, verbose from old call sites
) -> ProofCheckResult:
    """
    Statically determine whether target_line falls inside an open proof.
    Reads the pre-parsed JSON from OUTPUT_ROOT — no Isabelle server needed.
    """
    all_cmds = _load_cmds(theory_file)

    before = [c for c in all_cmds if c.line <= target_line]
    if not before:
        return ProofCheckResult(in_proof=False, open_stmt_line=None, open_stmt=None, fact_name=None)

    proof_stack:   list[_Cmd] = []
    context_stack: list[str]  = []

    for c in before:
        kw = c.kind
        if kw in CONTEXT_OPENERS:
            if re.search(r'\bbegin\b', c.cmd):
                m = _CONTEXT_NAME_RE.match(c.cmd.splitlines()[0])
                # Push name for named contexts, "" for anonymous ones so that
                # "end" pops the right entry instead of an outer named locale.
                context_stack.append(m.group(1) if m else "")
        elif kw == "end" and not proof_stack:
            if context_stack:
                context_stack.pop()
        elif kw in PROOF_OPENERS:
            proof_stack.append(c)
        elif kw in PROOF_CLOSERS and proof_stack:
            proof_stack.pop()

    if not proof_stack:
        return ProofCheckResult(in_proof=False, open_stmt_line=None, open_stmt=None, fact_name=None)

    open_cmd       = proof_stack[0]
    context_prefix = ".".join(s for s in context_stack if s)
    return ProofCheckResult(
        in_proof=True,
        open_stmt_line=open_cmd.line,
        open_stmt=open_cmd.cmd,
        fact_name=_extract_fact_name(open_cmd.cmd, context_prefix),
    )
