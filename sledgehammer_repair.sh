#!/usr/bin/env bash
# Usage:
#   ./sledgehammer_repair.sh                              # repair all sessions in SESSIONS_TO_REPAIR
#   ./sledgehammer_repair.sh --session ADS_Functor        # repair one session
#   ./sledgehammer_repair.sh --show-configured-list       # print the session list and exit

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 "$SCRIPT_DIR/sledgehammer-repair/driver.py" "$@"
