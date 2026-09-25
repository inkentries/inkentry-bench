#!/usr/bin/env python3
"""Known-item code retrieval over a pinned repository commit, through the real CLI.

Each eval set under `evalsets/code-knownitem-*/v<N>/` names a repository
commit, a list of target code regions (path plus line span) and three queries
per target. A query hits when an `inkentry search` result in the top 10 lies in
the target's file and its line span overlaps the target's. The file-level
variant drops the overlap condition. Reports R@1, R@5, R@10, MRR@10 and
file-level R@10: overall, by query style and by target language.

Three phases, which `run.sh` chains:

    materialize   git archive the pinned commit (and its submodules) into a corpus
    index         delete the corpus's .inkentry, index it fresh, wait for the lock
    evaluate      run every query through `inkentry search`, score, write JSON

Usage:
    python3 codeknown/evaluate.py materialize --set code-knownitem-inkentry/v1 [--repo-dir DIR]
    python3 codeknown/evaluate.py index       --set code-knownitem-inkentry/v1 --mode text
    python3 codeknown/evaluate.py evaluate    --set code-knownitem-inkentry/v1 --mode text [--out FILE]

The corpus lives under the cache dir, keyed by repository and commit, so two
sets pinned to the same commit share one corpus and one index.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVALSETS = REPO / "evalsets"
DEFAULT_CACHE = Path.home() / ".cache" / "inkentry-bench" / "codeknown"

STYLES = ("concept", "keywords", "identifier")
MODES = ("text", "hybrid")
TOP_K = 10

# Env vars the harness keeps from the caller. Every other INKENTRY_* is dropped:
# INKENTRY_SERVER_URL, INKENTRY_MODE, INKENTRY_PROJECT_ID and friends each
# change where the CLI sends data or which store it reads, and a leftover export
# in the caller's shell must not reach a benchmark run.
KEPT_INKENTRY_VARS = {"INKENTRY_BIN", "INKENTRY_STATE_DIR", "INKENTRY_CONFIG_DIR"}


# ── eval set ─────────────────────────────────────────────────────────────────


def parse_set_ref(ref: str) -> tuple[str, int]:
    m = re.fullmatch(r"([a-z0-9][a-z0-9-]*)/v([1-9][0-9]*)", ref)
    if not m:
        raise SystemExit(f"--set wants <name>/v<N>, e.g. code-knownitem-inkentry/v1; got {ref!r}")
    return m.group(1), int(m.group(2))


def load_set(ref: str) -> dict:
    name, version = parse_set_ref(ref)
    d = EVALSETS / name / f"v{version}"
    if not d.is_dir():
        raise SystemExit(f"no eval set at {d}")
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("name") != name or manifest.get("version") != version:
        raise SystemExit(
            f"{d}/manifest.json says {manifest.get('name')}/v{manifest.get('version')}, "
            f"not {name}/v{version}"
        )
    targets = json.loads((d / "targets.json").read_text(encoding="utf-8"))
    queries = json.loads((d / "queries.json").read_text(encoding="utf-8"))
    return {"dir": d, "manifest": manifest, "targets": targets, "queries": queries}


def query_id(q: dict) -> str:
    return f"{q['tid']}:{q['style']}"


# ── scoring ──────────────────────────────────────────────────────────────────


def result_payload(item: dict) -> dict:
    """The code fields of one search result, nested (current) or flat (pre-1.0)."""
    nested = item.get("code")
    return nested if isinstance(nested, dict) else item


def ranked_hits(items: list) -> list[dict]:
    """Ranked code results as {path, start, end}, in rank order.

    Appendix members (graph neighbours, cross-project entries) carry
    `fused_rank: null`; they were not ranked against the query and must not be
    scored as if they had been. Memory envelopes have no `code` payload.
    """
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if "fused_rank" in item and item["fused_rank"] is None:
            continue
        if item.get("type", "code") != "code":
            continue
        p = result_payload(item)
        path = p.get("file_path") or p.get("path")
        if path is None or p.get("start_line") is None or p.get("end_line") is None:
            continue
        out.append({"path": path, "start": int(p["start_line"]), "end": int(p["end_line"])})
    return out


def hit_rank(results: list[dict], target: dict, level: str = "chunk", k: int = TOP_K) -> int | None:
    """1-based rank of the first top-k result that is the target, else None.

    level="chunk": same path and the line spans overlap (both inclusive).
    level="file":  same path.
    """
    for i, r in enumerate(results[:k]):
        if r["path"] != target["path"]:
            continue
        if level == "file" or (r["start"] <= target["end_line"] and r["end"] >= target["start_line"]):
            return i + 1
    return None


def metrics(ranks: list[int | None], file_ranks: list[int | None]) -> dict:
    n = len(ranks)
    if n == 0:
        return {"n": 0, "recall_at_1": None, "recall_at_5": None, "recall_at_10": None,
                "mrr_at_10": None, "file_recall_at_10": None}

    def recall(rs, k):
        return round(sum(1 for r in rs if r is not None and r <= k) / n, 4)

    return {
        "n": n,
        "recall_at_1": recall(ranks, 1),
        "recall_at_5": recall(ranks, 5),
        "recall_at_10": recall(ranks, 10),
        "mrr_at_10": round(sum(1.0 / r for r in ranks if r is not None and r <= 10) / n, 4),
        "file_recall_at_10": recall(file_ranks, 10),
    }


def breakdown(per_query: list[dict]) -> dict:
    def m(rows):
        return metrics([r["rank"] for r in rows], [r["file_rank"] for r in rows])

    by_style = {s: m([r for r in per_query if r["style"] == s]) for s in STYLES}
    langs = sorted({r["language"] for r in per_query})
    by_language = {lang: m([r for r in per_query if r["language"] == lang]) for lang in langs}
    return {"all": m(per_query), "by_style": by_style, "by_language": by_language}


# ── inkentry invocation ──────────────────────────────────────────────────────


def inkentry_bin() -> str:
    return os.environ.get("INKENTRY_BIN", "inkentry")


def harness_env(mode: str, cache: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("INKENTRY_") or k in KEPT_INKENTRY_VARS}
    # Without the file store, anything linking the crate blocks on a keyring prompt.
    env["INKENTRY_SECRET_STORE"] = "file"
    env["NO_COLOR"] = "1"
    # A private registry: indexing registers the corpus, and search consults the
    # registry for linked projects. Neither should touch the user's.
    env["INKENTRY_REGISTRY_DIR"] = str(cache / "registry")
    if mode == "text":
        # Parse and full-text only, no server probe, nothing leaves the machine.
        env["INKENTRY_MODE"] = "offline"
    return env


def config_file(cache: Path) -> Path:
    """An explicit, near-empty personal config.

    Passed as --config on every call so the user's ~/.config/inkentry/config.toml
    (which may say `cloud = true`) is never read. The project config is the
    other half of that guard, and it is why the corpus's own .inkentry/ is
    deleted before indexing: see run.sh.
    """
    cfg = cache / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    body = "# codeknown harness: intentionally empty, so no server_url and no cloud.\n"
    if not cfg.exists() or cfg.read_text(encoding="utf-8") != body:
        cfg.write_text(body, encoding="utf-8")
    return cfg


def inkentry_version(env: dict) -> str:
    try:
        r = subprocess.run([inkentry_bin(), "--version"], capture_output=True, text=True,
                           timeout=30, env=env)
        return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def binary_sha256() -> str | None:
    """Identifies the exact build; many builds share one version string."""
    path = shutil.which(inkentry_bin())
    if path is None:
        return None
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def status(corpus: Path, cfg: Path, env: dict) -> dict:
    r = subprocess.run([inkentry_bin(), "--config", str(cfg), "status", "--format", "json"],
                       capture_output=True, text=True, cwd=corpus, env=env, timeout=120)
    if r.returncode != 0:
        raise SystemExit(f"inkentry status exited {r.returncode} in {corpus}: {r.stderr.strip()[:400]}")
    return json.loads(r.stdout)


def search_args(mode: str) -> list[str]:
    args = ["--only-code", "--no-stale-check", "--format", "json", "-l", str(TOP_K), "-q"]
    if mode == "text":
        args.append("--only-text")
    return args


def run_search(query: str, mode: str, corpus: Path, cfg: Path, env: dict) -> tuple[list, float]:
    # `--` before the query: one query in the inkentry set begins with "--", and
    # clap reads a bare "--only-text search flag example" as an unknown flag,
    # exits 2 and prints nothing, which would score as a silent miss.
    cmd = [inkentry_bin(), "--config", str(cfg), "search", *search_args(mode), "--", query]
    t0 = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=corpus, env=env, timeout=300)
    wall = time.monotonic() - t0
    if r.returncode != 0:
        # Exit 1 means the query never ran, exit 2 a rejected argument; no
        # matches is exit 0 with []. Either failure invalidates the whole run.
        raise SystemExit(f"search exited {r.returncode} for {query!r}: {r.stderr.strip()[:400]}")
    body = r.stdout.strip()
    items = json.loads(body) if body else []
    if not isinstance(items, list):
        raise SystemExit(f"search returned a {type(items).__name__}, not a list, for {query!r}")
    return items, wall


# ── corpus ───────────────────────────────────────────────────────────────────


def corpus_sources(manifest: dict) -> list[dict]:
    c = manifest["corpus"]
    return [{"path": "", "url": c["repo_url"], "commit": c["commit"]}] + list(c.get("submodules", []))


def default_corpus_dir(manifest: dict, cache: Path) -> Path:
    c = manifest["corpus"]
    slug = re.sub(r"[^A-Za-z0-9]+", "-", c["repo_url"].split("://", 1)[-1].removeprefix("github.com/"))
    return cache / "corpora" / f"{slug.strip('-')}-{c['commit'][:12]}"


def git(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, **kw)


def has_commit(git_args: list[str], commit: str) -> bool:
    return git([*git_args, "cat-file", "-e", f"{commit}^{{commit}}"]).returncode == 0


def locate_source(src: dict, repo_dir: Path | None, cache: Path) -> list[str]:
    """git args (-C DIR or --git-dir DIR) under which `src['commit']` exists."""
    if repo_dir is not None:
        d = repo_dir / src["path"] if src["path"] else repo_dir
        args = ["-C", str(d)]
        if not has_commit(args, src["commit"]):
            where = f"submodule {src['path']!r} of " if src["path"] else ""
            raise SystemExit(
                f"{src['commit']} is not in {where}{repo_dir} (checked {d}). "
                f"Fetch it there, check out the submodule, or drop --repo-dir to fetch from {src['url']}."
            )
        return args
    bare = cache / "git" / (re.sub(r"[^A-Za-z0-9]+", "-", src["url"].split("://", 1)[-1]).strip("-") + ".git")
    args = ["--git-dir", str(bare)]
    if not bare.exists():
        bare.parent.mkdir(parents=True, exist_ok=True)
        r = git(["init", "--bare", "-q", str(bare)])
        if r.returncode != 0:
            raise SystemExit(f"git init {bare} failed: {r.stderr.strip()}")
    if not has_commit(args, src["commit"]):
        print(f"==> fetching {src['url']} @ {src['commit'][:12]}", file=sys.stderr)
        r = git([*args, "fetch", "-q", "--depth", "1", "--no-tags", src["url"], src["commit"]])
        if r.returncode != 0 or not has_commit(args, src["commit"]):
            raise SystemExit(f"fetching {src['commit']} from {src['url']} failed: {r.stderr.strip()[:400]}")
    return args


def gitlinks(git_args: list[str], commit: str) -> dict[str, str]:
    """Submodule path -> pinned commit, read from the superproject's tree."""
    r = git([*git_args, "ls-tree", "-r", commit])
    if r.returncode != 0:
        raise SystemExit(f"git ls-tree {commit} failed: {r.stderr.strip()}")
    links = {}
    for line in r.stdout.splitlines():
        meta, path = line.split("\t", 1)
        mode, kind, sha = meta.split()
        if kind == "commit":
            links[path] = sha
    return links


def archive_into(git_args: list[str], commit: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    ga = subprocess.Popen(["git", *git_args, "archive", "--format=tar", commit], stdout=subprocess.PIPE)
    tar = subprocess.run(["tar", "-xf", "-", "-C", str(dest)], stdin=ga.stdout)
    ga.stdout.close()
    if ga.wait() != 0 or tar.returncode != 0:
        raise SystemExit(f"git archive {commit} into {dest} failed")


def materialize(evalset: dict, corpus: Path, cache: Path, repo_dir: Path | None) -> None:
    sources = corpus_sources(evalset["manifest"])
    located = [(src, locate_source(src, repo_dir, cache)) for src in sources]

    # The manifest's submodule list must be the superproject's own, or a
    # submodule the targets live in would silently be missing from the corpus.
    super_src, super_args = located[0]
    links = gitlinks(super_args, super_src["commit"])
    declared = {s["path"]: s["commit"] for s in sources[1:]}
    if links != declared:
        raise SystemExit(f"manifest submodules {declared} do not match the commit's gitlinks {links}")

    # Extract beside the corpus and rename, so an interrupted run never leaves a
    # half-written corpus that a later --reuse-corpus would trust.
    partial = corpus.with_name(corpus.name + ".partial")
    for d in (partial, corpus):
        if d.exists():
            shutil.rmtree(d)
    for src, args in located:
        archive_into(args, src["commit"], partial / src["path"] if src["path"] else partial)
    partial.rename(corpus)
    print(f"==> materialized {evalset['manifest']['corpus']['repo_url']} @ "
          f"{super_src['commit'][:12]} into {corpus}", file=sys.stderr)


def span_sha256(path: Path, start: int, end: int) -> str | None:
    """sha256 of lines start..end (1-based, inclusive) joined by "\\n", as bytes."""
    try:
        lines = path.read_bytes().split(b"\n")
    except (FileNotFoundError, IsADirectoryError):
        return None
    if start < 1 or end > len(lines) or start > end:
        return None
    return hashlib.sha256(b"\n".join(lines[start - 1:end])).hexdigest()


def verify_corpus(targets: list[dict], corpus: Path) -> None:
    """Every target span must hash to the value frozen in the set.

    Catches a wrong commit, a missing submodule, or line-ending rewriting,
    any of which would otherwise score as ordinary misses.
    """
    bad = [t for t in targets
           if span_sha256(corpus / t["path"], t["start_line"], t["end_line"]) != t["span_sha256"]]
    if bad:
        shown = "\n".join(f"  {t['path']}:{t['start_line']}-{t['end_line']}" for t in bad[:10])
        raise SystemExit(f"{len(bad)}/{len(targets)} target spans do not match the corpus at {corpus}:\n{shown}")


# ── index ────────────────────────────────────────────────────────────────────


def wait_for_index_lock(corpus: Path, timeout: float) -> None:
    """Block until no process holds the corpus's index run-lock.

    `inkentry index` returns while a detached child is still writing to the
    index (title-less refinement, conventions); querying before it exits
    measures an index that is still changing.
    """
    pid_file = corpus / ".inkentry" / "index.lock.pid"
    deadline = time.monotonic() + timeout
    while True:
        try:
            pid = int(pid_file.read_text().strip())
        except (FileNotFoundError, ValueError):
            return
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            pass
        if time.monotonic() > deadline:
            raise SystemExit(f"index lock still held by pid {pid} after {timeout:.0f}s")
        time.sleep(2)


def check_index(st: dict, mode: str) -> None:
    if st.get("index_rebuilt_from") is not None:
        raise SystemExit(f"the index was discarded from schema {st['index_rebuilt_from']} and is empty; re-index")
    if not st.get("total_chunks"):
        raise SystemExit("the index has no chunks")
    if mode == "hybrid":
        if st.get("embedding_pending") or st.get("embedding_refresh_pending"):
            raise SystemExit(
                f"embedding incomplete: {st.get('embedding_pending')} pending, "
                f"{st.get('embedding_refresh_pending')} awaiting refresh. Is inkentry-server running?")
        if not st.get("has_semantic_search"):
            raise SystemExit("no semantic search available (no reachable inkentry-server); "
                             "hybrid would silently degrade to full-text")
        url = st.get("server_url") or ""
        if not re.match(r"https?://(127\.0\.0\.1|localhost|\[::1\])(:|/|$)", url):
            raise SystemExit(f"inference resolved to {url!r}, not a loopback server")


def build_index(corpus: Path, mode: str, cache: Path, timeout: float) -> None:
    env, cfg = harness_env(mode, cache), config_file(cache)
    for d in (corpus / ".inkentry", corpus / ".spelunk"):
        if d.exists():
            print(f"==> removing {d}", file=sys.stderr)
            shutil.rmtree(d)
    print(f"==> indexing {corpus} ({mode})", file=sys.stderr)
    r = subprocess.run([inkentry_bin(), "--config", str(cfg), "index", str(corpus)],
                       cwd=corpus, env=env, stdout=sys.stderr)
    if r.returncode != 0:
        raise SystemExit(f"inkentry index exited {r.returncode}")
    wait_for_index_lock(corpus, timeout)
    check_index(status(corpus, cfg, env), mode)


# ── evaluate ─────────────────────────────────────────────────────────────────


def evaluate(evalset: dict, corpus: Path, mode: str, cache: Path, index_reused: bool) -> dict:
    manifest, targets = evalset["manifest"], {t["tid"]: t for t in evalset["targets"]}
    env, cfg = harness_env(mode, cache), config_file(cache)
    verify_corpus(evalset["targets"], corpus)
    wait_for_index_lock(corpus, timeout=60)
    st = status(corpus, cfg, env)
    check_index(st, mode)

    per_query, walls = [], []
    queries = evalset["queries"]
    for n, q in enumerate(queries, 1):
        t = targets[q["tid"]]
        items, wall = run_search(q["query"], mode, corpus, cfg, env)
        hits = ranked_hits(items)
        walls.append(wall)
        per_query.append({
            "qid": query_id(q), "tid": q["tid"], "style": q["style"], "language": t["language"],
            "rank": hit_rank(hits, t), "file_rank": hit_rank(hits, t, "file"),
            "n_results": len(hits), "wall_ms": round(wall * 1000, 1),
        })
        if n % 50 == 0 or n == len(queries):
            print(f"  {n}/{len(queries)} queries", file=sys.stderr)

    if not any(r["n_results"] for r in per_query):
        raise SystemExit("every query returned zero results: the index is empty or the output shape changed")

    scores = breakdown(per_query)
    c = manifest["corpus"]
    return {
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "benchmark": "codeknown",
        "evalset": manifest["name"],
        "evalset_version": manifest["version"],
        # Same labels as codesearchnet: the retrieval behaviour, with the literal
        # argv beside it.
        "condition": mode,
        "search_args": search_args(mode),
        "search_env": {"INKENTRY_MODE": env.get("INKENTRY_MODE")},
        "inkentry_version": inkentry_version(env),
        "inkentry_bin": shutil.which(inkentry_bin()) or inkentry_bin(),
        "inkentry_bin_sha256": binary_sha256(),
        # The server does not report its embedder's model id; the bundled one is
        # fixed per inkentry version, so inkentry_version identifies it.
        "model": "server-bundled embedder" if mode == "hybrid" else "none (full-text only)",
        "seed": None,
        "corpus": {"repo_url": c["repo_url"], "commit": c["commit"],
                   "submodules": c.get("submodules", []), "dir": str(corpus)},
        "index": {
            "reused": index_reused,
            "total_chunks": st.get("total_chunks"),
            "file_count": st.get("file_count"),
            "embedding_count": st.get("embedding_count"),
            # Chunks a build chose not to embed; absent from older builds.
            "text_only_count": st.get("text_only_count"),
            "summary_scheme": st.get("summary_scheme"),
            "last_indexed_at": st.get("last_indexed_at"),
            "tier": st.get("tier"),
            "server_url": st.get("server_url"),
        },
        "samples": len(per_query),
        "recall_at_1": scores["all"]["recall_at_1"],
        "recall_at_5": scores["all"]["recall_at_5"],
        "recall_at_10": scores["all"]["recall_at_10"],
        "mrr_at_10": scores["all"]["mrr_at_10"],
        "file_recall_at_10": scores["all"]["file_recall_at_10"],
        "median_wall_seconds": round(statistics.median(walls), 3),
        "metrics": scores,
        "per_query": per_query,
    }


def print_summary(result: dict) -> None:
    def row(label, m):
        return (f"  {label:<14s} n={m['n']:<4d} R@1 {m['recall_at_1']:.3f}  R@5 {m['recall_at_5']:.3f}  "
                f"R@10 {m['recall_at_10']:.3f}  MRR@10 {m['mrr_at_10']:.3f}  file-R@10 {m['file_recall_at_10']:.3f}")

    m = result["metrics"]
    print(f"{result['evalset']}/v{result['evalset_version']}  condition={result['condition']}  "
          f"{result['inkentry_version']}  corpus@{result['corpus']['commit'][:12]}")
    print(row("all", m["all"]))
    for s, v in m["by_style"].items():
        print(row(s, v))
    for lang, v in m["by_language"].items():
        print(row(lang, v))
    print(f"  median wall {result['median_wall_seconds']}s per query")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("phase", choices=("materialize", "verify", "index", "evaluate"))
    ap.add_argument("--set", required=True, dest="set_ref", help="Eval set as <name>/v<N>.")
    ap.add_argument("--mode", choices=MODES, default="text",
                    help="text: offline full-text index and --only-text queries. "
                         "hybrid: full embed through a local inkentry-server and default ranking.")
    ap.add_argument("--cache-dir", default=str(DEFAULT_CACHE),
                    help=f"Corpora, git fetches, config and registry (default: {DEFAULT_CACHE}).")
    ap.add_argument("--corpus-dir", default=None,
                    help="Corpus directory (default: <cache-dir>/corpora/<repo>-<commit>).")
    ap.add_argument("--repo-dir", default=None,
                    help="Local clone to git archive from, instead of fetching the manifest's URL.")
    ap.add_argument("--index-timeout", type=float, default=4 * 3600,
                    help="Seconds to wait for the index lock to release (default: 4h).")
    ap.add_argument("--index-reused", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--out", default=None, help="Write the result JSON here.")
    args = ap.parse_args()

    evalset = load_set(args.set_ref)
    cache = Path(args.cache_dir).expanduser()
    corpus = Path(args.corpus_dir).expanduser() if args.corpus_dir else default_corpus_dir(evalset["manifest"], cache)

    if args.phase == "materialize":
        materialize(evalset, corpus, cache, Path(args.repo_dir).expanduser() if args.repo_dir else None)
        verify_corpus(evalset["targets"], corpus)
        return
    if not corpus.is_dir():
        raise SystemExit(f"no corpus at {corpus}; run the materialize phase first, or use run.sh")
    if args.phase == "verify":
        verify_corpus(evalset["targets"], corpus)
        print(f"all {len(evalset['targets'])} target spans match {corpus}", file=sys.stderr)
        return
    if args.phase == "index":
        verify_corpus(evalset["targets"], corpus)
        build_index(corpus, args.mode, cache, args.index_timeout)
        return

    result = evaluate(evalset, corpus, args.mode, cache, args.index_reused)
    print_summary(result)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
        print(f"\nResults written to: {out}")


if __name__ == "__main__":
    main()
