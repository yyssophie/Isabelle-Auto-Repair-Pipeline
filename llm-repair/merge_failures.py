#!/usr/bin/env python3
import json
from pathlib import Path
from setup import *

OUTPUT_ROOT = Path(OUTPUT_ROOT)
AFP_THYS_ROOT = Path(SOURCE_AFP_PRE_MERGE)
MERGED_ROOT = Path(MERGED_ROOT)


def main() -> None:
    merged = 0
    for json_path in OUTPUT_ROOT.rglob("*.json"):
        if json_path.name == "executionTime.json":
            continue

        rel = json_path.relative_to(OUTPUT_ROOT)
        base_dir = Path(*rel.parts[:-1])
        theory_stem = Path(rel.name).stem  # strip .json
        theory_parts = theory_stem.split("@")
        if base_dir.parts and theory_parts and theory_parts[0] == base_dir.parts[-1]:
            theory_parts = theory_parts[1:]
        if not theory_parts:
            theory_parts = [theory_stem]
        theory_rel = base_dir.joinpath(*theory_parts).with_suffix(".thy")
        source_path = AFP_THYS_ROOT / theory_rel

        if not source_path.exists():
            print(f"[warn] missing theory {source_path}, skipped")
            continue

        try:
            record = json.loads(json_path.read_text())
        except json.JSONDecodeError as exc:
            print(f"[warn] bad JSON {json_path}: {exc}")
            continue

        failures = record.get("failures", [])
        theory_text = source_path.read_text()
        merged_path = MERGED_ROOT / theory_rel
        merged_path.parent.mkdir(parents=True, exist_ok=True)

        if not theory_text.endswith("\n"):
            theory_text += "\n"
        merged_text = theory_text + format_failures(rel, failures)
        merged_path.write_text(merged_text)
        merged += 1

    print(f"Merged {merged} theory files into {MERGED_ROOT}")


def format_failures(rel_path: Path, failures: list[dict]) -> str:
    if not failures:
        return ""

    lines = [
        "",
        "(*",
        f"  === Error Message ===",
    ]
    for idx, failure in enumerate(failures, start=1):
        pos = failure.get("pos", "?")
        msg = failure.get("msg", "").rstrip()
        lines.append(f"  [{idx}] line {pos}:")
        for line in msg.splitlines():
            lines.append("    " + line)
    lines.extend(["*)", ""])
    return "\n".join(lines)


if __name__ == "__main__":
    main()
