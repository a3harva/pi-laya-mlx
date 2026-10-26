"""Structural facts about shell commands, from CARE's static engine (prisma-research/CARE, MIT).

Laya judges text; it cannot parse shell. CARE canonicalizes a command and labels each part with its effect
(READ_ONLY, DESTRUCTIVE, NETWORK_FETCH, EXECUTION_CHAIN, ...), structural flags (output piped into an
interpreter, eval, command substitution), critical paths and provenance-backed risk rules. Those facts go into
the state Laya judges as `evidence`, so `grep -rn 'rm -rf ~' docs/` reads as a READ_ONLY search, not a deletion.
Laya still decides. Only checkpoints trained with evidence (config `state_evidence: true`) receive it.

Optional dependency: pip install "laya-guard[evidence]".
"""

from functools import lru_cache

try:
    from care import CAREEngine
except ImportError:  # evidence is optional; checkpoints that need it fail loudly in model.py
    CAREEngine = None

MAX_PARTS = 8
FLAGS = {"has_pipe_to_exec": "output_piped_to_interpreter", "has_eval": "eval", "has_command_sub": "command_substitution"}


def available() -> bool:
    return CAREEngine is not None


@lru_cache(maxsize=8)
def _engine(cwd: str):
    return CAREEngine(workspace=cwd or ".")


def shell_evidence(command: str, cwd: str | None) -> dict:
    """Compact, deterministic facts about one shell command."""
    details = _engine(cwd or ".").analyze(command).details
    effects = []
    for part in details.get("semantic", [])[:MAX_PARTS]:
        head = (part.get("atom") or "").split(" ", 1)[0][:40]
        effects.append(f"{head}: {part.get('class')}")
    evidence = {"effects": effects}
    flags = [name for key, name in FLAGS.items() if details.get("ast", {}).get(key)]
    if flags:
        evidence["structure"] = flags
    critical = details.get("path", {}).get("triggered") or []
    if critical:
        evidence["critical_paths"] = critical[:5]
    rules = details.get("pattern", {}).get("matches") or []
    if rules:
        evidence["risk_rules"] = rules[:5]
    return evidence


def with_evidence(state: dict) -> dict:
    """The state plus `evidence` for shell commands; other tools are returned unchanged."""
    command = state.get("input", {}).get("command") if state.get("tool") == "bash" else None
    if not isinstance(command, str) or not command.strip():
        return state
    return {**state, "evidence": shell_evidence(command, state.get("cwd"))}
