# Isabelle Auto-Repair Pipeline

Automatically repairs [Archive of Formal Proofs (AFP)](https://www.isa-afp.org/) theories that fail to build due to version incompatibilities between Isabelle releases (e.g. AFP 2024 → Isabelle 2025).

The pipeline reads pre-extracted build failures from structured JSON files, uses an LLM to propose line-level fixes, applies them to the target AFP tree, and verifies each fix by running a real Isabelle build.

---

## What it does

1. Reads failure information from a labeled error dataset (`OUTPUT_ROOT`, see below).
2. For each failing theory, extracts the error message and the erroneous source snippet.
3. Calls an LLM (GPT) to propose block-level replacement fixes in the format `LINES N-M: ...`.
4. Applies the fixes to the target AFP tree and rebuilds with Isabelle.
5. Retries up to `max_attempts` times if the build still fails.
6. Logs every attempt (status, error, LLM output, elapsed time) to an Excel file.

---

## Requirements

### Python dependencies

```
openai
openpyxl
```

Install with:
```bash
pip install openai openpyxl
```

### File paths — configure `setup.py`

Edit `setup.py` at the repo root to match your local environment:

| Variable | Description |
|---|---|
| `API_KEY` | OpenAI API key |
| `SOURCE_AFP` | AFP 2024 source theories (the ones being repaired) |
| `TARGET_AFP` | AFP 2025 target tree (where fixes are applied and built) |
| `TARGET_ISABELLE` | Path to the Isabelle 2025 binary |
| `OUTPUT_ROOT` | Root of the labeled failure dataset (see below) |
| `XLSX_PATH` | Output Excel log file path |

### Error dataset — `OUTPUT_ROOT`

The pipeline expects pre-extracted build failures under `OUTPUT_ROOT` in the following layout:

```
OUTPUT_ROOT/
  <SessionName>/
    <SessionName>@<TheoryName>.json          # flat session
    <SessionName>@<SubSession>@<Theory>.json  # nested session
```

**Example** (`OUTPUT_ROOT` = `.../2025-label-2024`):
```
2025-label-2024/
  ADS_Functor/
    ADS_Functor@Generic_ADS_Construction.json
  CakeML/
    CakeML@Tests@Compiler_Test.json
```

Each JSON file has the structure:
```json
{
  "cmds": [ ... ],
  "failures": [
    {
      "pos": 339,
      "msg": "Undefined fact: \"wfPUNIVI\" (line 339)\nAt command \"apply\" (line 339)"
    },
    {
      "pos": 345,
      "msg": "Undefined fact: \"wfP_def\" (line 345)\nAt command \"by\" (line 345)"
    }
  ]
}
```

Only the `failures` array is used by the repair pipeline.

---

## Usage

Run from the repo root using the provided wrapper script:

```bash
# Repair all sessions listed under OUTPUT_ROOT
./repair.sh

# Repair a single session
./repair.sh --session ADS_Functor

# Repair a nested session
./repair.sh --session CakeML/Tests

# Pass additional context about version changes to the LLM
./repair.sh --session ADS_Functor --additional_info "wfPUNIVI was renamed to wfP_induct in Isabelle 2025"

# Preview which sessions are queued for repair
./repair.sh --show-configured-list
```

---

## Output

Results are logged to the Excel file configured as `XLSX_PATH` in `setup.py`.

Each row records one repair attempt:

| Column | Description |
|---|---|
| Session | Session path (e.g. `CakeML/Tests`) |
| Theory | Theory file name (e.g. `Compiler_Test.thy`) |
| Attempt | Attempt number (1 to `max_attempts`) |
| Error Message | The error fed to the LLM |
| LLM Output | The raw fix blocks returned by the LLM |
| Status | `success` or `fail` |
| Elapsed (s) | Wall-clock time for the final attempt |

---

## Project structure

```
auto-repair-pipeline/
├── setup.py                  # All configurable paths and API key
├── repair.sh                 # Entry point — run this
└── llm-repair/
    ├── repair_driver.py      # Top-level driver: iterates sessions and theories
    ├── llm_repairer_multiline.py  # Core repair loop (LLM call, apply, build, retry)
    ├── failure_extractor.py  # Reads JSON errors and extracts erroneous snippets
    ├── session_lister.py     # Enumerates sessions to repair from OUTPUT_ROOT
    ├── excel_creater.py      # Appends rows to the Excel output log
    └── build_error_message_extractor.py  # Parses Isabelle build output for retry errors
```


## Sample output
can be found in out/