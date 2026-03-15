"""
batch_collect.py

Batched version of fact collection for a whole session:
  Phase 1 (fast, no Isabelle server): static check_proof_state for every theory
  Phase 2 (batched): ONE Isabelle server, one session_start per unique target-session,
                     then N use_theories calls reusing the same session_id

This amortises the expensive heap-loading cost across all theories in a session.

Usage:
    python batch_collect.py --session Actuarial_Mathematics
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile

# Suppress the harmless "Event loop is closed" GC noise from isabelle_client's
# asyncio subprocess transports being cleaned up after the loop exits.
# These appear as "Exception ignored in:" via sys.unraisablehook.
logging.getLogger("asyncio").setLevel(logging.CRITICAL)
logging.getLogger("asyncio.base_subprocess").setLevel(logging.CRITICAL)

_orig_unraisablehook = sys.unraisablehook
def _quiet_unraisablehook(unraisable):
    if isinstance(unraisable.exc_value, RuntimeError) and "Event loop is closed" in str(unraisable.exc_value):
        return
    _orig_unraisablehook(unraisable)
sys.unraisablehook = _quiet_unraisablehook
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

# ── repo-level path setup ────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "llm-repair"))

# ── external library paths ───────────────────────────────────────────────────
sys.path.insert(0, "/Volumes/PiggyBank/mirror-isabelle")
sys.path.insert(0, "/Users/yuanyusi/Desktop/Isabelle/isabelle-export-deps")

from setup import (
    SOURCE_AFP, DEPENDENCY_OUTPUT, SOURCE_ISABELLE, TARGET_ISABELLE, OUTPUT_ROOT,
)
from failure_extractor import failure_extractor as FailureExtractor
from static_proof_check import check_proof_state
from dep_extract import write_wrapper_theory, read_theory_name
from isabelle_client import get_isabelle_client, start_isabelle_server  # type: ignore

import json, re

# ─────────────────────────────────────────────────────────────────────────────
# Constants (previously from fact_collector)
# ─────────────────────────────────────────────────────────────────────────────

SOURCE_ISABELLE_HOME = Path(SOURCE_ISABELLE).parent.parent
EXPORT_DEPS_DIR      = str(Path("/Users/yuanyusi/Desktop/Isabelle/isabelle-export-deps/ExportDeps"))

_SESSION_MAP_PATH = Path(__file__).parent.parent / "session_finder" / "data" / "source_afp.json"
_SESSION_MAP: dict[str, str] | None = None

def _get_session_map() -> dict[str, str]:
    global _SESSION_MAP
    if _SESSION_MAP is None:
        _SESSION_MAP = json.loads(_SESSION_MAP_PATH.read_text())
    return _SESSION_MAP  # type: ignore[return-value]

_BLOCK_HEADER_RE = re.compile(r"\[(\d+)\]\s+line\s+(\d+):")

def _split_blocks(error_message: str) -> List[tuple[int, int, str]]:
    matches = list(_BLOCK_HEADER_RE.finditer(error_message))
    result = []
    for i, m in enumerate(matches):
        idx  = int(m.group(1))
        line = int(m.group(2))
        start = m.end()
        end   = matches[i + 1].start() if i + 1 < len(matches) else len(error_message)
        result.append((idx, line, error_message[start:end]))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BlockFacts:
    block_idx:      int
    line:           int
    strategy:       Optional[str] = None   # "open_proof" | None
    open_stmt_name: Optional[str] = None
    dep_content:    Optional[str] = None


@dataclass
class SessionJob:
    """Holds all error blocks for one theory file."""
    session_path: str          # e.g. "Actuarial_Mathematics"
    theory_path:  Path         # absolute path in SOURCE_AFP
    theory_stem:  str          # e.g. "Examples"
    block_facts:  List[BlockFacts] = field(default_factory=list)


@dataclass
class DepTask:
    """A single dep_extract task queued for Phase 2."""
    theory_path:    Path
    target_session: str   # Isabelle session name from source_afp.json
    theory_name:    str   # as read from the theory file header
    fact_name:      str   # locale-qualified fact name
    bf:             BlockFacts  # reference: Phase 2 writes dep_content here


def _write_dep_file(dep_path: Path, results: List[BlockFacts]) -> None:
    if all(bf.strategy is None for bf in results):
        print(f"[batch] skipped (no facts extracted): {dep_path}")
        return
    dep_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for bf in results:
        lines.append(f"[{bf.block_idx}] line {bf.line}:")
        if bf.strategy == "open_proof":
            lines.append(f"  in proof of: {bf.open_stmt_name}")
            if bf.dep_content:
                for content_line in bf.dep_content.splitlines():
                    lines.append(f"  {content_line}")
        lines.append("")
    dep_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[batch] wrote {dep_path}")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _setup_isabelle_path(isabelle_home: Path) -> None:
    bin_dir = str(isabelle_home / "bin")
    current = os.environ.get("PATH", "")
    if bin_dir not in current.split(os.pathsep):
        os.environ["PATH"] = bin_dir + os.pathsep + current


def iter_theory_targets(session_name: str) -> Iterator[Tuple[str, str]]:
    """
    Yield (session_path, theory_file) tuples by scanning OUTPUT_ROOT/{root_session}/
    for JSON files belonging to session_name (or any sub-session).
    Same logic as test_pre.py — copied here to avoid modifying that file.
    """
    import re
    _BLOCK_HEADER_RE_check = re.compile(r"\[(\d+)\]\s+line\s+(\d+):")

    root_session = session_name.split("/")[0]
    session_dir  = Path(OUTPUT_ROOT) / root_session
    session_prefix = session_name.replace("/", "@")

    for entry in sorted(session_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_file() or entry.suffix.lower() != ".json":
            continue
        stem = entry.stem
        at_idx = stem.rfind("@")
        if at_idx == -1:
            continue
        file_session = stem[:at_idx]
        theory_stem  = stem[at_idx + 1:]

        if file_session != session_prefix and not file_session.startswith(session_prefix + "@"):
            continue

        yield file_session.replace("@", "/"), theory_stem + ".thy"


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — static proof-state analysis (no Isabelle server)
# ─────────────────────────────────────────────────────────────────────────────

def phase1_collect(session_name: str) -> Tuple[List[SessionJob], List[DepTask]]:
    """
    For every theory in session_name:
      - Extract the error message from the pre-computed JSON
      - Run check_proof_state for each error block (pure static analysis)
      - Queue a DepTask for blocks that are inside a named proof

    Returns (session_jobs, dep_tasks).
    """
    session_jobs: List[SessionJob] = []
    dep_tasks:    List[DepTask]    = []

    for session_path, theory in iter_theory_targets(session_name):
        print(f"\n[phase1] ── {session_path}/{theory} ──")
        theory_path = Path(SOURCE_AFP) / session_path / theory
        theory_stem = theory_path.stem

        try:
            error_message = FailureExtractor().extract_error_message(session_path, theory)
        except Exception as exc:
            print(f"[phase1] ERROR extracting errors: {exc.__class__.__name__}: {exc}")
            continue

        blocks      = _split_blocks(error_message)
        block_facts: List[BlockFacts] = []

        for idx, line_num, block_text in blocks:
            print(f"  [phase1] block {idx} (line {line_num})")
            bf = BlockFacts(block_idx=idx, line=line_num)

            try:
                proof = check_proof_state(
                    theory_file=theory_path,
                    target_line=line_num,
                )
            except Exception as exc:
                print(f"  [phase1] check_proof_state failed: {exc.__class__.__name__}: {exc}")
                block_facts.append(bf)
                continue

            if proof.in_proof and proof.fact_name is not None:
                bf.strategy      = "open_proof"
                bf.open_stmt_name = proof.fact_name
                print(f"  [phase1] in proof of: {proof.fact_name!r} (line {proof.open_stmt_line})")

                target_session = _get_session_map().get(str(theory_path))
                if target_session is None:
                    print(f"  [phase1] no session entry for {theory_path} — skipping dep task")
                else:
                    try:
                        thy_name = read_theory_name(theory_path)
                    except Exception as exc:
                        print(f"  [phase1] could not read theory name: {exc}")
                        thy_name = theory_stem

                    dep_tasks.append(DepTask(
                        theory_path=theory_path,
                        target_session=target_session,
                        theory_name=thy_name,
                        fact_name=proof.fact_name,
                        bf=bf,
                    ))
            else:
                print(f"  [phase1] not in proof state")

            block_facts.append(bf)

        session_jobs.append(SessionJob(
            session_path=session_path,
            theory_path=theory_path,
            theory_stem=theory_stem,
            block_facts=block_facts,
        ))

    return session_jobs, dep_tasks


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — batched dep extraction (one server per target-session group)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_group(
    target_session: str,
    tasks: List[DepTask],
    tmp_base: Path,
) -> None:
    """
    Start ONE Isabelle server, call session_start once, then call use_theories
    once per task — all sharing the same session_id.
    """
    _setup_isabelle_path(SOURCE_ISABELLE_HOME)

    # Pass the whole AFP thys root so that parent sessions (e.g. CryptHOL)
    # are discoverable when the target session inherits from another AFP session.
    dirs = [str(Path(SOURCE_AFP)), EXPORT_DEPS_DIR]

    print(f"\n[phase2] session_start(session={target_session!r})")
    server_info, _ = start_isabelle_server()
    isabelle = get_isabelle_client(server_info)

    try:
        start_resps = isabelle.session_start(
            session=target_session,
            dirs=dirs,
        )
        session_id = start_resps[-1].response_body.session_id
        print(f"[phase2] session_id={session_id}  ({len(tasks)} tasks)")

        for i, task in enumerate(tasks):
            wrapper_name = f"Deps_Wrapper_{i}"
            task_dir     = tmp_base / f"task_{i}"
            task_dir.mkdir(parents=True, exist_ok=True)
            out_rel      = "deps_out.toml"

            exportdeps_import = "ExportDeps.ExportDeps"
            target_import     = f"{target_session}.{task.theory_name}"

            write_wrapper_theory(
                wrapper_dir=task_dir,
                wrapper_theory_name=wrapper_name,
                exportdeps_import=exportdeps_import,
                target_import=target_import,
                theorem_names=[task.fact_name],
                out_rel_path=out_rel,
            )

            print(f"[phase2] ({i + 1}/{len(tasks)}) use_theories for {task.fact_name!r}")
            try:
                isabelle.use_theories(
                    theories=[wrapper_name],
                    master_dir=str(task_dir),
                    session_id=session_id,
                )
            except Exception as exc:
                print(f"[phase2] use_theories failed: {exc.__class__.__name__}: {exc}")
                continue

            produced = task_dir / out_rel
            if produced.exists():
                task.bf.dep_content = produced.read_text(encoding="utf-8")
                print(f"[phase2] got deps for {task.fact_name!r}")
            else:
                print(f"[phase2] no output produced for {task.fact_name!r}")

    finally:
        try:
            isabelle.shutdown()
        except Exception:
            pass


def phase2_extract_deps(dep_tasks: List[DepTask], tmp_base: Path) -> None:
    """Group tasks by target-session and call _extract_group for each."""
    groups: Dict[str, List[DepTask]] = defaultdict(list)
    for task in dep_tasks:
        groups[task.target_session].append(task)

    for target_session, tasks in groups.items():
        print(f"\n[phase2] === target_session={target_session!r}  tasks={len(tasks)} ===")
        try:
            _extract_group(target_session, tasks, tmp_base / target_session.replace("/", "@"))
        except Exception as exc:
            print(f"[phase2] ERROR for session {target_session!r}: {exc.__class__.__name__}: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Top-level entry point
# ─────────────────────────────────────────────────────────────────────────────

def batch_collect_session(session_name: str) -> None:
    """
    Full batched pipeline for one session:
      1. Phase 1: static proof analysis for all theories (fast)
      2. Phase 2: one Isabelle server per target-session group, N use_theories calls
      3. Write dep files to DEPENDENCY_OUTPUT
    """
    print(f"\n[batch] === batch_collect_session({session_name!r}) ===")

    # Phase 1
    session_jobs, dep_tasks = phase1_collect(session_name)
    print(f"\n[batch] Phase 1 complete: {len(session_jobs)} theories, {len(dep_tasks)} dep tasks")

    # Phase 2
    if dep_tasks:
        with tempfile.TemporaryDirectory(prefix="batch-deps-") as tmp:
            phase2_extract_deps(dep_tasks, Path(tmp))
    else:
        print("[batch] No dep tasks — skipping Phase 2")

    # Write dep files
    target_version = Path(TARGET_ISABELLE).parent.parent.name   # e.g. "Isabelle2024"
    source_version = Path(SOURCE_ISABELLE).parent.parent.name   # e.g. "Isabelle2023"
    version_dir    = f"{source_version}-To-{target_version}"

    for job in session_jobs:
        dep_path = (
            Path(DEPENDENCY_OUTPUT)
            / version_dir
            / job.session_path
            / f"{job.theory_stem}_dep.txt"
        )
        _write_dep_file(dep_path, job.block_facts)
