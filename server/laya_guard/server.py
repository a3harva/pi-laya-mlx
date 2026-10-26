"""HTTP server: `POST /v1/guard` screens one tool call, `POST /v1/systemone` answers raw Laya requests.

Environment:
  LAYA_GUARD_HOST       bind address (default 127.0.0.1)
  LAYA_GUARD_PORT       port (default 8723)
  LAYA_GUARD_MODEL      checkpoint: a local directory or `hf-repo[:subdir]`
                        (default cavi-ai/laya-MLX-8bit:typed-decisions)
  LAYA_GUARD_THRESHOLD  block when risk >= this (default 0.5)
  LAYA_GUARD_API_KEY    when set, requests need `Authorization: Bearer <key>`
"""

import argparse
import logging
import os
import time

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import __version__, policy
from .model import load_model

log = logging.getLogger("laya_guard")


class GuardRequest(BaseModel):
    tool: str
    input: dict = Field(default_factory=dict)
    cwd: str | None = None
    threshold: float | None = None


class SystemOneRequest(BaseModel):
    state: dict | str
    questions: dict


def create_app(model, threshold: float, api_key: str | None = None) -> FastAPI:
    app = FastAPI(title="laya-guard", version=__version__)

    def authorize(authorization: str | None = Header(default=None)):
        if api_key and authorization != f"Bearer {api_key}":
            raise HTTPException(status_code=401, detail="missing or wrong bearer token")

    @app.get("/health")
    def health():
        return {"status": "ok", "model": model.name, "threshold": threshold, "version": __version__}

    @app.post("/v1/guard", dependencies=[Depends(authorize)])
    def guard(request: GuardRequest):
        started = time.perf_counter()
        limit = request.threshold if request.threshold is not None else threshold
        result = policy.judge(model.predict, request.tool, request.input, request.cwd, limit)
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        result["model"] = model.name
        log.info(
            "%-5s risk=%.2f %-11s %s: %s",
            result["decision"], result["risk"], result["category"], request.tool,
            policy.summarize_input(request.tool, request.input),
        )
        return result

    @app.post("/v1/systemone", dependencies=[Depends(authorize)])
    def systemone(request: SystemOneRequest):
        # Jev clients send `bool`; Laya calls the same question type `noul`.
        questions = {
            name: {**q, "type": "noul"} if q.get("type") == "bool" else q for name, q in request.questions.items()
        }
        try:
            return model.predict(request.state, questions)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return app


def main():
    parser = argparse.ArgumentParser(description="Laya tool-call safety guard server (MLX)")
    parser.add_argument("--host", default=os.environ.get("LAYA_GUARD_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("LAYA_GUARD_PORT", "8723")))
    parser.add_argument("--model", default=os.environ.get("LAYA_GUARD_MODEL"))
    parser.add_argument("--threshold", type=float, default=float(os.environ.get("LAYA_GUARD_THRESHOLD", "0.5")))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    os.environ.setdefault("USE_TF", "0")
    started = time.perf_counter()
    model = load_model(args.model)
    log.info("loaded %s in %.1fs", model.name, time.perf_counter() - started)
    app = create_app(model, args.threshold, os.environ.get("LAYA_GUARD_API_KEY"))
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
