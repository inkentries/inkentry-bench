#!/usr/bin/env bash
# git_notes_perf.sh — performance benchmark for GitNotesBackend
#
# Creates a synthetic git repo with ~5000 commits and ~50 inkentry notes
# (1% density), then times `inkentry memory list --backend git-notes`.
#
# Success criterion: <500ms for `list --limit 10` on a
# 5k-commit repo.
#
# Usage:
#   ./git_notes_perf.sh [COMMITS] [NOTE_PCT]
#
# Examples:
#   ./git_notes_perf.sh            # default: 5000 commits, 1% notes
#   ./git_notes_perf.sh 1000 5     # 1000 commits, 5% notes
#
# Prerequisites:
#   - inkentry binary in PATH (cargo install --path . or cargo build --release)
#   - git

set -euo pipefail

COMMITS="${1:-5000}"
NOTE_PCT="${2:-1}"
INKENTRY="${INKENTRY:-inkentry}"

# ── locate binary ────────────────────────────────────────────────────────────
if ! command -v "$INKENTRY" &>/dev/null; then
    # Try the release binary in the cargo target directory
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
echo "Parameters: COMMITS=$COMMITS, NOTE_PCT=$NOTE_PCT"
echo

# ── create fixture repo ──────────────────────────────────────────────────────
FIXTURE_DIR="$(mktemp -d)"
echo "Fixture repo: $FIXTURE_DIR"
trap 'rm -rf "$FIXTURE_DIR"' EXIT

cd "$FIXTURE_DIR"
git init -b main -q
git config user.email "bench@example.com"
git config user.name "Bench"

# Create initial commit
echo "benchmark fixture" > README.md
git add .
git commit --no-gpg-sign -q -m "init"

NOTE_INTERVAL=$(( 100 / NOTE_PCT ))  # attach a note every N commits
NOTES_WRITTEN=0

echo "Creating $COMMITS commits (attaching a note every $NOTE_INTERVAL commits)…"

for i in $(seq 1 "$COMMITS"); do
    echo "$i" > "file_$i.txt"
    git add . >/dev/null 2>&1
    git commit --no-gpg-sign -q --allow-empty -m "commit $i" >/dev/null 2>&1

    if (( i % NOTE_INTERVAL == 0 )); then
        SHA=$(git rev-parse HEAD)
        NOTE_JSON="{\"id\":$i,\"kind\":\"decision\",\"title\":\"decision $i\",\"body\":\"body for commit $i\",\"tags\":[],\"linked_files\":[],\"created_at\":$(date +%s),\"status\":\"active\"}"
        git notes --ref=inkentry add -f -m "$NOTE_JSON" "$SHA" 2>/dev/null
        NOTES_WRITTEN=$((NOTES_WRITTEN + 1))
    fi
done

echo "Created $COMMITS commits, attached $NOTES_WRITTEN notes."
echo

# ── benchmarks ───────────────────────────────────────────────────────────────
ms_now() {
    python3 -c "import time; print(int(time.time()*1000))"
}

run_timed() {
    local label="$1"; shift
    local start end elapsed_ms

    start=$(ms_now)
    ( cd "$FIXTURE_DIR" && "$INKENTRY" memory "$@" --backend git-notes > /dev/null )
    end=$(ms_now)
    elapsed_ms=$(( end - start ))

    printf "%-55s %4dms\n" "$label" "$elapsed_ms"

    if [[ "$label" == *"(primary)"* ]] && (( elapsed_ms > 500 )); then
        echo "  ⚠️  EXCEEDS 500ms threshold"
    fi
}

echo "=== Benchmark results ==="
run_timed "memory list --kind decision --limit 10  (primary)" \
    list --kind decision --limit 10

run_timed "memory list --limit 10" \
    list --limit 10

run_timed "memory list  (no limit, worst-case)" \
    list --limit 9999

echo
echo "=== Notes ==="
echo "  $NOTES_WRITTEN notes across $COMMITS commits ($NOTE_PCT% density)"
echo "  500ms is the target for 'list --limit 10' on a 5k-commit repo."
echo
echo "  Mitigation options if threshold is exceeded:"
echo "  A) Index file: maintain .git/notes/inkentry-index.ndjson"
echo "  B) Reverse log with early exit: --max-count=<limit*20> + early-exit"
echo "  C) Ref-per-kind: store under refs/notes/inkentry/<kind>/<sha>"
