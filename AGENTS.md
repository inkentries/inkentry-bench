# AGENTS.md — Benchmark Coding Conventions

Operational findings and coding conventions for this repository.

---

## Benchmark Design Principles

These emerged from a review of the original benchmark suite, which failed most
of them:

1. **Every claim needs a control.** Multi-turn-with-inkentry must be compared
   against multi-turn-without-inkentry, not against single-shot. A lift that
   disappears when the baseline gets the same compute budget is not inkentry's lift.
2. **No question-from-corpus circularity.** Questions used to evaluate retrieval
   must be authored by a party that has not seen what is in the memory store.
3. **Correctness > activity.** "Turns used" and "tokens spent" without a
   correctness signal are activity metrics, not quality metrics. Every agent
   benchmark needs a verifiable success criterion.
4. **Honest sample sizes.** No n=1 demos in a report that compares means.
5. **The report should under-promise.** Where infrastructure is ready but
   results aren't in, say so explicitly. Don't list empty rows in the
   executive summary.

### Scaffolding vs. deliverable split

A benchmark lands in two halves: the framework, and the content it runs over —
question sets, task corpora, labelled relevance judgements. Shipping the
framework against a placeholder corpus is fine and often necessary, but it does
not make the benchmark done. Track the content-authoring half as its own piece
of work, and keep the README explicit about which half exists, so nobody reads
a wired-up script as a measured result.

---

## Python Benchmark Scripts

### Dependency management
- Use `uv run --quiet --with-requirements requirements.txt` for all Python
  scripts under ``. Never assume `python3` has the needed packages.
- `requirements.txt` is the single source of truth for Python deps.

### Secrets
- API keys live in `.env.local` (gitignored). Load via `python-dotenv` at
  the top of every script that calls an API:
  ```python
  from dotenv import load_dotenv
  _root = Path(__file__).resolve().parents[2]  # or parents[3] for nested scripts
  _dotenv = _root / ".env.local"
  if _dotenv.exists():
      load_dotenv(_dotenv)
  ```
- Fall back to `DEEPSEEK_API_KEY` env var, then `--api-key` flag.
- Record `api_key_source` in result JSON for auditability.

### Reproducibility contract
Every result JSON must include: `benchmark`, `condition`, `model`,
`model_source`, `api_base_url`, `inkentry_version`, `seed`, `timestamp`.
Add `scaffold_hash` for committed baselines.

### Seed plumbing
- `np.random.seed(args.seed)` for numpy.
- `seed=args.seed` passed to `client.chat.completions.create()`.
- Both must be present; numpy-only seeding was a bug caught in review.

### Incremental writes
For long-running benchmarks, write results incrementally (every N tasks) so a
crash doesn't lose everything. `batch_run.py` does this; `swebench_eval.sh`
writes the final envelope at the end.

---

## Shell Script Patterns

### Don't shadow `PATH`
```bash
# WRONG — overrides the shell's command search path
PATH="${entry#*:}"

# RIGHT — use a different name
REPO_DIR="${entry#*:}"
```

### inkentry status for aggregate counts
```bash
# WRONG — inkentry chunks wants a file, not a directory
CHUNKS=$(inkentry chunks --format json "$DIR" ...)

# RIGHT — inkentry status gives file_count and chunk_count
STATS=$(cd "$DIR" && inkentry status --format json)
FILES=$(echo "$STATS" | python3 -c "import json,sys; print(json.load(sys.stdin).get('file_count',0))")
CHUNKS=$(echo "$STATS" | python3 -c "import json,sys; print(json.load(sys.stdin).get('chunk_count',0))")
```

### Verify field names against actual output
Always smoke-test inkentry commands to confirm JSON field names before
committing. `file_count` vs `files` was a silent-zero bug caught in review.

---

## The `search --format json` shape harnesses must unwrap

This is the shape the harnesses in this repo parse. It is not the canonical
contract; that lives with the CLI. Re-check it against a real binary before
trusting it.

`inkentry search --format json` returns a flat list of fusion envelopes, one
per hit. The chunk fields sit **nested**, under `code` or `memory` according
to `type`:

```json
[
  {
    "type": "code",
    "fused_rank": 1,
    "fused_score": 0.0163,
    "corpus_rank": 1,
    "code": {
      "chunk_id": 71, "file_path": "batch_run.py", "name": "main",
      "language": "python", "node_type": "verbatim",
      "start_line": 110, "end_line": 171, "content": "...",
      "distance": 0.049, "from_graph": false, "token_count": 509
    }
  }
]
```

Read `item["code"]["file_path"]`, never `item["file_path"]`. Earlier builds
emitted those fields flat at the top level, so unwrap tolerantly. That keeps
a harness able to re-read results captured by an older binary:

```python
nested = item.get("code")
payload = nested if isinstance(nested, dict) else item
name, path = payload.get("name"), payload.get("file_path")
```

### Picking a corpus

Search covers code and memory in one ranked list. Every benchmark here that
measures code retrieval passes `--only-code`, for two reasons: the corpora are
source checkouts with no memory entries, so the unified default spends an extra
query embed on nothing (measured at roughly 70ms against 55ms per query), and
in the SWE-bench conditions it keeps `inkentry_search` from quietly becoming a
partial `inkentry_full`. Use `--only-memory` for memory retrieval and
`--only-text` for the full-text-only condition.

### Commands that moved

| Reach for | Use |
|---|---|
| a search mode | no flag (best-available); `--only-text` for full-text only |
| memory retrieval | `inkentry search <q> --only-memory` |
| a symbol plus neighbours | `inkentry search <sym> --graph` |
| raw graph edges (JSONL) | `inkentry plumbing graph-edges --symbol <sym>` |

The removed spellings exit 2 with a migration hint on stderr and print nothing
on stdout, so a harness that ignores the exit code records a whole run of empty
results at a few milliseconds each.

### set -euo pipefail with explicit failure handling
```bash
# Prefer explicit failure over || true
if ! "$INKENTRY" index "$REPO_DIR" >/dev/null 2>&1; then
    echo "FAILED — skipping repo" >&2
    continue
fi
```

---

## Common Silent-Failure Patterns

These bugs all produce zero/empty results without raising exceptions.
Smoke tests catch them; code review often misses them.

| Pattern | Example | Fix |
|---------|---------|-----|
| Field name mismatch | `r.get("files")` vs actual `file_count` | Smoke-test against real output |
| Bare `except Exception: return []` | FTS syntax error swallowed | Log to stderr, narrow exception types |
| FTS5 AND vs OR semantics | Multi-word query requires all tokens | OR-join with quoted tokens |
| FTS5 special characters | `?` in query breaks parser | Strip non-alphanumeric before MATCH |
| `git diff` misses untracked files | Agent creates new files via `write_file` | `git add -A && git diff --cached HEAD` |
| `source_ref` vs `commit` field | Memory entries use different SHA field | Check both with prefix match |
| Wrong harness output filename | SWE-bench version drift | Glob for file containing `"resolved"` key |
| Removed CLI flag, exit code ignored | `--mode` after it was dropped | Check the exit code; every query returning in ~4ms is the tell |
| Envelope change read as a flat object | `item["file_path"]` against a nested `code` payload | Unwrap `item["code"]`; real latency with zero hits is the tell |

---

## Project-Specific Conventions

### Directory layout
- `agents/` — SWE-bench agent and evaluation
- `memory/` — decision archaeology and cross-session handoff
- `graph/` — code-graph retrieval
- `codesearchnet/` — CodeSearchNet retrieval
- `ownrepo/` — in-domain golden-set retrieval
- `gemma/crosscodeeval/` — RepoBench cross-file completion
- `perf_*.sh` — performance benchmarks

### Graph benchmark repo references
`graph/tasks.json` stores a `"repo"` slug (e.g. `"ripgrep"`) rather than
a path. The evaluator resolves it against `--repos-dir` / `$INKENTRY_BENCH_REPOS`
at runtime. Repos must live **outside** this repository — cloning them under the
repo root would pollute the inkentry index. See `README.md` for setup.

### Output paths
- Scratch results: `results/` (gitignored except `.gitignore`)
- Committed baselines: none. See the Baselines section of README.md.
- Plans and reports: `tmp/` (gitignored; use `git add -f` to commit)

### Configuration priority for API scripts
1. `--api-key` CLI flag
2. `DEEPSEEK_API_KEY` environment variable
3. `.env.local` auto-loaded via `python-dotenv`

### Model name
`deepseek-v4-flash` (not `deepseek-chat`).
