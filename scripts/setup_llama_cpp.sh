#!/usr/bin/env bash
set -euo pipefail
if command -v llama-server >/dev/null 2>&1; then
  llama-server --version
  exit 0
fi
if command -v brew >/dev/null 2>&1; then
  brew install llama.cpp
  llama-server --version
  exit 0
fi
echo "Install llama.cpp with llama-server from https://github.com/ggml-org/llama.cpp and put it on PATH" >&2
exit 1
