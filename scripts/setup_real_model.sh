#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
model="models/qwen2.5-0.5b-instruct-q4_k_m.gguf"
expected="74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db"
url="https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"
mkdir -p models
if [[ -f "$model" ]] && [[ "$(shasum -a 256 "$model" | cut -d ' ' -f 1)" == "$expected" ]]; then
  echo "Pinned model already present: $model"
  exit 0
fi
curl --fail --location --retry 3 --output "$model.part" "$url"
actual="$(shasum -a 256 "$model.part" | cut -d ' ' -f 1)"
if [[ "$actual" != "$expected" ]]; then
  echo "Model SHA-256 mismatch: expected $expected, got $actual" >&2
  exit 1
fi
mv "$model.part" "$model"
echo "Verified pinned model: $model ($actual)"
