#!/usr/bin/env bash
set -euo pipefail

BASE_MODEL="${BASE_MODEL:-microsoft/Phi-3.5-mini-instruct}"
LORA_DIR="${LORA_DIR:-expense-agent-lora}"
MERGED_DIR="${MERGED_DIR:-expense-agent-merged}"
LLAMA_CPP_DIR="${LLAMA_CPP_DIR:-$HOME/src/llama.cpp}"
OUT_DIR="${OUT_DIR:-/mnt/nas/ai/models/expense-agent}"
F16_GGUF="$OUT_DIR/expense-agent-f16.gguf"
Q4_GGUF="$OUT_DIR/expense-agent-q4_k_m.gguf"

mkdir -p "$OUT_DIR"

python3 merge_lora.py \
  --base-model "$BASE_MODEL" \
  --lora "$LORA_DIR" \
  --output "$MERGED_DIR"

python3 "$LLAMA_CPP_DIR/convert_hf_to_gguf.py" "$MERGED_DIR" --outfile "$F16_GGUF" --outtype f16
"$LLAMA_CPP_DIR/build/bin/llama-quantize" "$F16_GGUF" "$Q4_GGUF" Q4_K_M

cat > "$OUT_DIR/README.txt" <<EOF
Expense Agent GGUF model
Model: $Q4_GGUF
Start on Raspberry Pi with:
  llama-server -m $Q4_GGUF -c 4096 --host 0.0.0.0 --port 8080
EOF

echo "Wrote quantized model to $Q4_GGUF"
