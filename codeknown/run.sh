#!/usr/bin/env bash
# Run a known-item code-retrieval eval set against the current inkentry build.
#
# Materializes the set's pinned repository commit, indexes it fresh, and runs
# every query through `inkentry search`, so a bare invocation goes from nothing
# to a result JSON.
#
# Usage:
#   bash codeknown/run.sh --set code-knownitem-inkentry/v1 [--mode text|hybrid]
#                         [--repo-dir DIR] [--cache-dir DIR] [--corpus-dir DIR]
#                         [--out FILE] [--reuse-corpus] [--reuse-index]
#
#   --mode text      (default) offline index: parse and full-text only, seconds,
#                    no inference server; queries run with --only-text
#   --mode hybrid    full index with embeddings through a local inkentry-server
#                    (about 10 minutes for inkentry, an hour for lago); queries
#                    run with the default best-available ranking
#   --repo-dir DIR   local clone to `git archive` from (its submodules checked
#                    out); without it the commit is fetched from the set's URL
#
# Every mode that indexes deletes <corpus>/.inkentry first, because indexing is
# content-hash incremental (an unchanged corpus would be hash-skipped and the run
# would re-measure whatever index was on disk) and because a repository can
# commit its own .inkentry/config.toml (inkentry's says `cloud = true`), which
# would otherwise route embedding to the hosted cloud.
#
#   (no flag)        re-materialize the corpus, delete the index, index, evaluate
#   --reuse-corpus   keep the corpus (spans re-verified), delete the index, index, evaluate
#   --reuse-index    keep the corpus and the index, evaluate only

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

INKENTRY_BIN="${INKENTRY_BIN:-inkentry}"
export INKENTRY_BIN

SET=""
MODE="text"
SRC_REPO_DIR=""
CACHE_DIR=""
CORPUS_DIR=""
OUT=""
REUSE_CORPUS=0
REUSE_INDEX=0

usage() {
    grep '^#' "$0" | grep -v '#!/' | sed 's/^#[[:space:]]\{0,1\}//'
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --set) SET="$2"; shift 2 ;;
        --mode) MODE="$2"; shift 2 ;;
        --repo-dir) SRC_REPO_DIR="$2"; shift 2 ;;
        --cache-dir) CACHE_DIR="$2"; shift 2 ;;
        --corpus-dir) CORPUS_DIR="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --reuse-corpus) REUSE_CORPUS=1; shift ;;
        --reuse-index) REUSE_INDEX=1; REUSE_CORPUS=1; shift ;;
        -h|--help) usage ;;
        *) echo "unknown argument: $1" >&2; usage ;;
    esac
done

if [[ -z "$SET" ]]; then
    echo "--set is required, e.g. --set code-knownitem-inkentry/v1 (see evalsets/)" >&2
    exit 2
fi
if [[ "$MODE" != "text" && "$MODE" != "hybrid" ]]; then
    echo "--mode must be text or hybrid, got: $MODE" >&2
    exit 2
fi
if ! command -v "$INKENTRY_BIN" >/dev/null 2>&1; then
    echo "inkentry not found in PATH (set INKENTRY_BIN to override)" >&2
    exit 1
fi

if [[ -z "$OUT" ]]; then
    TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
    OUT="${REPO_DIR}/results/codeknown-${SET%/*}-${SET#*/}-${MODE}-${TIMESTAMP}.json"
fi

PY=(python3)
if command -v uv >/dev/null 2>&1; then
    PY=(uv run --quiet --with-requirements "${REPO_DIR}/requirements.txt" python3)
fi

COMMON=(--set "$SET" --mode "$MODE")
[[ -n "$CACHE_DIR" ]] && COMMON+=(--cache-dir "$CACHE_DIR")
[[ -n "$CORPUS_DIR" ]] && COMMON+=(--corpus-dir "$CORPUS_DIR")

if [[ "$REUSE_CORPUS" -eq 0 ]]; then
    echo "==> materializing corpus for ${SET}"
    MAT_ARGS=("${COMMON[@]}")
    [[ -n "$SRC_REPO_DIR" ]] && MAT_ARGS+=(--repo-dir "$SRC_REPO_DIR")
    "${PY[@]}" "${SCRIPT_DIR}/evaluate.py" materialize "${MAT_ARGS[@]}"
fi

EVAL_ARGS=("${COMMON[@]}" --out "$OUT")
if [[ "$REUSE_INDEX" -eq 0 ]]; then
    "${PY[@]}" "${SCRIPT_DIR}/evaluate.py" index "${COMMON[@]}"
else
    echo "==> reusing the existing index (it may have been built by another binary)"
    EVAL_ARGS+=(--index-reused)
fi

echo "==> evaluating ${SET} (condition: ${MODE})"
"${PY[@]}" "${SCRIPT_DIR}/evaluate.py" evaluate "${EVAL_ARGS[@]}"
