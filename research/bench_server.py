"""Run every held-out set through a running guard server over HTTP and record verdicts and latency.

  python research/bench_server.py http://127.0.0.1:8723 out/laya.jsonl
  python research/bench_server.py http://127.0.0.1:8724 out/jev.jsonl

Calls go one at a time by default, as pi's extension sends them, so latency is what an agent waits per call.
`--concurrency N` overlaps calls for a remote backend; each call is still timed on its own.
Rows already in the output file are skipped, so an interrupted run resumes.
"""

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
SETS = {
    "heldout": [DATA / "eval.jsonl"] + [DATA / "redteam" / "probes" / f"round-{n}.jsonl" for n in range(1, 8)],
    "round9": [DATA / "redteam" / "probes" / "round-9.jsonl"],
    "round10": [DATA / "redteam" / "probes" / "round-10.jsonl"],
    "final": [DATA / "redteam" / "final-exam.jsonl"],
    "canonical": [DATA / "canonical.jsonl"],
}
CWD = "/Users/dev/project"


def rows():
    for name, files in SETS.items():
        for file in files:
            for i, line in enumerate(file.read_text().splitlines()):
                if line.strip():
                    row = json.loads(line)
                    label = row.get("label") or ("safe" if row.get("expected") == "routine" else "unsafe")
                    yield f"{file.stem}:{i}", name, label, row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("out")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--concurrency", type=int, default=1)
    opts = parser.parse_args()

    out = Path(opts.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {json.loads(line)["id"] for line in out.read_text().splitlines()} if out.exists() else set()
    todo = [r for r in rows() if r[0] not in done]
    print(f"{len(done)} done, {len(todo)} to go")
    lock, count = threading.Lock(), [0]

    def check(item):
        row_id, set_name, label, row = item
        body = {"tool": row["tool"], "input": row["input"], "cwd": row.get("cwd") or CWD, "threshold": opts.threshold}
        request = urllib.request.Request(f"{opts.url}/v1/guard", json.dumps(body).encode(), {"Content-Type": "application/json"})
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                result = json.load(response)
            error = None
        except Exception as e:  # recorded, scored as an error, not silently dropped
            result, error = {}, str(e)[:200]
        wall = (time.perf_counter() - started) * 1000
        record = {
            "id": row_id, "set": set_name, "label": label, "tool": row["tool"],
            "decision": result.get("decision"), "risk": result.get("risk"), "category": result.get("category"),
            "windows": result.get("windows"), "server_ms": result.get("latency_ms"), "wall_ms": round(wall, 1), "error": error,
        }
        with lock:
            sink.write(json.dumps(record) + "\n")
            sink.flush()
            count[0] += 1
            if count[0] % 50 == 0:
                print(f"{count[0]}/{len(todo)}", flush=True)

    with out.open("a") as sink, ThreadPoolExecutor(opts.concurrency) as pool:
        list(pool.map(check, todo))


if __name__ == "__main__":
    main()
