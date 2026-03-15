"""
For each dep.txt in DEPENDENCY_OUTPUT/Isabelle2023-To-Isabelle2024:
  For each block ([N] line ...:):
    For each [[theorems.dependencies]] pos that is a ~~/src/...thy path:
      - Parse the .thy file via parse_thy_to_json (skip if already cached)
      - Find the command spanning that line
      - Check the diff file for changes in that line range
      - If changed, write to CHANGES_OUTPUT/<same relative path>.txt

Output format per block with changes:
  [N] line <error_line>:
    dep: <dep_name> @ <thy_path>:<line>
    line <start>-<end>:
      <diff hunk lines>
"""

import glob
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
import setup  # type: ignore

from parse_thy_to_json import run as parse_and_cache, ISABELLE_HOME, PARSED_BASE, PARSE_UTILS

DEPENDENCY_OUTPUT = Path(setup.DEPENDENCY_OUTPUT)
CHANGES_OUTPUT    = Path(setup.CHANGES_OUTPUT)
DIFFS_BASE        = Path("/Volumes/PiggyBank/mirror-isabelle/diffs")

# Derive diff folder from SOURCE/TARGET isabelle bin paths
# e.g. .../Isabelle2023/bin/isabelle -> Isabelle2023
_src_ver  = Path(setup.SOURCE_ISABELLE).parent.parent.name  # Isabelle2023
_tgt_ver  = Path(setup.TARGET_ISABELLE).parent.parent.name  # Isabelle2024
DIFF_DIR  = DIFFS_BASE / f"{_src_ver}-To-{_tgt_ver}"       # .../Isabelle2023-To-Isabelle2024

DEP_SUBDIR = f"{_src_ver}-To-{_tgt_ver}"


# ── helpers ──────────────────────────────────────────────────────────────────

def parsed_json_path(tilde_path: str) -> Path:
    """~~/src/HOL/X.thy  ->  PARSED_BASE/Isabelle2023/src/HOL/X.json"""
    rel = tilde_path.lstrip("~").lstrip("/")          # src/HOL/X.thy
    return PARSED_BASE / ISABELLE_HOME.name / Path(rel).with_suffix(".json")


def ensure_parsed(tilde_path: str) -> dict | None:
    """Return parsed JSON dict, parsing+caching if needed. Returns None on error."""
    out = parsed_json_path(tilde_path)
    if not out.exists():
        try:
            parse_and_cache(tilde_path)
        except Exception as e:
            print(f"  [skip] parse failed for {tilde_path}: {e}", file=sys.stderr)
            return None
    try:
        return json.loads(out.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [skip] load failed for {out}: {e}", file=sys.stderr)
        return None


def find_cmd_range(parsed: dict, line: int) -> tuple[int, int]:
    """Return (start, end) of the command that best covers `line`.

    Strategy: find the largest start_line <= line; use its end_line.
    If line is below all commands, use the first command.
    """
    keys = sorted(int(k) for k in parsed)
    if not keys:
        return line, line
    # last key <= line
    lo = keys[0]
    for k in keys:
        if k <= line:
            lo = k
        else:
            break
    entry = parsed[str(lo)]
    end = entry["end_line"] if entry["end_line"] is not None else lo
    return lo, end


def diff_changes_in_range(thy_rel: str, start: int, end: int) -> list[str]:
    """Return diff hunk lines that overlap [start, end] from the .diff file.

    thy_rel: e.g. src/HOL/Analysis/Measure_Space.thy  (relative to Isabelle root)
    Diff file: DIFF_DIR / thy_rel + ".diff"
    Hunks use OLD line numbers (the source file).
    """
    diff_file = DIFF_DIR / (thy_rel + ".diff")
    if not diff_file.exists():
        return []

    content = diff_file.read_text(encoding="utf-8", errors="replace")
    result_lines = []

    # Split into hunks
    hunk_re = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', re.MULTILINE)
    hunk_starts = [m.start() for m in hunk_re.finditer(content)]

    for i, hs in enumerate(hunk_starts):
        hunk_end = hunk_starts[i + 1] if i + 1 < len(hunk_starts) else len(content)
        hunk_text = content[hs:hunk_end]
        m = hunk_re.match(hunk_text)
        old_start = int(m.group(1))
        old_count = int(m.group(2)) if m.group(2) is not None else 1
        old_end   = old_start + old_count - 1

        # Check overlap with [start, end]
        if old_end < start or old_start > end:
            continue

        result_lines.append(hunk_text.rstrip())

    return result_lines


def parse_dep_sections(content: str) -> list[dict]:
    """Split dep.txt content into sections with metadata."""
    sections = re.split(r'(?=^\[\d+\] line \d+:)', content, flags=re.MULTILINE)
    result = []
    for sec in sections:
        m = re.match(r'^\[(\d+)\] line (\d+):', sec)
        if not m:
            continue
        # Extract all dep pos fields
        dep_entries = []
        # Each [[theorems]] block may have multiple [[theorems.dependencies]] with pos
        # We scan for pos lines after each key= raw= pretty= block
        # Simpler: just find all  key = "..."  pos = "..."  pairs in order
        # Actually we want: for each dependency, its key/name and pos
        dep_blocks = re.finditer(
            r'key = "([^"]+)"[^\[]*?pos = "([^"]+)"',
            sec,
            re.DOTALL,
        )
        for db in dep_blocks:
            key = db.group(1)
            pos = db.group(2)
            dep_entries.append({"key": key, "pos": pos})

        result.append({
            "idx": m.group(1),
            "error_line": int(m.group(2)),
            "deps": dep_entries,
            "raw": sec,
        })
    return result


# ── main ─────────────────────────────────────────────────────────────────────

dep_root = DEPENDENCY_OUTPUT / DEP_SUBDIR
all_dep_files = sorted(dep_root.rglob("*_dep.txt"))
total = len(all_dep_files)

for file_idx, dep_file in enumerate(all_dep_files, 1):
    rel = dep_file.relative_to(dep_root)          # e.g. ABY3_Protocols/Spmf_Common_dep.txt
    out_path = CHANGES_OUTPUT / DEP_SUBDIR / rel.with_suffix(".txt")

    if out_path.exists():
        print(f"[{file_idx}/{total}] skip (already done): {rel}")
        continue

    print(f"[{file_idx}/{total}] processing: {rel}")

    content = dep_file.read_text(encoding="utf-8")
    sections = parse_dep_sections(content)

    out_blocks = []  # collect output for this dep file

    for sec in sections:
        block_lines = []

        for dep in sec["deps"]:
            pos_str = dep["pos"]  # e.g. ~~/src/HOL/Analysis/Measure_Space.thy:1535:59786:59800

            # Must start with ~~/src and end with .thy
            if not pos_str.startswith("~~/src") or not pos_str.split(":")[0].endswith(".thy"):
                continue

            parts = pos_str.split(":")
            tilde_path = parts[0]                 # ~~/src/HOL/Analysis/Measure_Space.thy
            try:
                dep_line = int(parts[1])
            except (IndexError, ValueError):
                continue

            thy_rel = tilde_path.lstrip("~").lstrip("/")  # src/HOL/Analysis/Measure_Space.thy

            # Parse / load cached JSON
            parsed = ensure_parsed(tilde_path)
            if parsed is None:
                continue

            cmd_start, cmd_end = find_cmd_range(parsed, dep_line)

            # Check diffs
            hunks = diff_changes_in_range(thy_rel, cmd_start, cmd_end)
            if not hunks:
                continue

            block_lines.append(
                f"  dep: {dep['key']} @ {tilde_path}:{dep_line}"
            )
            block_lines.append(f"  cmd lines: {cmd_start}-{cmd_end}")
            for hunk in hunks:
                for hline in hunk.splitlines():
                    block_lines.append(f"    {hline}")

        if block_lines:
            out_blocks.append(
                f"[{sec['idx']}] line {sec['error_line']}:\n" + "\n".join(block_lines)
            )

    if not out_blocks:
        print(f"  -> no changes found")
        # Write empty marker so re-runs skip this file too
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.touch()
        continue

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n\n".join(out_blocks) + "\n", encoding="utf-8")
    print(f"  -> written {len(out_blocks)} blocks with changes")

print("Done.")
