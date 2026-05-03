#!/usr/bin/env bash
set -euo pipefail

LLAMA_CPP_DIR="${LLAMA_CPP_DIR:-$HOME/src/llama.cpp}"

if [ ! -d "$LLAMA_CPP_DIR/.git" ]; then
  mkdir -p "$(dirname "$LLAMA_CPP_DIR")"
  git clone https://github.com/ggml-org/llama.cpp.git "$LLAMA_CPP_DIR"
fi

cmake -S "$LLAMA_CPP_DIR" -B "$LLAMA_CPP_DIR/build" -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release
cmake --build "$LLAMA_CPP_DIR/build" --config Release -j"$(nproc)"

echo "llama.cpp built at $LLAMA_CPP_DIR/build"
