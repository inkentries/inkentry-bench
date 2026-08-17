#!/usr/bin/env python3
"""Code-graph benchmark — grep vs inkentry_search vs inkentry_graph.

For each task (a symbol in an indexed repo), runs three conditions and
measures how well each retrieves the ground-truth set of files containing
callers/callees/implementers of that symbol.

Conditions:
    grep             git grep <symbol> over the repo
    inkentry_search  inkentry search <symbol> --only-code (semantic)
    inkentry_graph   inkentry plumbing graph-edges --symbol <symbol>

Metrics: precision@k, recall@k, F1. No LLM, no API costs.

Usage:
    python graph/evaluate.py \\
        --tasks graph/tasks.json \\
        --repos-dir ~/inkentry-bench/repos \\
        --k 10 \\
        --out results/graph.json

    # or via environment variable:
    INKENTRY_BENCH_REPOS=~/inkentry-bench/repos python graph/evaluate.py ...

Task format (JSON):
    [
        {
            "symbol": "parse_args",
            "repo": "ripgrep",
            "ground_truth_files": ["src/cli.rs", "src/parser.rs"]
        }
    ]
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def run_grep(repo_path: Path, symbol: str, limit: int = 10) -> set[str]:
    """Return set of file paths containing the symbol via git grep, capped at limit."""
    try:
        result = subprocess.run(
            ["git", "grep", "-wl", symbol, "--", ":!.inkentry"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            files = result.stdout.strip().split("\n")
            return set(files[:limit])
        return set()
    except Exception:
        return set()


def search_file_paths(results: list) -> set[str]:
    """File paths from a `search --format json` payload, either envelope shape.

    Results arrive as a fusion envelope, one entry per hit:
    `{type, fused_rank, fused_score, corpus_rank, code|memory: {…}}`, with the
    chunk fields nested under `code`. Older builds emitted them flat. Reading
    the top level unconditionally against a current binary yields an empty set
    and scores a silent zero, so unwrap when the nested object is present.
    """
    paths: set[str] = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        nested = item.get("code")
        payload = nested if isinstance(nested, dict) else item
        path = payload.get("file_path", "")
        if path:
            paths.add(path)
    return paths


def run_inkentry_search(repo_path: Path, symbol: str, limit: int = 10) -> set[str]:
    """Return set of file paths from inkentry search results."""
    try:
        result = subprocess.run(
            [
                "inkentry",
                "search",
                symbol,
                "--only-code",
                "--limit",
                str(limit),
                "--format",
                "json",
            ],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return search_file_paths(json.loads(result.stdout))
        if result.returncode not in (0, 1):
            print(
                f"  search: exit {result.returncode} for {symbol}: "
                f"{result.stderr.strip()[:200]}",
                file=sys.stderr,
            )
        return set()
    except Exception as e:
        print(f"  search: failed for {symbol}: {e}", file=sys.stderr)
        return set()


def run_inkentry_graph(repo_path: Path, symbol: str, limit: int = 10) -> set[str]:
    """Return set of file paths from the code graph edges touching a symbol."""
    try:
        result = subprocess.run(
            ["inkentry", "plumbing", "graph-edges", "--symbol", symbol],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode not in (0, 1):
            print(
                f"  graph: exit {result.returncode} for {symbol}: "
                f"{result.stderr.strip()[:200]}",
                file=sys.stderr,
            )
            return set()
        if result.stdout.strip():
            # JSONL, one edge object per line: {source_file, source_name,
            # target_name, kind, line}. Collect unique source_file values in
            # order of first appearance.
            edges = [
                json.loads(line)
                for line in result.stdout.splitlines()
                if line.strip()
            ]
            seen: set[str] = set()
            files_ordered: list[str] = []
            for edge in edges:
                f = edge.get("source_file", "")
                if f and f not in seen:
                    seen.add(f)
                    files_ordered.append(f)
            return set(files_ordered[:limit])
        return set()
    except Exception as e:
        print(f"  graph: parse failed for {symbol}: {e}", file=sys.stderr)
        return set()


def precision(retrieved: set[str], relevant: set[str]) -> float:
    if not retrieved:
        return 0.0
    return len(retrieved & relevant) / len(retrieved)


def recall(retrieved: set[str], relevant: set[str]) -> float:
    if not relevant:
        return 1.0
    return len(retrieved & relevant) / len(relevant)


def f1(p: float, r: float) -> float:
    if p + r == 0:
        return 0.0
    return 2 * p * r / (p + r)


def get_inkentry_version() -> str:
    try:
        r = subprocess.run(
            ["inkentry", "--version"], capture_output=True, text=True, timeout=5
        )
        return r.stdout.strip()
    except Exception:
        return "unknown"


def main():
    parser = argparse.ArgumentParser(description="Code-graph benchmark.")
    parser.add_argument("--tasks", required=True, help="Tasks JSON file.")
    parser.add_argument(
        "--repos-dir",
        default=os.environ.get("INKENTRY_BENCH_REPOS"),
        help="Directory containing benchmark repos (overrides $INKENTRY_BENCH_REPOS).",
    )
    parser.add_argument("--k", type=int, default=10, help="Result limit (default: 10).")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    if not args.repos_dir:
        parser.error(
            "Provide --repos-dir or set $INKENTRY_BENCH_REPOS to the directory "
            "containing the benchmark repos."
        )
    repos_dir = Path(args.repos_dir).expanduser().resolve()

    with open(args.tasks) as f:
        tasks = json.load(f)

    print(f"Tasks: {len(tasks)}")
    print()

    conditions = {
        "grep": {"precisions": [], "recalls": [], "f1s": [], "wall": []},
        "inkentry_search": {"precisions": [], "recalls": [], "f1s": [], "wall": []},
        "inkentry_graph": {"precisions": [], "recalls": [], "f1s": [], "wall": []},
    }

    for i, task in enumerate(tasks):
        symbol = task["symbol"]
        repo_path = (repos_dir / task["repo"]).resolve()
        relevant = set(task["ground_truth_files"])

        print(f"[{i + 1}/{len(tasks)}] {symbol} ({len(relevant)} ground-truth files)")

        for cond_name, runners in [
            ("grep", lambda: run_grep(repo_path, symbol, args.k)),
            ("inkentry_search", lambda: run_inkentry_search(repo_path, symbol, args.k)),
            ("inkentry_graph", lambda: run_inkentry_graph(repo_path, symbol, args.k)),
        ]:
            start = time.monotonic()
            retrieved = runners()
            elapsed = time.monotonic() - start

            p = precision(retrieved, relevant)
            r = recall(retrieved, relevant)
            f = f1(p, r)

            conditions[cond_name]["precisions"].append(p)
            conditions[cond_name]["recalls"].append(r)
            conditions[cond_name]["f1s"].append(f)
            conditions[cond_name]["wall"].append(elapsed)

            print(
                f"  {cond_name:<18} P={p:.2f}  R={r:.2f}  F1={f:.2f}  "
                f"retrieved={len(retrieved)}/{len(relevant)}  {elapsed:.2f}s"
            )

    output = {
        "benchmark": "code_graph",
        "inkentry_version": get_inkentry_version(),
        "k": args.k,
        "num_tasks": len(tasks),
        "timestamp": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
    }

    print()
    for cond_name, cond_data in conditions.items():
        ps = cond_data["precisions"]
        rs = cond_data["recalls"]
        fs = cond_data["f1s"]
        ws = cond_data["wall"]
        n = len(ps)
        if n:
            output[cond_name] = {
                "precision": round(statistics.mean(ps), 4),
                "recall": round(statistics.mean(rs), 4),
                "f1": round(statistics.mean(fs), 4),
                "median_wall_seconds": round(statistics.median(ws), 3),
            }
            print(
                f"{cond_name:<18} P={statistics.mean(ps):.3f}  R={statistics.mean(rs):.3f}  "
                f"F1={statistics.mean(fs):.3f}  wall={statistics.median(ws):.3f}s"
            )

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\nResults written to: {args.out}")
    else:
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
