# inkentry-bench

Benchmark harness for [inkentry](https://github.com/inkentries/inkentry): retrieval
quality, agent task completion, and indexing performance. Nothing here runs in
CI — these are manual runs, taken before and after changes to the indexer,
chunker, embedding pipeline, or ranking.

---

## Capture a retrieval baseline

**Anything that changes how results are ranked needs a before/after number.**
Some ranking regressions are invisible to tests: fusing scores computed under
different embedding instruct prefixes, for instance, produces a plausible
ordering that is quietly worse, and no assertion fails. A measured number is
the only thing that catches it.

One command, from a clean checkout:

```bash
INKENTRY_SECRET_STORE=file bash codesearchnet/run.sh --samples 500 --seed 0 --mode hybrid
```

That does all three phases: samples 500 CodeSearchNet Python functions, writes
them to disk as a corpus, indexes the corpus with your current `inkentry`
build, and queries it. It prints MRR@10, Recall@5 and Recall@10, and writes
`results/codesearchnet-hybrid-<timestamp>.json`.

**Then make your change, rebuild, and re-run with the same flags.** Two runs
are comparable when `--samples`, `--seed`, `--languages` and `--mode` match and
the corpus was rebuilt the same way; the result JSON records all of them plus
the inkentry version, so a pair of files can be checked rather than trusted.

Compare with:

```bash
python3 report.py results/codesearchnet-hybrid-<before>.json results/codesearchnet-hybrid-<after>.json
```

### Cost, and how to spend less of it

Indexing is the slow phase — it embeds every chunk, and on a laptop CPU that is
minutes for 500 functions and hours for a full repository. The query phase is
seconds.

- `--reuse-index` re-queries an existing corpus index. Use it to compare
  `--mode hybrid` against `--mode text` or `--mode semantic` without paying to
  index again. It is **not** valid for a before/after comparison of a change
  that affects indexing, chunking or embedding — those need a fresh index.
- `--reuse-corpus` keeps the materialized files but re-indexes them. This is
  the right flag for a ranking change: identical corpus, new index.
- `--samples 100` gives a fast smoke number. It is too small to trust for a
  reported figure — expect several points of noise.

### Options

`--languages python` (comma-separated; CodeSearchNet ships go, java,
javascript, php, python, ruby) · `--samples 500` · `--seed 0` ·
`--mode hybrid|semantic|text|auto` · `--corpus-dir DIR` (default
`~/.cache/inkentry-bench/codesearchnet`) · `--out FILE`

### What the number means

Each sample is a real function and its real docstring. The docstring is the
query; a hit is that function appearing in the top 10.

**The docstring is stripped from the indexed body**, because it is the query —
leaving it in lets full-text search match the query against itself and score a
perfect 1.0, which measures nothing and hides regressions. Pass
`--keep-docstrings` to reproduce the leaky variant; the result JSON records
which way it ran in `docstrings_kept`.

The corpus is assembled from sampled functions rather than whole repositories,
so absolute scores are not comparable to published CodeSearchNet numbers. They
are comparable to *each other*, which is what a before/after needs.

### The in-domain alternative

`ownrepo/golden_eval.py` builds a golden set straight out of an existing index
— every chunk with a symbol name and a doc comment becomes a (query, target)
pair. No downloads, no corpus to build, and it measures retrieval over real
inkentry code rather than sampled Python:

```bash
cd /path/to/indexed/repo
INKENTRY_SECRET_STORE=file python3 /path/to/inkentry-bench/ownrepo/golden_eval.py \
    --samples 150 --mode semantic --model-label <embedding-model>
```

It needs an already-indexed repository and is read-only on the database. Note
the leakage caveat in its module docstring: inkentry's embedding text includes
the doc comment, so absolute numbers run high. Fine for relative comparison,
not for a published figure.

---

## Overview

| Tier | Benchmark | Model | Cadence |
|------|-----------|-------|---------|
| **Primary** | CrossCodeEval | gemma-4-e2b-it (local) | Pre-release |
| **Primary** | SWE-bench | any OpenAI-compatible | Pre-release |
| Secondary | SWE-bench (Claude) | claude-sonnet-4-6 | Major releases only |
| Retrieval | CodeSearchNet | model-agnostic | On indexer/chunker/ranking changes |
| Retrieval | Own-repo golden set | model-agnostic | On indexer/chunker/ranking changes |
| Retrieval | Code-graph | model-agnostic | On graph/indexer changes |

---

## Prerequisites

- `inkentry` in PATH (build: `cargo build --release`). Set `INKENTRY_BIN` to
  point at a specific binary instead.
- **`INKENTRY_SECRET_STORE=file`.** Anything that links the inkentry crate
  blocks forever on a keyring unlock prompt without it. The run scripts export
  it; set it yourself when invoking a Python evaluator directly.
- `uv` in PATH — run scripts use `uv run` and install Python deps from
  `requirements.txt` automatically.
- Docker, for SWE-bench only.
- A local OpenAI-compatible server at `http://127.0.0.1:1234` with
  `gemma-4-e2b-it` loaded, for the Gemma benchmarks only.

## Baselines

One committed baseline exists, for CodeSearchNet retrieval:
`results/codesearchnet-baseline/`. It is the median of three repeats against a
recorded product commit, taken before the unified-search rank-fusion change,
and it is the reference point for ranking work measured against that commit.
Read its README before comparing anything to it — in particular, a comparison
is only meaningful at the same `--seed` and sample count.

Every other benchmark here predates the rename from the previous product, and
its recorded numbers were measured against that product's index, embedding
model and ranking — they are not inkentry numbers and comparing against them
would be misleading. Capture your own "before" run and keep the file.

The two places where pre-rename measurements survive are labelled as such:
`linearrag/results.json` and `linearrag/labels.json` (see `linearrag/README.md`).
Fabricated test fixtures live in `tests/fixtures/` and are labelled too. Other
runs land in `results/`, which is gitignored apart from the committed
baselines.

## SWE-bench repo setup

The SWE-bench local scripts expect each task's repo cloned at the pre-fix commit under `repos/<task_id>/`, with an `ISSUE.txt` alongside. Run once before benchmarking:

```bash
bash setup_repos.sh
```

This fetches task metadata from HuggingFace (`princeton-nlp/SWE-bench_Verified`) and clones each repo at the correct base commit. Re-running is idempotent; already-correct checkouts are skipped. Requires internet access and `uv`.

`--git-timeout` is enforced via a `timeout`/`gtimeout` binary. macOS ships neither; install GNU coreutils (`brew install coreutils`) for `gtimeout` to enforce it, otherwise the script runs git ops without a per-command timeout.

Options: `--tasks N` (first N only) · `--repos-dir DIR` · `--dataset SLUG`

**For Claude benchmarks (secondary):**
- `ANTHROPIC_API_KEY` in environment
- Docker

---

## RepoBench (cross-file completion)

Measures whether `inkentry_search` helps complete lines that require symbols from other files. Uses [RepoBench-Python](https://huggingface.co/datasets/tianyang/repobench_python_v1.1), `cross_file_first` split: the completion point requires a symbol introduced in another file, making it the most relevant split for measuring inkentry's retrieval benefit.

```bash
# Inkentry condition
bash gemma/crosscodeeval/run.sh --condition inkentry --repo-path /path/to/indexed/repo

# Control condition — run this too; there is no committed baseline to compare against
bash gemma/crosscodeeval/run.sh --condition baseline --samples 400
```

**Options:** `--split cross_file_first|cross_file_random|in_file` · `--samples 200` · `--model gemma-4-e2b-it` · `--api-base-url http://127.0.0.1:1234/v1`

**Metrics:** `exact_match`, `edit_similarity`, `identifier_recall`

---

## SWE-bench (local model)

Measures whether `inkentry_search` helps fix real GitHub issues. Uses the same 50-task slice as the Claude variant so results are directly comparable.

```bash
# Run agent + Docker harness in one step (recommended)
bash agents/swebench_run.sh \
    --condition inkentry_search \
    --model gemma-4-e2b-it \
    --api-base-url http://127.0.0.1:1234/v1 \
    --eval

# Agent run only (then eval separately)
bash agents/swebench_run.sh \
    --condition inkentry_search \
    --model gemma-4-e2b-it \
    --api-base-url http://127.0.0.1:1234/v1

# Evaluate a prior agent run
bash agents/swebench_eval.sh \
    --results results/swebench-inkentry_search-<timestamp>.json \
    --patches-dir patches/inkentry_search-<timestamp>
```

Repo checkouts are expected at `repos/<task_id>/` (via `setup_repos.sh`). Each directory must contain an `ISSUE.txt`. Patches are saved to `patches/<condition>-<timestamp>/` during the agent run. Pass `--eval` to automatically invoke the Docker harness after all tasks complete.

> **Note:** `gemma/swebench_local/run.sh` is retired; it always outputs `resolved=0` because it never ran the Docker harness. Use `agents/swebench_run.sh` instead.

**Metrics:** `resolve_rate` (via harness), `median_tokens_per_task`, `median_wall_seconds`

---

## SWE-bench (Claude) - secondary

```bash
bash swebench/run.sh --condition baseline --tasks 50
bash swebench/run.sh --condition inkentry  --tasks 50
```

Requires `ANTHROPIC_API_KEY`. Results go to `results/swebench-{condition}-{timestamp}.json`.

---

## Code-graph - call graph retrieval quality

Model-agnostic. Measures how well `inkentry graph` retrieves files containing callers/callees/implementers of a symbol, compared against `git grep` and `inkentry search` as baselines.

**Repo setup:** clone the benchmark repos somewhere *outside* this repository (to avoid polluting the inkentry index), then point the evaluator at them:

```bash
mkdir -p ~/inkentry-bench/repos
git clone https://github.com/BurntSushi/ripgrep      ~/inkentry-bench/repos/ripgrep
git clone https://github.com/django/django            ~/inkentry-bench/repos/django__django-12125
git clone https://github.com/sympy/sympy              ~/inkentry-bench/repos/sympy__sympy-20590
```

Index each repo with inkentry before running:

```bash
for repo in ~/inkentry-bench/repos/*/; do inkentry index "$repo"; done
```

**Run:**

```bash
# via --repos-dir flag
python graph/evaluate.py \
    --tasks graph/tasks.json \
    --repos-dir ~/inkentry-bench/repos \
    --k 10 \
    --out results/graph.json

# or via environment variable
export INKENTRY_BENCH_REPOS=~/inkentry-bench/repos
python graph/evaluate.py --tasks graph/tasks.json --k 10
```

**Metrics:** `precision@k`, `recall@k`, `F1` (averaged across all 42 tasks in three repos: ripgrep, django, sympy).

---

## CodeSearchNet — retrieval quality

Model-agnostic. Measures how accurately `inkentry search` retrieves code for
natural-language queries. `run.sh` builds and indexes its own corpus, so it does
not need a repository indexed beforehand.

See [Capture a retrieval baseline](#capture-a-retrieval-baseline) above for the
full invocation, the comparability rules, and the docstring-leakage caveat.

**Metrics:** `mrr_at_10`, `recall_at_5`, `recall_at_10`

---

## Comparing results

```bash
python report.py results/crosscodeeval-baseline-<ts>.json results/crosscodeeval-inkentry-<ts>.json
```

The inkentry run scripts print this comparison automatically when a baseline exists. You can also compare any two result files directly.

Example output:
```
| benchmark      | condition | model                    | exact_match | edit_sim | id_recall | med_wall_s |
|----------------|-----------|--------------------------|-------------|----------|-----------|------------|
| crosscodeeval  | baseline  | gemma-4-e2b-it (local)  | 0.170       | 0.481    | 0.224     | 3.800      |
| crosscodeeval  | inkentry   | gemma-4-e2b-it (local)  | 0.210       | 0.541    | 0.291     | 4.100      |
```

---

## Paired statistics

`report.py` gives a quick side-by-side of aggregate figures. For any *published*
agentic comparison, use `paired_stats.py` instead: it applies the reporting
standards below over per-task result files (arrays of records, or the
post-harness `{"aggregate": ..., "tasks": [...]}` form).

```bash
python paired_stats.py \
    tests/fixtures/swebench-local-baseline.json \
    tests/fixtures/swebench-local-treatment.json
```

Those two files are fabricated input for exercising the tool, not measurements
— see `tests/fixtures/README.md`.

What it computes:

- **McNemar's exact test**, paired by `task_id` on the task-level binary outcome
  (`resolved`/`passed`). Discordant pairs and an exact binomial p-value are
  reported. This is a paired test (not a two-proportion z-test) because both
  conditions run the same tasks. Multi-seed cells collapse to one outcome per
  task by majority vote before pairing.
- **Bootstrap 95% CIs** over per-seed cell means (`mean +/- half-width [lo, hi]`),
  reproducible via a fixed RNG seed. Needs n>=3 seeds.
- **Deterministic layers** (retrieval benchmarks, n=1) are stated as
  `deterministic, n=1` rather than given a fabricated CI.
- **Cell-labeled output:** every figure names its full cell (model, harness,
  condition, instance_filter, n). The tool **refuses (errors)** to aggregate
  across records with differing model / harness / condition; pass `--filter` to
  label the instance subset.
- **Negative results** are printed as `not significant`, never dropped.

**Power note:** the 50-task slice can only detect large effects (about +/-15pp).
Headline claims must come from the filtered subset or the 150+ question set.

Fabricated fixtures live in `tests/fixtures/`; real runs land in `results/`
(gitignored).

---

## Metrics reference

| Metric | Benchmark | Meaning |
|--------|-----------|---------|
| `exact_match` | CrossCodeEval | Fraction of completions that exactly match ground truth |
| `edit_similarity` | CrossCodeEval | Average SequenceMatcher ratio between prediction and ground truth |
| `identifier_recall` | CrossCodeEval | Fraction of identifiers in ground truth that appear in the prediction |
| `resolve_rate` | SWE-bench | Fraction of tasks where the patch passes all tests (set by harness) |
| `mrr_at_10` | CodeSearchNet, own-repo | Mean Reciprocal Rank at 10 |
| `recall_at_5/10` | CodeSearchNet, own-repo | Fraction of queries where ground truth appears in top 5/10 results |
| `median_tokens_per_task` | SWE-bench | Median total tokens per task |
| `median_wall_seconds` | All | Median wall-clock seconds per task/query |
