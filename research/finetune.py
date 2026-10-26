"""Fine-tune Laya (MLX) to classify coding-agent tool calls into the guard's risk categories.

Trains on the exact state rendering and question the server uses (laya_guard.policy), with a
cross-entropy loss on the category question's option logits. The result is a normal Laya MLX
checkpoint (8-bit, same layout as cavi-ai/laya-MLX-8bit) that `laya_guard` loads unchanged.

  python research/finetune.py --out checkpoints/laya-toolguard
"""

import argparse
import json
import math
import random
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

import mlx.core as mx
import mlx.nn as nn
import mlx.optimizers as optim
from mlx.utils import tree_flatten, tree_unflatten

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
from laya_guard import evidence, policy  # noqa: E402
from laya_guard.model import resolve_checkpoint  # noqa: E402

DATA = ROOT / "research" / "data"
LABELS = list(policy.CATEGORIES)


# ----------------------------------------------------------------------------- data


def parse_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip().rstrip(",")
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("category") in LABELS and row.get("tool") in {"bash", "write", "edit", "read"} and isinstance(row.get("input"), dict):
            rows.append({"category": row["category"], "tool": row["tool"], "input": row["input"], "cwd": row.get("cwd")})
    return rows


def key(row: dict) -> str:
    return json.dumps([row["tool"], row["input"]], sort_keys=True)


def held_out_rows() -> dict[str, list[dict]]:
    """Evaluation sets that are never trained on: the hand-written set and every red-team round's probes."""
    sets = {"eval": [{"category": "routine" if r["label"] == "safe" else "destroy", "tool": r["tool"], "input": r["input"],
                      "cwd": "/Users/dev/project"} for r in map(json.loads, (DATA / "eval.jsonl").read_text().splitlines())]}
    probes = []
    for path in sorted((DATA / "redteam" / "probes").glob("*.jsonl")):
        probes += [{"category": r["expected"], "tool": r["tool"], "input": r["input"], "cwd": r.get("cwd")}
                   for r in map(json.loads, path.read_text().splitlines()) if r.get("expected") in LABELS]
    if probes:
        sets["redteam"] = probes
    # Near-variants of classic attacks: a fixed gate against forgetting them (v4 allowed `terraform destroy`).
    canonical = DATA / "canonical.jsonl"
    if canonical.exists():
        sets["canonical"] = [{"category": r["expected"], "tool": r["tool"], "input": r["input"], "cwd": "/Users/dev/project"}
                             for r in map(json.loads, canonical.read_text().splitlines())]
    return sets


def load_data(seed: int, val_fraction: float, redteam_repeat: int = 1):
    held_out = {key(row) for rows in held_out_rows().values() for row in rows}
    rows, redteam, seen = [], [], set(held_out)
    sources = [(p, False) for p in sorted((DATA / "gen").glob("*.jsonl")) + [DATA / "templates.jsonl"]]
    sources += [(p, True) for p in sorted((DATA / "redteam" / "train").glob("*.jsonl"))]
    for path, reviewed in sources:
        for row in parse_rows(path):
            if key(row) not in seen:
                seen.add(key(row))
                (redteam if reviewed else rows).append(row)
    rng = random.Random(seed)
    rng.shuffle(rows)
    split = int(len(rows) * (1 - val_fraction))
    # Reviewed red-team rows are few but target known failures: repeat them in training only.
    train = rows[:split] + redteam * redteam_repeat
    rng.shuffle(train)
    return train, rows[split:]


def encode(rows: list[dict], tok, args, build_sequence, internal_question, use_evidence: bool = False) -> list[dict]:
    question = internal_question(policy.QUESTIONS[policy.QUESTION_ID])
    items = []
    for row in rows:
        state = policy.render_state(row["tool"], row["input"], row.get("cwd"))
        if use_evidence:
            state = evidence.with_evidence(state)
        ids, markers = build_sequence(tok, state, question, args.max_len, args.head_max_len)
        items.append({"ids": ids, "markers": markers, "qtype": 0, "label": LABELS.index(row["category"])})
    return items


def batches(items: list[dict], size: int, shuffle: bool, rng: random.Random):
    """Length-bucketed batches, so padding stays small."""
    order = sorted(range(len(items)), key=lambda i: len(items[i]["ids"]))
    groups = [order[i:i + size] for i in range(0, len(order), size)]
    if shuffle:
        rng.shuffle(groups)
    yield from ([items[i] for i in group] for group in groups)


# ----------------------------------------------------------------------------- model


def dequantize(model: nn.Module) -> list[str]:
    """Replace QuantizedLinear layers with float Linear layers so they can train; returns their names."""
    replaced = []
    for name, module in model.named_modules():
        if isinstance(module, nn.QuantizedLinear):
            weight = mx.dequantize(module.weight, module.scales, module.biases, module.group_size, module.bits)
            linear = nn.Linear(weight.shape[1], weight.shape[0], bias="bias" in module)
            linear.weight = weight.astype(mx.float32)
            if "bias" in module:
                linear.bias = module.bias.astype(mx.float32)
            replaced.append((name, linear))
    model.update_modules(tree_unflatten(replaced))
    model.set_dtype(mx.float32)
    return [name for name, _ in replaced]


def set_trainable(model, train_layers: int):
    model.freeze()
    for layer in model.encoder.layers[-train_layers:]:
        layer.unfreeze()
    model.encoder.final_norm.unfreeze()
    for part in (model.head, model.type_emb, model.scorer):
        part.unfreeze()


def evaluate(model, items, batch_fn, size=16):
    """Option probabilities (temperature 1) for every item."""
    probs = []
    for group in batches(items, size, False, random.Random(0)):
        logits, _ = model(*batch_fn(group, PAD_ID))
        p = mx.softmax(logits[:, :len(LABELS)], axis=-1)
        mx.eval(p)
        probs.extend(zip([item["label"] for item in group], p.tolist(), [len(item["ids"]) for item in group]))
    return probs


def metrics(probs, temperature=1.0, threshold=0.5):
    nll, correct, tp, fp, tn, fn = 0.0, 0, 0, 0, 0, 0
    for label, p, _ in probs:
        if temperature != 1.0:
            z = [math.log(max(v, 1e-12)) / temperature for v in p]
            top = max(z)
            w = [math.exp(v - top) for v in z]
            p = [v / sum(w) for v in w]
        nll -= math.log(max(p[label], 1e-12))
        correct += p.index(max(p)) == label
        unsafe_pred, unsafe_true = 1 - p[0] >= threshold, label != 0
        tp += unsafe_pred and unsafe_true
        fp += unsafe_pred and not unsafe_true
        tn += not unsafe_pred and not unsafe_true
        fn += not unsafe_pred and unsafe_true
    n = len(probs)
    return {"nll": nll / n, "acc6": correct / n, "bin_acc": (tp + tn) / n, "fpr": fp / max(1, fp + tn), "fnr": fn / max(1, fn + tp)}


# ----------------------------------------------------------------------------- main


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="cavi-ai/laya-MLX-8bit:typed-decisions")
    parser.add_argument("--out", default=str(ROOT / "checkpoints" / "laya-toolguard"))
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--lr", type=float, default=3e-5)
    parser.add_argument("--train-layers", type=int, default=8)
    parser.add_argument("--routine-weight", type=float, default=2.0, help="extra loss weight on routine rows, to keep false positives low")
    parser.add_argument("--val", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--redteam-repeat", type=int, default=3, help="times each reviewed red-team row appears in training")
    parser.add_argument("--evidence", action="store_true", help="add CARE structural evidence to shell states (needs laya-guard[evidence])")
    parser.add_argument("--label-smoothing", type=float, default=0.1, help="keeps scores graded instead of saturating at 1.00")
    opts = parser.parse_args()

    checkpoint, code_dir = resolve_checkpoint(opts.base)
    sys.path.insert(0, str(code_dir))
    from laya import load
    from laya.laya import batch as batch_fn, build_sequence, internal_question

    model, tok = load(checkpoint)
    global PAD_ID
    PAD_ID = tok.pad_id
    quantized = dequantize(model)
    set_trainable(model, opts.train_layers)
    trainable = sum(v.size for _, v in tree_flatten(model.trainable_parameters()))
    print(f"base {checkpoint}\n{len(quantized)} linear layers dequantized; {trainable / 1e6:.0f}M trainable params")

    train_rows, val_rows = load_data(opts.seed, opts.val, opts.redteam_repeat)
    held_sets = held_out_rows()
    print(f"train {len(train_rows)} {dict(Counter(r['category'] for r in train_rows))}\nval {len(val_rows)}  "
          + "  ".join(f"held-out {name} {len(rows)}" for name, rows in held_sets.items()))
    train = encode(train_rows, tok, model.args, build_sequence, internal_question, opts.evidence)
    val = encode(val_rows, tok, model.args, build_sequence, internal_question, opts.evidence)
    held = {name: encode(rows, tok, model.args, build_sequence, internal_question, opts.evidence) for name, rows in held_sets.items()}

    counts = Counter(item["label"] for item in train)
    class_weight = mx.array([(len(train) / (len(LABELS) * counts[i])) * (opts.routine_weight if i == 0 else 1.0) for i in range(len(LABELS))])

    def loss_fn(model, inputs, labels):
        logits, _ = model(*inputs)
        logits = logits[:, :len(LABELS)]
        ce = nn.losses.cross_entropy(logits, labels, reduction="none", label_smoothing=opts.label_smoothing)
        w = class_weight[labels]
        return (ce * w).sum() / w.sum()

    steps = opts.epochs * math.ceil(len(train) / opts.batch)
    schedule = optim.join_schedules([optim.linear_schedule(0, opts.lr, max(1, steps // 10)), optim.cosine_decay(opts.lr, steps)], [max(1, steps // 10)])
    optimizer = optim.AdamW(learning_rate=schedule, weight_decay=0.01)
    grad_fn = nn.value_and_grad(model, loss_fn)
    rng = random.Random(opts.seed)

    def report(tag):
        model.eval()
        v = metrics(evaluate(model, val, batch_fn))
        line = f"[{tag}] val nll={v['nll']:.3f} acc6={v['acc6']:.3f} bin={v['bin_acc']:.3f} fpr={v['fpr']:.3f} fnr={v['fnr']:.3f}"
        for name, items in held.items():
            h = metrics(evaluate(model, items, batch_fn))
            line += f" | {name} bin={h['bin_acc']:.3f} fpr={h['fpr']:.3f} fnr={h['fnr']:.3f}"
        print(line, flush=True)
        model.train()

    report("base")
    model.train()
    step, started = 0, time.time()
    for epoch in range(opts.epochs):
        running = 0.0
        for group in batches(train, opts.batch, True, rng):
            inputs = batch_fn(group, tok.pad_id)
            labels = mx.array([item["label"] for item in group])
            loss, grads = grad_fn(model, inputs, labels)
            optimizer.update(model, grads)
            mx.eval(model.parameters(), optimizer.state, loss)
            running += loss.item()
            step += 1
            if step % 50 == 0:
                print(f"  step {step}/{steps} loss {running / 50:.4f} ({time.time() - started:.0f}s)", flush=True)
                running = 0.0
        report(f"epoch {epoch + 1}")

    # Calibrate: one temperature for the 6-option choice bucket, fit on the validation split.
    model.eval()
    val_probs = evaluate(model, val, batch_fn)
    temperature = min((metrics(val_probs, t)["nll"], t) for t in [x / 20 for x in range(10, 81)])[1]
    print(f"fitted temperature {temperature:.2f}: val nll {metrics(val_probs)['nll']:.3f} -> {metrics(val_probs, temperature)['nll']:.3f}")

    save(model, quantized, checkpoint, code_dir, Path(opts.out), temperature, opts)


def save(model, quantized: list[str], base: Path, code_dir: Path, out: Path, temperature: float, opts):
    config = json.loads((base / "config.json").read_text())
    q = config.get("quantization")
    if q:
        names = set(quantized)
        nn.quantize(model, group_size=q["group_size"], bits=q["bits"], class_predicate=lambda name, module: name in names)
    # Keep float16 for everything that is not quantized, as the base checkpoint does.
    weights = {k: (v.astype(mx.float16) if v.dtype == mx.float32 else v) for k, v in tree_flatten(model.parameters())}
    out.mkdir(parents=True, exist_ok=True)
    mx.save_safetensors(str(out / "model.safetensors"), weights)
    buckets = dict(config.get("temperature_by_options", {}))
    buckets[f"choice:{'6-10' if len(LABELS) > 5 else '3-5'}"] = temperature
    config.update(temperature_by_options=buckets, model_name="laya-toolguard", state_evidence=bool(opts.evidence),
                  finetune={"base": str(opts.base), "labels": LABELS, "epochs": opts.epochs, "lr": opts.lr, "train_layers": opts.train_layers})
    (out / "config.json").write_text(json.dumps(config, indent=2))
    for name in ("tokenizer.json", "tokenizer_config.json"):
        shutil.copy(base / name, out / name)
    shutil.copytree(code_dir / "laya", out / "laya", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("LICENSE",):
        if (code_dir / name).exists():
            shutil.copy(code_dir / name, out / name)
    print(f"saved {out}")


if __name__ == "__main__":
    main()
