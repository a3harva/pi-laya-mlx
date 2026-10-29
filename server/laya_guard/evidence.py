"""Structural facts about shell commands, from CARE's static engine (prisma-research/CARE, MIT).

Laya judges text; it cannot parse shell. CARE canonicalizes a command and labels each part with its effect
(READ_ONLY, DESTRUCTIVE, NETWORK_FETCH, EXECUTION_CHAIN, ...), structural flags (output piped into an
interpreter, eval, command substitution), critical paths and provenance-backed risk rules. Those facts go into
the state Laya judges as `evidence`, so `grep -rn 'rm -rf ~' docs/` reads as a READ_ONLY search, not a deletion.
Laya still decides. Only checkpoints trained with evidence (config `state_evidence: true`) receive it.

Optional dependency: pip install "laya-guard[evidence]".
"""

import shlex
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


FIND_EXEC = {"-exec", "-execdir", "-ok", "-okdir"}
# xargs options that take a separate value (`-I {}`, `-n 1`); `-I{}` style is one token.
XARGS_VALUE_OPTS = {"-I", "-J", "-L", "-n", "-P", "-s", "-d", "-E", "-a", "-R", "-S"}


def _runs(atom: str) -> list[str]:
    """Commands that `find` or `xargs` run on their own (CARE classifies both by name, as READ_ONLY / UNKNOWN).

    `-delete` is reported as `rm`, since that is its effect.
    """
    try:
        words = shlex.split(atom)
    except ValueError:
        words = atom.split()
    if not words:
        return []
    if words[0] == "find":
        runs = ["rm {}"] if "-delete" in words else []
        for i, word in enumerate(words):
            if word in FIND_EXEC:
                end = next((j for j in range(i + 1, len(words)) if words[j] in (";", "+")), len(words))
                runs.append(" ".join(words[i + 1 : end]))
        return runs
    if words[0] == "xargs":
        i = 1
        while i < len(words) and words[i].startswith("-"):
            i += 2 if words[i] in XARGS_VALUE_OPTS else 1
        return [" ".join(words[i:])] if i < len(words) else []
    return []


def _effect(engine, part: dict) -> str:
    """`head: CLASS` for one command part; for find/xargs, the class of what they run when that is riskier."""
    atom = part.get("atom") or ""
    head, cls = atom.split(" ", 1)[0][:40], part.get("class")
    for inner in _runs(atom):
        inner_parts = engine.analyze(inner).details.get("semantic", [])
        inner_cls = inner_parts[0].get("class") if inner_parts else None
        if inner_cls and inner_cls not in ("READ_ONLY", "UNKNOWN"):
            cls = inner_cls
    return f"{head}: {cls}"


def shell_evidence(command: str, cwd: str | None) -> dict:
    """Compact, deterministic facts about one shell command."""
    engine = _engine(cwd or ".")
    details = engine.analyze(command).details
    effects = [_effect(engine, part) for part in details.get("semantic", [])[:MAX_PARTS]]
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
