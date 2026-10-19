#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 1 ]]; then
  echo "usage: $0 CONFIG [--resume] [--fail-fast]" >&2
  exit 2
fi
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$script_dir/.."
config="$1"
shift
python3 -m saferoll.cli campaign-run --config "$config" "$@"
