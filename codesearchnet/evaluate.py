#!/usr/bin/env python3
"""Measure inkentry retrieval accuracy on the CodeSearchNet test split.

Each sample is a (docstring, function) pair. The docstring is the query; a hit
is the sampled function appearing in the top 10 of `inkentry search`. Reports
MRR@10, Recall@5 and Recall@10.

The eval runs in two phases because the corpus has to exist on disk before
inkentry can index it:

    materialize   write the sampled functions out as source files
    evaluate      query the index built over those files

`run.sh` chains both around an `inkentry index` call. Run the phases by hand
only when you want to re-query an index that already exists.

Sampling is seeded, so the same --seed/--samples/--languages triple always
selects the same functions. Two runs are comparable only if all three match
and the corpus was rebuilt from the same manifest.

Usage:
    python3 codesearchnet/evaluate.py --materialize --corpus-dir DIR
    python3 codesearchnet/evaluate.py --corpus-dir DIR [--mode hybrid] [--out FILE]
"""

import argparse
import json
import os
import re
import subprocess
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# The bare `code_search_net` id stopped resolving when the Hub required
# namespaced dataset ids; the loading script it used is also gone, so
# `trust_remote_code` must not be passed.
CSN_DATASET = "code-search-net/code_search_net"

MANIFEST_NAME = "manifest.json"
UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9._/-]")

# Set on every spawned inkentry so a headless run never blocks on a keyring
# unlock prompt. Anything that links the crate hangs indefinitely without it.
SECRET_STORE_ENV = "INKENTRY_SECRET_STORE"


def inkentry_bin() -> str:
    return os.environ.get("INKENTRY_BIN", "inkentry")


def subprocess_env() -> dict:
    env = dict(os.environ)
    env.setdefault(SECRET_STORE_ENV, "file")
    return env


def inkentry_version() -> str:
    try:
        r = subprocess.run(
            [inkentry_bin(), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            env=subprocess_env(),
        )
        return r.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def safe_relpath(repo: str, path: str) -> str:
    """Map a CodeSearchNet (repo, path) pair onto a corpus-relative file path."""
    combined = f"{repo}/{path}"
    combined = UNSAFE_PATH_CHARS.sub("_", combined)
    parts = [p for p in combined.split("/") if p not in ("", ".", "..")]
    return "/".join(parts)


def short_name(func_name: str) -> str:
    """CodeSearchNet stores fully qualified names; indexers record the last part."""
    return func_name.rsplit(".", 1)[-1]


def strip_docstring(code: str, doc: str) -> str:
    """Remove the documentation text from a function body.

    The query for each sample *is* that function's docstring, and CodeSearchNet's
    `whole_func_string` embeds it in the body. Indexing it verbatim turns the
    benchmark into a string-matching exercise: full-text mode scores a perfect
    1.0 and can no longer detect a ranking regression. Removing the text leaves
    the eval measuring retrieval from code, which is what it claims to measure.

    The docstring appears verbatim, whatever the language's comment syntax, so a
    literal removal handles all six CodeSearchNet languages. Leftover empty
    quotes or comment markers are harmless.
    """
    if not doc:
        return code
    stripped = code.replace(doc, "")
    for line in doc.splitlines():
        line = line.strip()
        if len(line) > 3:
            stripped = stripped.replace(line, "")
    return stripped


def materialize(
    corpus_dir: Path,
    languages: list[str],
    samples: int,
    seed: int,
    keep_docstrings: bool = False,
) -> dict:
    """Write the sampled functions to disk and return the manifest.

    One output file per source (repo, path), holding every sampled function
    from it. Functions that would be indistinguishable in the results — same
    file and same short name — are dropped rather than counted as ambiguous
    hits.
    """
    import random

    from datasets import load_dataset

    entries: list[dict] = []
    files: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()

    for language in languages:
        print(f"Loading {CSN_DATASET} [{language}/test]...", file=sys.stderr)
        dataset = load_dataset(CSN_DATASET, language, split="test")

        order = list(range(len(dataset)))
        random.Random(seed).shuffle(order)

        taken = 0
        for idx in order:
            if taken >= samples:
                break
            row = dataset[idx]
            query = (row.get("func_documentation_string") or "").strip()
            code = row.get("whole_func_string") or row.get("func_code_string") or ""
            name = short_name(row.get("func_name") or "")
            repo = row.get("repository_name") or "unknown"
            path = row.get("func_path_in_repository") or f"{name}.txt"
            if not query or not code or not name:
                continue

            relpath = safe_relpath(repo, path)
            key = (relpath, name)
            if key in seen:
                continue
            seen.add(key)

            if not keep_docstrings:
                code = strip_docstring(code, query)
            files.setdefault(relpath, []).append(code)
            entries.append(
                {
                    "language": language,
                    "query": query,
                    "name": name,
                    "relpath": relpath,
                    "func_name": row.get("func_name"),
                    "url": row.get("func_code_url"),
                }
            )
            taken += 1

    corpus_root = corpus_dir / "corpus"
    for relpath, blocks in files.items():
        dest = corpus_root / relpath
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n\n\n".join(blocks) + "\n", encoding="utf-8")

    manifest = {
        "dataset": CSN_DATASET,
        "split": "test",
        "languages": languages,
        "samples_per_language": samples,
        "seed": seed,
        "docstrings_kept": keep_docstrings,
        "entries": entries,
        "corpus_files": len(files),
    }
    corpus_dir.mkdir(parents=True, exist_ok=True)
    (corpus_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(
        f"Materialized {len(entries)} functions into {len(files)} files under {corpus_root}",
        file=sys.stderr,
    )
    return manifest


def resolve_db(corpus_dir: Path, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    candidate = corpus_dir / "corpus" / ".inkentry" / "index.db"
    if candidate.exists():
        return str(candidate)
    # Builds predating the rename still write the project index to `.spelunk/`.
    legacy = corpus_dir / "corpus" / ".spelunk" / "index.db"
    if legacy.exists():
        return str(legacy)
    return None


def search(query: str, db: str | None, cwd: Path, mode: str, limit: int) -> list[dict]:
    cmd = [inkentry_bin(), "search", query, "--limit", str(limit), "--format", "json"]
    if mode:
        cmd += ["--mode", mode]
    if db:
        cmd += ["--db", db]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(cwd),
            env=subprocess_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"search failed: {exc}", file=sys.stderr)
        return []
    if r.returncode != 0:
        print(f"search exited {r.returncode}: {r.stderr.strip()[:200]}", file=sys.stderr)
        return []
    body = r.stdout.strip()
    if not body:
        return []
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        print(f"search returned non-JSON: {body[:200]}", file=sys.stderr)
        return []


def find_rank(results: list[dict], name: str, relpath: str) -> int | None:
    """1-based rank of the target, matched on symbol name *and* file path.

    Name alone is not enough: CodeSearchNet reuses names like `get` across
    repositories, and a name-only match would score an unrelated function as
    a hit.
    """
    for i, item in enumerate(results):
        rname = item.get("name") or ""
        rpath = item.get("file_path") or item.get("path") or ""
        if rname == name and rpath.replace("\\", "/").endswith(relpath):
            return i + 1
    return None


def evaluate(manifest: dict, corpus_dir: Path, db: str | None, mode: str) -> dict:
    entries = manifest["entries"]
    corpus_root = corpus_dir / "corpus"
    ranks: list[int | None] = []
    walls: list[float] = []

    for n, entry in enumerate(entries, 1):
        t0 = time.monotonic()
        results = search(entry["query"], db, corpus_root, mode, limit=10)
        walls.append(time.monotonic() - t0)
        ranks.append(find_rank(results, entry["name"], entry["relpath"]))
        if n % 25 == 0 or n == len(entries):
            print(f"  {n}/{len(entries)} queries", file=sys.stderr)

    total = len(ranks)
    rr = [1.0 / r if r else 0.0 for r in ranks]
    r5 = [1.0 if r and r <= 5 else 0.0 for r in ranks]
    r10 = [1.0 if r else 0.0 for r in ranks]

    return {
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "benchmark": "codesearchnet",
        "dataset": manifest["dataset"],
        "condition": mode,
        "search_mode": mode,
        "inkentry_version": inkentry_version(),
        "languages": manifest["languages"],
        "seed": manifest["seed"],
        "corpus_files": manifest["corpus_files"],
        "docstrings_kept": manifest.get("docstrings_kept", False),
        "samples": total,
        "mrr_at_10": round(sum(rr) / total, 4) if total else 0.0,
        "recall_at_5": round(sum(r5) / total, 4) if total else 0.0,
        "recall_at_10": round(sum(r10) / total, 4) if total else 0.0,
        "median_wall_seconds": round(statistics.median(walls), 3) if walls else 0.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--corpus-dir",
        default=str(Path.home() / ".cache" / "inkentry-bench" / "codesearchnet"),
        help="Where the sampled corpus and manifest live (default: ~/.cache/inkentry-bench/codesearchnet).",
    )
    ap.add_argument("--materialize", action="store_true", help="Write the corpus and manifest, then exit.")
    ap.add_argument("--languages", default="python", help="Comma-separated CodeSearchNet languages (default: python).")
    ap.add_argument("--samples", type=int, default=500, help="Functions sampled per language (default: 500).")
    ap.add_argument("--seed", type=int, default=0, help="Sampling seed (default: 0).")
    ap.add_argument(
        "--keep-docstrings",
        action="store_true",
        help="Leave each function's docstring in the indexed body. Off by default: the "
        "docstring is the query, so keeping it lets full-text search score a perfect 1.0.",
    )
    ap.add_argument("--mode", default="hybrid", help="inkentry search --mode value (default: hybrid).")
    ap.add_argument("--db", default=None, help="Index to query (default: the corpus's own index).")
    ap.add_argument("--out", default=None, help="Write the result JSON here instead of stdout.")
    args = ap.parse_args()

    corpus_dir = Path(args.corpus_dir).expanduser()
    languages = [x.strip() for x in args.languages.split(",") if x.strip()]

    if args.materialize:
        materialize(corpus_dir, languages, args.samples, args.seed, args.keep_docstrings)
        return

    manifest_path = corpus_dir / MANIFEST_NAME
    if not manifest_path.exists():
        sys.exit(
            f"No manifest at {manifest_path}. Run with --materialize first, or use run.sh."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    db = resolve_db(corpus_dir, args.db)
    if db is None:
        sys.exit(
            f"No index found under {corpus_dir / 'corpus'}. "
            f"Run: inkentry index {corpus_dir / 'corpus'}"
        )

    result = evaluate(manifest, corpus_dir, db, args.mode)

    print(f"MRR@10:      {result['mrr_at_10']}")
    print(f"Recall@5:    {result['recall_at_5']}")
    print(f"Recall@10:   {result['recall_at_10']}")
    print(f"Median wall: {result['median_wall_seconds']}s")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\nResults written to: {out}")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
