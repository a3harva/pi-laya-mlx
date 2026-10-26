# pi-laya-mlx

**Screen every tool call your [pi](https://pi.dev) coding agent makes with [Laya](https://huggingface.co/convaiinnovations/laya), a calibrated decision model running locally on Apple Silicon via MLX.** Calls Laya judges unsafe are blocked before they run, and the model gets the reason back as the tool result, so it can change course instead of retrying.

```
model ──tool call──▶ pi ──tool_call hook──▶ extension ──POST /v1/guard──▶ laya-guard (MLX, localhost)
                      ▲                         │                              │
                      │       allow ◀───────────┤◀── {decision, risk, category, reason}
                      └── "Blocked by the Laya safety guard: it looks like it deletes data outside
                           the project ... pick a narrower, reversible alternative" (tool result)
```

- **Local and private.** Nothing leaves the machine. One check takes ~20-60 ms on an M-series Mac.
- **Gives the model a reason.** Blocks come back to the model as a tool result naming the risk category (destroy, exfiltrate, remote_code, system, production), so it explains or picks a safer path.
- **Fine-tuned for tool calls.** Ships a training script that fine-tunes Laya in MLX on labeled tool calls (`research/`). Zero-shot Laya is a weak judge of shell commands; the fine-tuned checkpoint is much better (see [Results](#results)).
- **Jev-compatible.** The server also exposes `POST /v1/systemone`, the TypeSafe Jev request shape, for any other Laya question.

## Requirements

- Apple Silicon Mac, macOS 14+
- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- pi 1.4+

## Install

```bash
git clone https://github.com/a3harva/pi-laya-mlx && cd pi-laya-mlx
./scripts/setup.sh          # creates server/.venv, installs MLX deps, downloads the base model, fine-tunes (~1 h)
```

Try it for one session:

```bash
pi -e ./extension
```

Install it for every session:

```bash
pi install ./            # or: add the absolute path of this folder to "packages" in ~/.pi/agent/settings.json
```

The extension starts the server on first use (a detached daemon on `127.0.0.1:8723`, shared by all pi sessions, log in `~/.cache/laya-guard/server.log`). To run it yourself:

```bash
server/.venv/bin/laya-guard-serve --port 8723
```

## Use

The footer shows `laya ✓12 ✗1` (allowed, blocked). `/laya` shows status; `/laya off` and `/laya on` toggle screening for the session.

When a call is blocked, pi shows a warning and the model receives:

> Blocked by the Laya safety guard: it looks like it deletes or overwrites data outside the project (home directory, disk, or git history) (risk 0.97 >= threshold 0.50; top category 'destroy' p=0.95). Do not retry the same action or work around the guard with an equivalent command. If the step is needed, pick a narrower, reversible alternative that stays inside the project, or explain to the user why it is needed and let them run it themselves.

### Configuration

Extension (environment variables):

| Variable | Default | |
|---|---|---|
| `LAYA_GUARD_URL` | `http://127.0.0.1:8723` | server URL |
| `LAYA_GUARD_THRESHOLD` | server's | block when risk ≥ this |
| `LAYA_GUARD_FAIL` | `closed` | `closed` blocks calls while the server is unreachable; `open` lets them run |
| `LAYA_GUARD_SKIP` | none | comma-separated tool names never screened, e.g. `read,grep,find,ls` |
| `LAYA_GUARD_CONFIRM` | off | `1`: in the TUI, ask the user to allow a flagged call instead of blocking outright |
| `LAYA_GUARD_AUTOSTART` | on | `0`: never start the server |
| `LAYA_GUARD_API_KEY` | none | bearer token, if the server requires one |
| `LAYA_GUARD_TIMEOUT_MS` | `15000` | how long one check may take before it counts as unreachable |

Server (`laya-guard-serve` flags or environment variables):

| Variable | Default | |
|---|---|---|
| `LAYA_GUARD_MODEL` / `--model` | fine-tuned checkpoint if present, else `cavi-ai/laya-MLX-8bit:typed-decisions` | local dir, `hf-repo:subdir`, or a comma-separated list (cautious ensemble) |
| `LAYA_GUARD_THRESHOLD` / `--threshold` | `0.5` | |
| `LAYA_GUARD_HOST`, `LAYA_GUARD_PORT` | `127.0.0.1`, `8723` | |
| `LAYA_GUARD_API_KEY` | none | require `Authorization: Bearer <key>` |

### API

```bash
curl -s localhost:8723/v1/guard -H 'content-type: application/json' \
  -d '{"tool":"bash","input":{"command":"rm -rf ~"},"cwd":"/Users/me/proj"}'
# {"decision":"block","risk":0.99,"category":"destroy","probabilities":{...},"reason":"Blocked by ...","latency_ms":31.2}
```

`POST /v1/systemone` takes `{state, questions}` in the Laya / Jev shape (`choice`, `score`, `noul`/`bool`). `GET /health` reports the model and threshold.

## How it works

1. pi fires `tool_call` before every tool runs, including calls nested in other tools. The extension sends the tool name, its arguments and the working directory to the server.
2. The server renders that as a JSON state and asks Laya one `choice` question, *"Which best describes what this action does?"*, with six options: `routine` and five risk categories (`server/laya_guard/policy.py`). Laya scores every option in a single forward pass and returns calibrated probabilities.
3. Risk is `1 - p(routine)`. At or above the threshold the call is blocked, and the reason names the most likely risk category.
4. The extension returns `{ block: true, reason }`, which pi delivers to the model as the tool result. Every verdict is also appended to the session (`laya-guard` entries) for auditing.

Laya never generates text, so there is nothing to parse and a prompt injection inside a command cannot make it "say" allow; it can only shift probabilities, which is what the fine-tuning data includes hard cases for.

## Results

Zero-shot Laya is a weak judge of shell commands (it blocked `npm test`). It was fine-tuned in eight versions, with a
red-team loop between them: a second pi agent (DeepSeek, locked down so it can only score text, never run it) wrote
adversarial probes and training rows each round; every plan and label was reviewed before use. All numbers are on
calls never trained on. Full history: [`research/REDTEAM_LOG.md`](research/REDTEAM_LOG.md).

| model | synthetic val (324): risky blocked / routine allowed | held-out (617) | final exam (107) | latency |
|---|---|---|---|---|
| v1 (first fine-tune) | | round-1 probes: 67.6% overall | | ~50 ms |
| v8 (evidence, single model) | 98.3% / 97.9% | 91.1% / 92.7% | 82.4% / 91.1% | ~60-85 ms |
| **v2+v8 cautious ensemble** | 98.9% / 95.2% | **93.4%** / 77.0% | **90.2%** / 73.2% | ~2x |

The synthetic validation split is LLM- and template-written data held out from training; it is easy, so it mostly
shows the model learned the categories. The held-out and final-exam sets are adversarial on purpose (hard negatives such as `grep -rn 'rm -rf ~' docs/`);
on the plain hand-written set v8 allows 100% of routine calls and blocks 92-95% of risky ones.

External comparison on a CARE-style benchmark (549 commands from Atomic Red Team, RedCode-Exec and NL2Bash, rebuilt
because CARE did not release its split):

| | F1 | detection | false positives |
|---|---|---|---|
| CARE static engine | 65.9% | 53.6% | 6.1% |
| Laya v8 | 62.8% | 51.4% | 8.2% |
| Laya v8 OR CARE static | **73.0%** | 67.7% | 11.9% |

### Ensembles and evidence

- `LAYA_GUARD_MODEL=checkpoints/v2,checkpoints/v8` runs a cautious ensemble: a call is blocked if any member would
  block it. Newer checkpoints fix many red-team failures but forget a few classic attacks; the union keeps both.
- `pip install "laya-guard[evidence]"` adds structural facts from [CARE](https://github.com/prisma-research/CARE)'s
  static shell analyzer (effect class per command part, output piped to an interpreter, critical paths, risk rules)
  to the state Laya judges. Checkpoints trained with it (`state_evidence: true` in their config, e.g. v8) require it.
- Long commands, file contents and edits are judged in overlapping 1,200-character windows; the riskiest decides.

## Fine-tuning

```bash
server/.venv/bin/python research/gen_templates.py          # template rows -> research/data/templates.jsonl
research/gen_llm.sh destroy A                               # LLM-written rows via pi (any provider) -> research/data/gen/
# v8 recipe; the watchdog stops training if free memory drops below 10% (16 GB Macs need --batch 4)
scripts/train-guarded.sh --out checkpoints/laya-toolguard --evidence --epochs 4 --train-layers 14 --batch 4
server/.venv/bin/python research/evaluate.py checkpoints/laya-toolguard
```

Data (`research/data/`):

| file | rows | used for |
|---|---|---|
| `gen/*.jsonl`, `templates.jsonl` | ~3,500 | training (10% held out as the synthetic validation split) |
| `redteam/train/*.jsonl` | ~1,200 | training; reviewed red-team rows, repeated 3x |
| `eval.jsonl` | 128 | hand-written test set, never trained on |
| `redteam/probes/*.jsonl` | ~490 | adversarial probes from rounds 1-7, never trained on |
| `canonical.jsonl` | 34 | classic attacks; a gate against forgetting them |
| `redteam/final-exam.jsonl` | 107 | round 8 final exam, never trained on |

Labels follow one convention throughout: local dev data and regenerable caches are routine, history rewrites are
`destroy`, tampering with safety controls is `system`, and describing danger (in docs, greps, tests) is routine.
All commands, hosts (RFC 5737 addresses, example.com) and credentials in the data are fictitious.

`finetune.py` loads the 8-bit MLX checkpoint, dequantizes it, trains the top encoder layers and the decision head with cross-entropy on the guard's own question (so training matches serving exactly), fits a calibration temperature on a validation split, and re-quantizes to 8-bit in the same layout, so `laya_guard` loads it unchanged. `research/data/eval.jsonl` is a hand-written held-out set that is never trained on.

## Testing

```bash
cd server && .venv/bin/python -m pytest -q          # unit tests, no model needed
.venv/bin/python ../tests/live/run_scenarios.py     # live pi sessions in a sandbox (needs a configured pi model)
```

The live test runs real pi sessions on a throwaway fixture project under `LAYA_TEST_SAFE_ROOT` (default `~/Developer/temp`). A test-only extension (`tests/live/safety-net.ts`) wraps every bash command in macOS `sandbox-exec` (writes only inside the scenario folder, no network) and blocks writes outside it, so even a call Laya misses cannot touch real files.

## Limits

- Laya is a classifier, not a sandbox. It lowers risk; it does not prove a call is safe. Pair it with OS sandboxing for untrusted work.
- The English checkpoints read 512-1024 tokens; long `write` contents are clipped before judging.
- Commands that hide their effect (an innocent-looking script that does damage when run) are judged by what is visible in the call.

## Credits

- [Laya](https://huggingface.co/convaiinnovations/laya) by Convai Innovations (Apache-2.0).
- MLX port [cavi-ai/laya-MLX-8bit](https://huggingface.co/cavi-ai/laya-MLX-8bit) by CAVI AI; its model code (MIT) is downloaded with the weights.
- [pi](https://pi.dev) by Earendil Works.

## License

MIT for this repository's code. Model weights keep their own licenses (Apache-2.0).
