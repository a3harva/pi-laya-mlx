"""Loads a Laya MLX checkpoint and runs predictions one at a time (MLX is not thread-safe)."""

import json
import os
import sys
import threading
from pathlib import Path

from huggingface_hub import snapshot_download

from . import evidence

# The MLX port of Laya, with its model code bundled in the repo (cavi-ai/mlx-agent, MIT).
DEFAULT_REPO = "cavi-ai/laya-MLX-8bit"
DEFAULT_SUBDIR = "typed-decisions"
# The checkpoint research/finetune.py writes; used by default when present.
FINETUNED = Path(__file__).resolve().parents[2] / "checkpoints" / "laya-toolguard"


def resolve_checkpoint(spec: str | None) -> tuple[Path, Path]:
    """(checkpoint dir, dir containing the `laya` package) for a local path or `repo[:subdir]`."""
    if not spec and (FINETUNED / "config.json").is_file():
        spec = str(FINETUNED)
    spec = spec or f"{DEFAULT_REPO}:{DEFAULT_SUBDIR}"
    local = Path(spec).expanduser()
    if local.is_dir():
        return local, _code_dir(local)
    repo, _, subdir = spec.partition(":")
    root = Path(snapshot_download(repo, ignore_patterns=["multilingual/*"] if subdir != "multilingual" else None))
    return (root / subdir if subdir else root), root


def _code_dir(checkpoint: Path) -> Path:
    """A fine-tuned checkpoint may ship its own `laya/` code; otherwise use the upstream port's."""
    for candidate in (checkpoint, checkpoint.parent):
        if (candidate / "laya" / "laya.py").is_file():
            return candidate
    return Path(snapshot_download(DEFAULT_REPO, allow_patterns=["laya/*"]))


class LayaModel:
    def __init__(self, spec: str | None = None):
        self.checkpoint, code_dir = resolve_checkpoint(spec or os.environ.get("LAYA_GUARD_MODEL"))
        if str(code_dir) not in sys.path:
            sys.path.insert(0, str(code_dir))
        from laya import load, predict  # noqa: E402  (bundled with the checkpoint)

        self._predict = predict
        self._model, self._tokenizer = load(self.checkpoint)
        self._lock = threading.Lock()
        # Checkpoints trained with CARE evidence in the state must receive it at inference too.
        config = json.loads((Path(self.checkpoint) / "config.json").read_text())
        self.uses_evidence = bool(config.get("state_evidence"))
        if self.uses_evidence and not evidence.available():
            raise RuntimeError(f"{self.checkpoint} needs CARE evidence: pip install 'laya-guard[evidence]'")

    @property
    def name(self) -> str:
        return str(self.checkpoint)

    def predict(self, state, questions: dict) -> dict:
        if self.uses_evidence and isinstance(state, dict):
            state = evidence.with_evidence(state)
        with self._lock:
            return self._predict(self._model, self._tokenizer, state, questions)


class CautiousEnsemble:
    """Several checkpoints judging together; the most cautious answer wins (block if any member would).

    Used when a newer checkpoint fixes some failures but forgets others: the union keeps both strengths at
    the cost of one forward pass per member.
    """

    def __init__(self, specs: list[str], question_id: str = "category"):
        self.members = [LayaModel(spec) for spec in specs]
        self.question_id = question_id

    @property
    def name(self) -> str:
        return " + ".join(member.name for member in self.members)

    def predict(self, state, questions: dict) -> dict:
        results = [member.predict(state, questions) for member in self.members]
        return min(results, key=lambda r: r["answers"][self.question_id]["probabilities"].get("routine", 0.0))


def load_model(spec: str | None = None):
    """One checkpoint, or a comma-separated list judged as a cautious ensemble."""
    spec = spec or os.environ.get("LAYA_GUARD_MODEL")
    if spec and "," in spec:
        return CautiousEnsemble([part.strip() for part in spec.split(",") if part.strip()])
    return LayaModel(spec)
