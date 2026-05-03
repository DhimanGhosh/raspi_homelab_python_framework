#!/usr/bin/env bash
set -euo pipefail

sudo apt update
sudo apt install -y git build-essential cmake ninja-build python3-venv python3-pip rsync
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements-train.txt

echo "WSL2 training environment is ready. Activate it with: source .venv/bin/activate"
