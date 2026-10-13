#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 -m saferoll.cli run --config "${1:-configs/phase4a-smoke.yaml}"
