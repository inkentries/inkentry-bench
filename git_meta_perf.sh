#!/usr/bin/env bash
# git_meta_perf.sh — performance benchmark for GitMetaBackend (default)
#
# Creates a synthetic git repo with ~5000 commits and populates inkentry memory
# via `memory add` at 100% density (every commit), then times
# `inkentry memory list --kind decision --limit 10`.
#
# Target: <100ms on a 5k-commit repo at 100% note density.
#
# Usage:
#   ./git_meta_perf.sh [COMMITS]
#
# Prerequisites:
#   - inkentry binary in PATH
#   - git
#   - A inkentry index of the fixture repo (`inkentry index .`)

set -euo pipefail

COMMITS="${1:-5000}"
INKENTRY="${INKENTRY:-inkentry}"

# ── locate binary ──────────────────────────────────────────────────────────
if ! command -v "$INKENTRY" &>/dev/null; then
    REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null || echo .)"
    if [ -x "$REPO_ROOT/target/release/inkentry" ]; then
        INKENTRY="$REPO_ROOT/target/release/inkentry"
    elif [ -x "$REPO_ROOT/target/debug/inkentry" ]; then
        INKENTRY="$REPO_ROOT/target/debug/inkentry"
    else
        echo "ERROR: inkentry not found. Build with: cargo build --release" >&2
        exit 1
    fi
fi

echo "Using: $INKENTRY"
echo "Parameters: COMMITS=$COMMITS (100% density)"
echo

# ── create fixture repo ────────────────────────────────────────────────────
FIXTURE_DIR="$(mktemp -d)"
echo "Fixture repo: $FIXTURE_DIR"
trap 'rm -rf "$FIXTURE_DIR"' EXIT

cd "$FIXTURE_DIR"
git init -b main -q
git config user.email "bench@example.com"
git config user.name "Bench"

echo "benchmark fixture for GitMetaBackend" > README.md
mkdir -p src
echo "fn main() { println!(\"hello\"); }" > src/main.rs
git add .
git commit --no-gpg-sign -q -m "init"

echo "Creating $COMMITS commits…"
for i in $(seq 1 "$COMMITS"); do
    echo "// commit $i" >> src/main.rs
    git add src/main.rs >/dev/null 2>&1
    git commit --no-gpg-sign -q --allow-empty -m "commit $i" >/dev/null 2>&1
done
echo "Created $COMMITS commits."

# ── index the repo (required for memory) ────────────────────────────────────
echo "Indexing repo..."
"$INKENTRY" index "$FIXTURE_DIR" 2>/dev/null

# ── populate memory at 100% density ─────────────────────────────────────────
echo "Populating memory ($COMMITS notes)..."
for i in $(seq 1 "$COMMITS"); do
    "$INKENTRY" memory add \
        --kind decision \
        --title "decision $i" \
        --body "Body for commit $i. This is a synthetic decision entry for benchmarking GitMetaBackend at scale." \
        --tags "bench,perf" \
        2>/dev/null
done
echo "Populated $COMMITS notes."

# ── benchmarks ──────────────────────────────────────────────────────────────
ms_now() {
    python3 -c "import time; print(int(time.time()*1000))"
}

run_timed() {
    local label="$1"; shift
    local start end elapsed_ms

    start=$(ms_now)
    # Takes the full argv rather than injecting a `memory` subcommand: memory
    # retrieval moved from `memory search` onto `search --only-memory`, which is
    # not under `memory` at all.
    ( cd "$FIXTURE_DIR" && "$INKENTRY" "$@" > /dev/null )
    end=$(ms_now)
    elapsed_ms=$(( end - start ))

    printf "%-55s %4dms\n" "$label" "$elapsed_ms"

    if [[ "$label" == *"(primary)"* ]] && (( elapsed_ms > 100 )); then
        echo "  ⚠️  EXCEEDS 100ms target"
    fi
}

echo
echo "=== Benchmark results ==="
run_timed "memory list --kind decision --limit 10  (primary)" \
    memory list --kind decision --limit 10

run_timed "memory list --limit 10" \
    memory list --limit 10

run_timed "search 'benchmark' --only-memory --limit 5" \
    search "benchmark" --only-memory --limit 5

echo
echo "=== Notes ==="
echo "  $COMMITS notes at 100% density on $COMMITS commits"
echo "  100ms is the target for 'list --kind decision --limit 10'."
echo "  Compare with git_notes_perf.sh results for backend selection."
