#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 -m saferoll.cli validate --run-dir "${1:?Usage: validate_run.sh runs/<run-id>}"
