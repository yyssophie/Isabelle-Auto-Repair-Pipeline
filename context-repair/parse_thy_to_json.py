"""
Parse an Isabelle .thy file and save the result as a line-indexed JSON.

Usage:
    python parse_thy_to_json.py <path>

Where <path> is of the form:
    ~~/src/HOL/Computational_Algebra/Euclidean_Algorithm.thy
    (~~ resolves to SOURCE_ISABELLE home from setup.py)

Output is written to:
    /Volumes/PiggyBank/parsed_isabelle/<IsabelleVersion>/src/<relative_to_src>.json

JSON format:
    {
        "<start_line>": {"cmd": "...", "end_line": <N>},
        ...
    }
    end_line = start_line of next command - 1 (last command gets end_line = last line of file)
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PARSED_BASE = Path("/Volumes/PiggyBank/parsed_isabelle")
PARSE_UTILS = Path("/Users/yuanyusi/Desktop/Isabelle/Parse_Theory_Utils")

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(PARSE_UTILS))
from parse_theory import parse_theory  # type: ignore
import setup  # type: ignore

# SOURCE_ISABELLE = .../Isabelle2023/bin/isabelle  ->  home = grandparent
ISABELLE_HOME = Path(setup.SOURCE_ISABELLE).parent.parent  # e.g. .../Isabelle2023


def run(input_path: str) -> None:
    # Strip leading ~~ so "~~/src/HOL/..." -> "src/HOL/..."
    rel_str = input_path.lstrip("~").lstrip("/")
    rel_to_home = Path(rel_str)  # e.g. src/HOL/Computational_Algebra/Euclidean_Algorithm.thy

    thy_file = ISABELLE_HOME / rel_to_home
    if not thy_file.exists():
        raise FileNotFoundError(f"Theory file not found: {thy_file}")

    # Output: PiggyBank/<IsabelleVersion>/<rel_to_home without .thy>.json
    version_name = ISABELLE_HOME.name  # e.g. Isabelle2023
    out_path = PARSED_BASE / version_name / rel_to_home.with_suffix(".json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total_lines = sum(1 for _ in thy_file.open(encoding="utf-8"))

    print(f"Parsing: {thy_file}")
    cmds = parse_theory(
        thy_file,
        session="HOL",
        parsetheory_dir=str(PARSE_UTILS / "ParseTheory"),
        isabelle_home=ISABELLE_HOME,
    )

    # Build line-indexed dict; end_line = next cmd start - 1, or total lines for the last cmd
    result = {}
    for i, cmd in enumerate(cmds):
        start = cmd.line
        end = cmds[i + 1].line - 1 if i + 1 < len(cmds) else total_lines
        result[str(start)] = {"cmd": cmd.cmd, "end_line": end}

    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Written {len(result)} commands to: {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} '~~/src/path/to/File.thy'")
        sys.exit(1)
    run(sys.argv[1])
