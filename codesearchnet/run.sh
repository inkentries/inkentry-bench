#!/usr/bin/env bash
# Capture a CodeSearchNet retrieval number for the current inkentry build.
#
# Materializes the sampled corpus, indexes it, and evaluates against it, so a
# bare invocation goes from nothing to a comparable number.
#
# Usage:
#   bash codesearchnet/run.sh [--languages python] [--samples 500] [--seed 0]
#                             [--mode hybrid] [--corpus-dir DIR] [--out FILE]
#                             [--reuse-corpus] [--reuse-index]
#
# Two runs are comparable only if --languages, --samples and --seed match.
# Indexing is the slow part; --reuse-index skips it when only the query side
# changed, and --reuse-corpus keeps an already-materialized corpus.

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
MODE="hybrid"
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
        --mode) MODE="$2"; shift 2 ;;
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
    OUT="${REPO_DIR}/results/codesearchnet-${MODE}-${TIMESTAMP}.json"
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
    echo "==> indexing corpus (this is the slow phase)"
    "$INKENTRY_BIN" index "${CORPUS_DIR}/corpus"
fi

echo "==> evaluating (mode: ${MODE})"
"${PY[@]}" "${SCRIPT_DIR}/evaluate.py" \
    --corpus-dir "$CORPUS_DIR" \
    --mode "$MODE" \
    --out "$OUT"
