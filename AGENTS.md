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

The canonical contract is [JSON output: the envelope contract][contract], in the
inkentry repo — specified field by field and covered by that project's stability
policy. **Read it there.** What follows is only what is policy *here*: how these
harnesses unwrap, and which corpus they ask for.

[contract]: https://github.com/inkentries/inkentry/blob/main/docs/commands.md#json-output-the-envelope-contract

`inkentry search --format json` returns a list of fusion envelopes, one per hit
— `{type, fused_rank, fused_score, corpus_rank, code|memory}` — with the chunk
fields **nested** under `code` or `memory` according to `type`. Read
`item["code"]["file_path"]`, never `item["file_path"]`.

Earlier builds emitted those fields flat at the top level, so unwrap tolerantly.
That keeps a harness able to re-read results captured by an older binary:

```python
nested = item.get("code")
payload = nested if isinstance(nested, dict) else item
name, path = payload.get("name"), payload.get("file_path")
```

Do not restate the field list here. It has an owner now, and a second copy
drifts — and drift in this particular list is indistinguishable from a working
benchmark, which is the whole reason both documents exist.

### Five ways to read zero results from a healthy binary

Each returns nothing at full query latency, with nothing on stdout saying so.

**Reading the flat shape.** The case above: matches nothing, on every result.

**Treating exit 1 as "no results".** `search` is porcelain — no matches is exit
`0` with `[]`. Exit `1` means the query never ran (no project here, an
unreadable index) and exit `2` is a rejected argument. The convention where `1`
means an empty set belongs to `plumbing` commands, which reserve `2` for errors.
`graph/evaluate.py` calls both and needs both rules; its two blocks differ on
purpose, and `agents/agent.py` passes the convention into `_run_inkentry`.

**Deriving rank from position, over an appendix.** `--graph` call-graph
neighbours, `--expand-graph` relates-to neighbours and cross-project memory
entries are appended **after** the ranked members, with `fused_rank`,
`fused_score` and `corpus_rank` all `null`. None of them was ranked against the
query. A `find_rank` built on `enumerate()` scores them as ranks 6, 7, 8 and
inflates recall. Filter to `fused_rank is not None`, or read `fused_rank`.

**Iterating the top level under `--budget`.** `--budget` (mutually exclusive
with `--limit`) replaces the top-level array with an **object**, envelopes under
`results`. Nothing here passes it today; a consumer that iterates the top level
reads zero the moment something does.

**Querying an index an older binary wrote.** Not a parsing bug — an environment
one, and the likeliest of these in practice. An index whose schema predates the
running build is **not** opened in place: it is discarded and rebuilt *empty*,
and it stays empty until you re-index. Search then returns `[]` at exit 0 from a
perfectly healthy binary. The notice goes to **stderr**, so it lands in the
stream every harness here drops on the success path, and `--reuse-index` and
`--reuse-corpus` are the two flags most likely to walk into it.

`inkentry status --format json` reports `index_rebuilt_from` — the schema
version a rebuild discarded, null when nothing was discarded. That is the
machine-readable guard; `perf_scale.sh` already reads `status --format json` for
`chunk_count`, so the pattern is in the repo. No harness checks it yet.

Coverage, freshness and degradation notices go to **stderr**, so stdout stays
machine-clean. Every harness here captures stderr and drops it on the success
path — worth remembering when a run comes back inexplicably empty.

### Picking a corpus

Search covers code and memory in one ranked list. Every benchmark here that
measures code retrieval passes `--only-code`, for two reasons: the corpora are
source checkouts with no memory entries, so the unified default spends an extra
query embed on nothing (measured at roughly 70ms against 55ms per query), and
in the SWE-bench conditions it keeps `inkentry_search` from quietly becoming a
partial `inkentry_full`. Use `--only-memory` for memory retrieval and
`--only-text` for the full-text-only condition.

### Condition labels are frozen baseline keys

`hybrid` and `text` in the result JSON name retrieval *behaviour*, not flags.
They are kept so runs stay comparable with baselines captured before the flags
changed, and the literal argv is recorded beside them as `search_args`. Do not
"correct" them to match current flag names — that silently breaks comparison
against `results/codesearchnet-baseline/`.

`perf_search.sh` is the exception: it prints to a terminal and writes no
`search_args`, so a bare label there has nothing to disambiguate it. It calls
the no-flags condition `default` and echoes the flags next to it.

### Commands that moved

| Reach for | Use |
|---|---|
| a search mode | no flag (best-available); `--only-text` for full-text only |
| structural / ast-grep search | no replacement; it was removed |
| memory retrieval | `inkentry search <q> --only-memory` |
| a symbol plus neighbours | `inkentry search <sym> --graph` — appendix members are unranked, see above |
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
- Scratch results: `results/` (gitignored except the allowlisted subdirectories below)
- Committed baselines: `results/codesearchnet-baseline/` only, kept by an
  explicit allowlist in `results/.gitignore`. See the Baselines section of
  README.md before comparing anything to it.
- Plans and reports: `tmp/` (gitignored; use `git add -f` to commit)

### Configuration priority for API scripts
1. `--api-key` CLI flag
2. `DEEPSEEK_API_KEY` environment variable
3. `.env.local` auto-loaded via `python-dotenv`

### Model name
`deepseek-v4-flash` (not `deepseek-chat`).
