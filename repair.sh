#!/usr/bin/env bash
# Usage:
#   ./repair.sh                              # repair all sessions in SESSIONS_TO_REPAIR
#   ./repair.sh --session ADS_Functor        # repair one session
#   ./repair.sh --additional_info "..."      # pass extra context to the LLM
#   ./repair.sh --show-configured-list       # print the session list and exit

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 "$SCRIPT_DIR/llm-repair/repair_driver.py" "$@"
