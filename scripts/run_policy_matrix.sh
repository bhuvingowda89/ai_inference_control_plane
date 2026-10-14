#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 -m saferoll.cli policy-matrix --config "${1:?Usage: run_policy_matrix.sh configs/<scenario>.yaml}" "${@:2}"
