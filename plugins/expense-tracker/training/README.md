# Windows WSL2 to Raspberry Pi NAS Model Workflow

Goal: fine-tune an open-source model on your powerful Windows PC, export a quantized GGUF file to `/mnt/nas`, and run inference on the Raspberry Pi without storing model files on the SD card.

## Recommended Model

Default: `microsoft/Phi-3.5-mini-instruct`

- License: MIT.
- Size: 3.8B parameters.
- Good fit for your Windows PC: 32GB RAM, 12GB RTX VRAM, 500GB SSD with QLoRA.
- Good fit for Raspberry Pi inference after Q4 GGUF quantization, with the model stored under `/mnt/nas`.

You can swap in another open model if its license allows your use and llama.cpp supports conversion to GGUF.

## WSL2 Setup

Run inside WSL2 Ubuntu:

```bash
cd homelab_os/plugins/expense-tracker/training
bash prepare_wsl2.sh
source .venv/bin/activate
```

Build llama.cpp with CUDA on the Windows PC:

```bash
bash build_llamacpp_wsl2.sh
```

## Create a Large Synthetic Dataset

The dataset teaches the model how users ask expense questions and how to choose app tools. It does not need to memorize private transactions.

```bash
python3 create_qna_dataset.py \
  --sqlite /mnt/nas/homelab/runtime/expense-tracker/data/expenses.db \
  --synthetic-count 50000 \
  --out expense_qna_train.jsonl
```

If the SQLite DB is not mounted in WSL2, omit `--sqlite`; the generator will use common expense categories.

## Fine-Tune With QLoRA

```bash
python3 finetune_qlora.py \
  --base-model microsoft/Phi-3.5-mini-instruct \
  --dataset expense_qna_train.jsonl \
  --output expense-agent-lora \
  --epochs 2 \
  --batch-size 1
```

For 12GB VRAM, keep batch size at `1` or `2`. The script uses 4-bit QLoRA and gradient accumulation.

## Merge, Convert, Quantize, and Save to NAS

```bash
OUT_DIR=/mnt/nas/ai/models/expense-agent \
LLAMA_CPP_DIR=$HOME/src/llama.cpp \
bash export_to_gguf.sh
```

This writes:

```text
/mnt/nas/ai/models/expense-agent/expense-agent-q4_k_m.gguf
```

The final model lives on the external HDD mounted at `/mnt/nas`, not on the Raspberry Pi SD card.

## Raspberry Pi Inference Without SD Card Model Storage

Store the llama.cpp binary on NAS too:

```bash
mkdir -p /mnt/nas/ai/bin
cp /path/to/llama-server /mnt/nas/ai/bin/llama-server
chmod +x /mnt/nas/ai/bin/llama-server
```

Install the service:

```bash
sudo cp raspi_llama_server.service /etc/systemd/system/expense-agent.service
sudo systemctl daemon-reload
sudo systemctl enable --now expense-agent.service
```

The service reads the model from:

```text
/mnt/nas/ai/models/expense-agent/expense-agent-q4_k_m.gguf
```

and exposes an OpenAI-compatible local API at:

```text
http://127.0.0.1:8080/v1/chat/completions
```

Expense Tracker calls that through Docker using:

```yaml
LLM_BACKEND: "llama_cpp"
LLM_BASE_URL: "http://host.docker.internal:8080"
LLM_MODEL: "expense-agent"
```

## Why This Fits Low Pi Storage

- Model file: `/mnt/nas/ai/models/...`
- Optional llama-server binary: `/mnt/nas/ai/bin/...`
- Expense database: `/mnt/nas/homelab/runtime/...`
- Pi SD card: only OS and small service/config files.
