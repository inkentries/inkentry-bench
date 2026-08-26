#!/usr/bin/env bash
# perf_search.sh — inkentry search latency benchmark
#
# Times `inkentry search` across different query lengths and retrieval
# conditions, reporting p50/p95/p99 latency.
#
# Usage:
#   ./perf_search.sh [REPO_PATH]
#
# Prerequisites:
#   - Repo must already be indexed
#   - python3 with statistics module

set -euo pipefail

INKENTRY="${INKENTRY:-inkentry}"

# REPO_PATH was accepted, echoed, and then never used: every search ran in the
# caller's cwd, so a run "against" another repo silently measured whatever was
# indexed here. Resolve both to absolute paths and cd once, which keeps the
# timed region free of a per-iteration subshell.
REPO="$(cd "${1:-.}" && pwd)"
if [[ "$INKENTRY" == */* ]]; then
    INKENTRY="$(cd "$(dirname "$INKENTRY")" && pwd)/$(basename "$INKENTRY")"
fi
cd "$REPO"

echo "=== Search latency ==="
echo "Repo:   ${REPO}"
echo "Binary: ${INKENTRY}"
echo

# Queries of varying lengths
QUERIES=(
    "error handling"
    "how does authentication work"
    "find all functions that parse command line arguments and validate user input"
)

# Retrieval conditions, as "label:flags". An empty flag list is the default
# best-available ranking over both corpora.
#
# Labelled `default` rather than `hybrid` on purpose. The JSON-writing harnesses
# keep `hybrid` as a frozen baseline key, but they record `search_args` beside
# it; this script prints to a terminal and writes no JSON, so the label is all a
# reader gets. `hybrid` there means `--only-code`, and here it would mean no
# flags at all — the same word for two different retrievals. The flags are
# echoed next to the label for the same reason.
CONDITIONS=("default:" "code-only:--only-code" "text:--only-text")

for entry in "${CONDITIONS[@]}"; do
    label="${entry%%:*}"
    # Written so an empty flag list stays safe under `set -u` on bash 3.2,
    # where expanding an empty array is an unbound-variable error.
    flags=()
    if [[ -n "${entry#*:}" ]]; then
        flags=("${entry#*:}")
    fi
    # Built from the string, not the array: expanding an empty array under
    # `set -u` on bash 3.2 is an unbound-variable error.
    flag_display="${entry#*:}"
    if [[ -z "$flag_display" ]]; then
        flag_display="no flags — both corpora"
    fi
    echo "--- Condition: ${label} [${flag_display}] ---"

    # A removed or renamed flag exits non-zero in microseconds, which reads as
    # a spectacular latency win rather than as a failure. Prove the invocation
    # works once before timing it ten times.
    if ! "$INKENTRY" search "${QUERIES[0]}" ${flags[@]+"${flags[@]}"} --limit 5 --format json > /dev/null; then
        echo "  invocation failed for condition ${label}, skipping" >&2
        echo
        continue
    fi

    TIMES=()
    for q in "${QUERIES[@]}"; do
        echo "  Query: \"${q}\""
        ITER=10
        RUN_TIMES=()

        for _ in $(seq 1 $ITER); do
            start=$(python3 -c "import time; print(int(time.time()*1000))")
            "$INKENTRY" search "$q" ${flags[@]+"${flags[@]}"} --limit 5 --format json > /dev/null 2>&1
            end=$(python3 -c "import time; print(int(time.time()*1000))")
            elapsed=$(( end - start ))
            RUN_TIMES+=("$elapsed")
        done

        # Compute stats via python
        STATS=$(python3 -c "
import statistics
times = [$(echo "${RUN_TIMES[*]}" | tr ' ' ',')]
times.sort()
p50 = times[len(times)//2]
p95 = times[int(len(times)*0.95)]
p99 = times[min(int(len(times)*0.99), len(times)-1)]
mean = statistics.mean(times)
print(f'{p50},{p95},{p99},{mean:.1f}')
")
        P50=$(echo "$STATS" | cut -d, -f1)
        P95=$(echo "$STATS" | cut -d, -f2)
        P99=$(echo "$STATS" | cut -d, -f3)
        MEAN=$(echo "$STATS" | cut -d, -f4)

        printf "    p50=%4sms  p95=%4sms  p99=%4sms  mean=%5sms\n" "$P50" "$P95" "$P99" "$MEAN"
    done
    echo
done
