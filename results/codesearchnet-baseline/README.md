# CodeSearchNet retrieval baseline (pre-unified-search)

The reference retrieval number for `inkentry`, captured so that ranking changes
have something honest to be judged against.

**File:** `codesearchnet-hybrid-pre-unified-search-20260811T083245Z.json`

## What was measured

| | |
|---|---|
| Product commit | `c3b6a9a1ce6efc66ed7280b64b9aac47b493986c` (`inkentry`, `main`) |
| Harness commit | `49020ff54ac1d392221c402857b8ce831490dacc` (this repo) |
| Date (UTC) | 2026-08-11 |
| Command | `bash codesearchnet/run.sh --samples 500 --seed 0 --mode hybrid` |
| Samples / seed / mode | 500 · 0 · hybrid |
| Corpus | 500 CodeSearchNet Python functions across 437 files, docstrings stripped |
| Repeats | 3 |

The product commit **includes** the deterministic structural summaries and the
PageRank-ordered tiered embed queue. It **excludes** unified-search rank fusion.
That is the point of this file: it is the "before" for unified search and for
subsequent ranking work.

## Results

Median of 3 repeats, with the full observed range:

| Metric | Median | Min | Max | Range |
|---|---|---|---|---|
| MRR@10 | **0.1924** | 0.1912 | 0.1928 | 0.0016 |
| Recall@5 | **0.3520** | 0.3500 | 0.3560 | 0.0060 |
| Recall@10 | **0.6540** | 0.6500 | 0.6560 | 0.0060 |

Median and range rather than mean and standard deviation: with three repeats a
mean is pulled around by any single odd run, and the thing a reader needs is
how far apart the repeats actually landed.

A full repeat takes roughly **6 minutes** (371 s, 387 s, 366 s), almost all of
it indexing.

## How the repeats were run

Each repeat used the **same materialized corpus** and a **freshly built index**,
so what varies between them is the indexing and ranking pipeline rather than
which functions were sampled.

Note that `--reuse-corpus` alone does not achieve this. Indexing is content-hash
incremental: with the previous index still on disk, every file is hash-skipped
and the "repeat" re-measures the index the previous repeat built. Each repeat
here therefore deletes the corpus's `.inkentry/` directory first.

Embeddings were served by a dedicated `inkentry-server` built from the same
commit, on an ephemeral loopback port, with its own database and an offline
`--model-dir` (F2LLM-v2-330M Q8_0). Sharing an embedder with an
already-running server built from different code would have quietly changed
what was being measured.

## Reading the spread

Two different kinds of variance matter here, and they are easy to conflate:

- **Pipeline variance** — same corpus, fresh index, as measured above:
  **0.006 on Recall@10** (0.6pt) across three repeats.
- **Corpus-sampling variance** — what you get if the corpus is re-materialized
  under a different `--seed`. For 500 samples at a recall of 0.65 the binomial
  standard error alone is about **0.021** (2.1pt).

So a before/after pair is only sensitive to small effects if both sides use the
same `--seed` and the same corpus. Changing the seed between the "before" and
the "after" reintroduces roughly 2pt of noise and swamps most ranking changes.

One caveat on the numbers above: three repeats is a thin basis for a variance
estimate, and the range of three samples systematically understates the true
spread. Treat 0.006 as a lower bound on pipeline noise, not a precise figure.

Also note that the harness records only aggregate metrics, not per-query ranks.
A paired per-query test (see `paired_stats.py`) is the sharper instrument for
before/after comparison, but it needs per-query output the result JSON does not
currently carry.
