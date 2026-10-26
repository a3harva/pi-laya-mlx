#!/usr/bin/env bash
# Sets up the laya-guard server: Python env, MLX dependencies, base model, and (unless --skip-finetune) the
# fine-tuned tool-call checkpoint in checkpoints/laya-toolguard, trained with the v8 recipe (~1 h on an M-series Mac).
set -euo pipefail
cd "$(dirname "$0")/.."

[[ "$(uname -sm)" == "Darwin arm64" ]] || { echo "laya-guard needs an Apple Silicon Mac (MLX)"; exit 1; }
command -v uv >/dev/null || { echo "install uv first: https://docs.astral.sh/uv/"; exit 1; }

uv venv --python 3.12 server/.venv
uv pip install --python server/.venv/bin/python -e "server[dev,evidence]"
USE_TF=0 server/.venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('cavi-ai/laya-MLX-8bit', ignore_patterns=['multilingual/*'])"

if [[ "${1:-}" != "--skip-finetune" && ! -f checkpoints/laya-toolguard/config.json ]]; then
  echo "fine-tuning Laya on tool calls (pass --skip-finetune to use the zero-shot base model)"
  server/.venv/bin/python research/gen_templates.py
  scripts/train-guarded.sh --out checkpoints/laya-toolguard --evidence --epochs 4 --train-layers 14 --batch 4
fi
USE_TF=0 server/.venv/bin/python research/evaluate.py "$( [[ -f checkpoints/laya-toolguard/config.json ]] && echo checkpoints/laya-toolguard )"
echo "done: run 'pi -e $(pwd)/extension'"
