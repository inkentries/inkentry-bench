#!/usr/bin/env python3
"""Own-repo retrieval eval for inkentry (in-domain golden set).

Builds a golden set straight from the *indexed* project: every embedded chunk
that has a symbol name and a docstring becomes one (query, target) pair, where
the query is the docstring and the target is that chunk (matched by file path +
symbol name, so name collisions across files don't create false hits).

For each query it runs `inkentry search` and records the 1-based rank of the
target, then reports MRR@10, Recall@5, Recall@10, and median wall time — same
shape as codesearchnet/evaluate.py so report.py can diff them.

This is the "own-repo golden set" path: in-domain, no downloads, runs against
whatever model currently backs the index.

NOTE on leakage: inkentry's Chunk::embedding_text() includes the docstring in the
embedded text, so a docstring query is an easy hit. That's fine for *calibration*
(does the pipeline produce sane, non-zero, model-discriminating numbers?) and for
*relative* model comparison (every model gets the same advantage). For a stricter
absolute number, re-embed with docstrings stripped from embedding_text.

Prereq: the target project is indexed; `inkentry` is in PATH. Read-only on the DB.

Usage:
    python ownrepo/golden_eval.py \
        --db .inkentry/index.db \
        [--samples 150] [--seed 0] [--conditions hybrid] [--model-label nomic-v1.5] \
        [--out results/ownrepo-nomic.json]
"""

import argparse
import json
import re
import sqlite3
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone


# Code node types worth retrieving (excludes Markdown `section`, etc.).
CODE_NODE_TYPES = ("function", "method", "struct", "class", "enum", "trait", "impl", "type_alias")
# Rust line-doc markers; strip these from extracted comments.
DOC_RE = re.compile(r"^\s*(///|//!)\s?(.*)$")
ATTR_OR_BLANK_RE = re.compile(r"^\s*(#\[.*\]|#!\[.*\]|)\s*$")


def extract_doc(path: str, start_line: int) -> str:
    """Extract the FULL leading `///`/`//!` doc comment above a definition.

    `metadata.docstring` in the index only keeps the last comment line, so we
    read the source and walk upward from the definition, collecting the
    contiguous doc-comment block (skipping intervening attributes / blank lines).
    """
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return ""
    i = start_line - 2  # 0-based line directly above the definition
    doc: list[str] = []
    while i >= 0:
        m = DOC_RE.match(lines[i])
        if m:
            doc.append(m.group(2).strip())
            i -= 1
        elif ATTR_OR_BLANK_RE.match(lines[i]) and doc:
            # attribute/blank between doc and def — keep scanning upward
            i -= 1
        else:
            break
    doc.reverse()
    text = re.sub(r"\s+", " ", " ".join(d for d in doc if d)).strip()
    return text[:400]


def load_golden(db_path: str, samples: int, seed: int) -> list[dict]:
    """Build the golden set: non-vendor code chunks with a real doc comment.

    Query = the function's full leading doc comment (read from source). Target =
    that chunk, matched later by file path + symbol name.
    """
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(CODE_NODE_TYPES))
    rows = conn.execute(
        f"""
        SELECT c.name AS name, f.path AS path, c.start_line AS start_line
        FROM chunks c
        JOIN files f ON f.id = c.file_id
        WHERE c.name IS NOT NULL
          AND c.node_type IN ({placeholders})
          AND json_extract(c.metadata, '$.docstring') IS NOT NULL
          AND f.path NOT LIKE 'vendor/%'
          AND f.path LIKE '%.rs'
        """,
        CODE_NODE_TYPES,
    ).fetchall()
    conn.close()

    items = []
    for r in rows:
        d = dict(r)
        d["query"] = extract_doc(d["path"], d["start_line"])
        if len(d["query"]) >= 40:  # require a distinctive query
            items.append(d)

    import random

    random.Random(seed).shuffle(items)
    return items[:samples]


CONDITIONS = {
    # The golden set is built from indexed code chunks, so every condition
    # restricts to the code corpus; the knob is the ranking.
    "hybrid": ["--only-code"],
    "text": ["--only-code", "--only-text"],
}


def inkentry_search(query: str, condition: str, limit: int = 10) -> list[dict]:
    cmd = ["inkentry", "search", query, "--limit", str(limit), "--format", "json"]
    cmd.extend(CONDITIONS[condition])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode not in (0, 1):
            print(
                f"search exited {r.returncode}: {r.stderr.strip()[:200]}",
                file=sys.stderr,
            )
            return []
        if not r.stdout.strip():
            return []
        return json.loads(r.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        print(f"search failed: {e}", file=sys.stderr)
        return []


def find_rank(results: list[dict], name: str, path: str) -> int | None:
    """Rank of the first result matching the target by file path + symbol name.

    Results arrive as a fusion envelope with the chunk fields nested under
    `code`; older builds emitted them flat. Reading the top level against a
    current binary matches nothing and scores a silent zero, so unwrap when
    the nested object is present and fall through to the flat shape otherwise.
    """
    for i, item in enumerate(results):
        nested = item.get("code")
        payload = nested if isinstance(nested, dict) else item
        rname = payload.get("name") or ""
        rpath = payload.get("file_path") or payload.get("path") or ""
        if rname == name and rpath.endswith(path):
            return i + 1
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Own-repo golden-set retrieval eval.")
    ap.add_argument("--db", default=".inkentry/index.db")
    ap.add_argument("--samples", type=int, default=150)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--conditions",
        default="hybrid",
        help="hybrid | text (comma-separated ok). hybrid is the best-available "
        "ranking; text is full-text only.",
    )
    ap.add_argument("--model-label", default="unknown")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    golden = load_golden(args.db, args.samples, args.seed)
    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    unknown = [c for c in conditions if c not in CONDITIONS]
    if unknown:
        ap.error(
            f"unknown condition(s): {', '.join(unknown)}. "
            f"Valid: {', '.join(sorted(CONDITIONS))}"
        )
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outputs = []

    for condition in conditions:
        rr, r5, r10, walls = [], [], [], []
        for it in golden:
            q = it["query"]
            if not q:
                continue
            t0 = time.monotonic()
            res = inkentry_search(q, condition=condition)
            walls.append(time.monotonic() - t0)
            rank = find_rank(res, it["name"], it["path"])
            rr.append(1.0 / rank if rank else 0.0)
            r5.append(1.0 if rank and rank <= 5 else 0.0)
            r10.append(1.0 if rank else 0.0)

        n = len(rr)
        out = {
            "run_id": ts,
            "benchmark": "ownrepo-golden",
            "model_label": args.model_label,
            "condition": condition,
            "search_args": CONDITIONS[condition],
            "samples": n,
            "mrr_at_10": round(sum(rr) / n, 4) if n else 0.0,
            "recall_at_5": round(sum(r5) / n, 4) if n else 0.0,
            "recall_at_10": round(sum(r10) / n, 4) if n else 0.0,
            "median_wall_seconds": round(statistics.median(walls), 3) if walls else 0.0,
        }
        outputs.append(out)
        print(
            f"[{condition}] n={n}  MRR@10={out['mrr_at_10']}  "
            f"R@5={out['recall_at_5']}  R@10={out['recall_at_10']}  "
            f"med_wall={out['median_wall_seconds']}s"
        )

    result = outputs[0] if len(outputs) == 1 else outputs
    if args.out:
        import os

        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(result, f, indent=2)
        print(f"written: {args.out}")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
