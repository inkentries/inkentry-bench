#!/usr/bin/env bash
# Capture a CodeSearchNet retrieval number for the current inkentry build.
#
# Materializes the sampled corpus, indexes it, and evaluates against it, so a
# bare invocation goes from nothing to a comparable number.
#
# Usage:
#   bash codesearchnet/run.sh [--languages python] [--samples 500] [--seed 0]
#                             [--only-text] [--corpus-dir DIR] [--out FILE]
#                             [--reuse-corpus] [--reuse-index]
#
# Queries run against the code corpus with the best-available ranking. Pass
# --only-text for the full-text-only condition, which needs no inference server.
#
# Two runs are comparable only if --languages, --samples and --seed match.
#
# Indexing is the slow part. Both modes that index delete the corpus's existing
# index first, because indexing is content-hash incremental: left in place, an
# unchanged corpus is entirely hash-skipped and the run re-measures the index
# that was already on disk — possibly one built by a different binary.
#
#   (no flag)        re-sample the corpus, delete the index, index, evaluate
#   --reuse-corpus   keep the corpus, delete the index, index, evaluate
#   --reuse-index    keep the corpus and the index, evaluate only

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Anything linking the inkentry crate blocks forever on a keyring prompt
# without this.
export INKENTRY_SECRET_STORE="${INKENTRY_SECRET_STORE:-file}"
INKENTRY_BIN="${INKENTRY_BIN:-inkentry}"
export INKENTRY_BIN

LANGUAGES="python"
SAMPLES="500"
SEED="0"
ONLY_TEXT=0
CONDITION="hybrid"
CORPUS_DIR="${HOME}/.cache/inkentry-bench/codesearchnet"
OUT=""
REUSE_CORPUS=0
REUSE_INDEX=0

usage() {
    grep '^#' "$0" | grep -v '#!/' | sed 's/^#[[:space:]]\{0,1\}//'
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --languages) LANGUAGES="$2"; shift 2 ;;
        --samples) SAMPLES="$2"; shift 2 ;;
        --seed) SEED="$2"; shift 2 ;;
        --only-text) ONLY_TEXT=1; CONDITION="text"; shift ;;
        --mode)
            # Exit 2 to match what `inkentry search --mode` itself does, and the
            # rows below to match its hint.
            echo "--mode was removed from inkentry search; this harness no longer takes it." >&2
            echo "  --mode text                 ->  --only-text" >&2
            echo "  --mode semantic|hybrid|auto ->  no flag; that is the default" >&2
            echo "  --mode ast-grep             ->  no replacement; structural search was removed" >&2
            exit 2
            ;;
        --corpus-dir) CORPUS_DIR="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --reuse-corpus) REUSE_CORPUS=1; shift ;;
        --reuse-index) REUSE_INDEX=1; REUSE_CORPUS=1; shift ;;
        -h|--help) usage ;;
        *) echo "unknown argument: $1" >&2; usage ;;
    esac
done

if ! command -v "$INKENTRY_BIN" >/dev/null 2>&1; then
    echo "inkentry not found in PATH (set INKENTRY_BIN to override)" >&2
    exit 1
fi

if [[ -z "$OUT" ]]; then
    TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
    OUT="${REPO_DIR}/results/codesearchnet-${CONDITION}-${TIMESTAMP}.json"
fi
mkdir -p "$(dirname "$OUT")"

PY=(python3)
if command -v uv >/dev/null 2>&1; then
    PY=(uv run --quiet --with-requirements "${REPO_DIR}/requirements.txt" python3)
fi

if [[ "$REUSE_CORPUS" -eq 0 ]]; then
    echo "==> materializing corpus (${LANGUAGES}, ${SAMPLES}/lang, seed ${SEED})"
    "${PY[@]}" "${SCRIPT_DIR}/evaluate.py" \
        --materialize \
        --corpus-dir "$CORPUS_DIR" \
        --languages "$LANGUAGES" \
        --samples "$SAMPLES" \
        --seed "$SEED"
fi

if [[ "$REUSE_INDEX" -eq 0 ]]; then
    # Re-materializing the corpus is not enough to force a re-index: the same
    # --seed writes byte-identical files, so every one of them is hash-skipped
    # and the "new" index is the old one. Delete it and mean it.
    for index_dir in "${CORPUS_DIR}/corpus/.inkentry" "${CORPUS_DIR}/corpus/.spelunk"; do
        if [[ -d "$index_dir" ]]; then
            echo "==> removing previous index at ${index_dir}"
            rm -rf "$index_dir"
        fi
    done
    echo "==> indexing corpus (this is the slow phase)"
    "$INKENTRY_BIN" index "${CORPUS_DIR}/corpus"
fi

echo "==> evaluating (condition: ${CONDITION})"
EVAL_ARGS=(--corpus-dir "$CORPUS_DIR" --out "$OUT")
if [[ "$ONLY_TEXT" -eq 1 ]]; then
    EVAL_ARGS+=(--only-text)
fi
"${PY[@]}" "${SCRIPT_DIR}/evaluate.py" "${EVAL_ARGS[@]}"
