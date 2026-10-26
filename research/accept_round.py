"""Reviewer tool: copy a red-team file into the dataset after review, applying drops and relabels.

  accept_round.py candidates <src.jsonl> <dest.jsonl> --tag r3 [--drop id,id] [--relabel id=category,...]
  accept_round.py probes     <src.jsonl> <dest.jsonl>         [--drop id,id] [--relabel id=category,...]

Candidates keep only category/tool/input/cwd plus a `source` tag. Probes are copied with their expected labels.
"""

import argparse
import json
from pathlib import Path

CATEGORIES = {"routine", "destroy", "exfiltrate", "remote_code", "system", "production"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["candidates", "probes"])
    parser.add_argument("src")
    parser.add_argument("dest")
    parser.add_argument("--tag", default="")
    parser.add_argument("--drop", default="")
    parser.add_argument("--relabel", default="")
    opts = parser.parse_args()
    drop = {i for i in opts.drop.split(",") if i}
    relabel = dict(pair.split("=") for pair in opts.relabel.split(",") if pair)
    field = "category" if opts.kind == "candidates" else "expected"

    rows, skipped = [], []
    for line in Path(opts.src).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("id") in drop:
            continue
        if row.get("id") in relabel:
            row[field] = relabel[row["id"]]
        if row.get(field) not in CATEGORIES or row.get("tool") not in {"bash", "write", "edit", "read"}:
            skipped.append(row.get("id"))
            continue
        if opts.kind == "candidates":
            row = {"category": row["category"], "tool": row["tool"], "input": row["input"], "cwd": row.get("cwd"),
                   "source": f"redteam-{opts.tag}:{row.get('id')}"}
        rows.append(row)
    dest = Path(opts.dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"{len(rows)} rows -> {dest}; dropped {len(drop)}; relabelled {len(relabel)}; invalid {skipped}")


if __name__ == "__main__":
    main()
