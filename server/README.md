# laya-guard server

MLX server for the pi-laya-mlx tool-call guard. See the repository README.

FastAPI app (`laya_guard/server.py`): `POST /v1/guard` screens one tool call, `POST /v1/systemone` answers raw Laya
requests, and `GET /` serves the dev UI.

## Run the dev server (with UI)

From the repository root, after `scripts/setup.sh` has created `server/.venv`:

```bash
USE_TF=0 server/.venv/bin/laya-guard-serve --port 8723
```

Then open <http://127.0.0.1:8723/> in a browser. The UI is served by the same process, so there is no separate frontend
dev server or build step.

Run it detached instead (keeps running after the terminal closes, log in `~/.cache/laya-guard/server.log`):

```bash
mkdir -p ~/.cache/laya-guard
USE_TF=0 nohup server/.venv/bin/laya-guard-serve --port 8723 > ~/.cache/laya-guard/server.log 2>&1 &
```

Check it is up:

```bash
curl -s localhost:8723/health
curl -s -o /dev/null -w '%{http_code}\n' localhost:8723/      # 200 = UI is served
```

Smoke-test the guard endpoint:

```bash
curl -s localhost:8723/v1/guard -H 'content-type: application/json' \
  -d '{"tool":"bash","input":{"command":"rm -rf ~/Desktop"},"cwd":"/tmp"}'
```

Stop a detached server:

```bash
pkill -f 'laya-guard-serve --port 8723'
```

## Options

Flags or environment variables:

| | Default | |
|---|---|---|
| `--host` / `LAYA_GUARD_HOST` | `127.0.0.1` | bind address |
| `--port` / `LAYA_GUARD_PORT` | `8723` | |
| `--model` / `LAYA_GUARD_MODEL` | `checkpoints/laya-toolguard` if present, else the Hugging Face checkpoint | local dir, `hf-repo[:subdir]`, or a comma-separated list (cautious ensemble) |
| `--threshold` / `LAYA_GUARD_THRESHOLD` | `0.5` | block when risk ≥ this |
| `LAYA_GUARD_API_KEY` | none | require `Authorization: Bearer <key>` |
| `--backend` / `LAYA_GUARD_BACKEND` | `mlx` | `jev`: TypeSafe's hosted JEV (`laya_guard/jev.py`, needs `TYPESAFE_API_KEY`) |

## Tests

```bash
cd server && .venv/bin/python -m pytest -q     # unit tests, no model needed
```
