#!/usr/bin/env bash
# Sets up the laya-guard server: Python env, MLX dependencies, and the fine-tuned checkpoint from Hugging Face
# (a3harva/laya-toolguard-mlx). With --finetune, trains it locally instead with the v8 recipe (~1 h on an M-series Mac)
# into checkpoints/laya-toolguard, which the server then prefers.
set -euo pipefail
cd "$(dirname "$0")/.."

[[ "$(uname -sm)" == "Darwin arm64" ]] || { echo "laya-guard needs an Apple Silicon Mac (MLX)"; exit 1; }
command -v uv >/dev/null || { echo "install uv first: https://docs.astral.sh/uv/"; exit 1; }

uv venv --python 3.12 server/.venv
uv pip install --python server/.venv/bin/python -e "server[dev,evidence]"

if [[ "${1:-}" == "--finetune" ]]; then
  server/.venv/bin/python research/gen_templates.py
  scripts/train-guarded.sh --out checkpoints/laya-toolguard --evidence --epochs 4 --train-layers 14 --batch 4
fi
# Loads the default checkpoint (downloading it if needed) and scores the hand-written test set.
USE_TF=0 server/.venv/bin/python research/evaluate.py
echo "done: run 'pi -e $(pwd)/extension'"
