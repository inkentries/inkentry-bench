# linearrag/

LinearRAG vs. plain KNN retrieval comparison: 20 queries across three
categories (structural, semantic multi-hop, cross-cutting), top-10 results and
latency for both algorithms, scored against hand-authored relevance labels.

```bash
python3 linearrag/run_eval.py collect   # query both algorithms, write results.json
# label the relevant chunks in labels.json, then:
python3 linearrag/run_eval.py metrics
```

## About the committed `results.json` and `labels.json`

**These are historical, pre-rename measurements. They are not inkentry
numbers.** They were collected against the predecessor product — a
`spelunk`-branded build, on that product's own source tree, with that
product's embedding model and ranking. They are checked in because the
relevance labels in `labels.json` were authored by hand and are expensive to
reproduce, not because the scores are current.

Consequences, stated plainly:

- The file paths, symbol names and snippets inside both files are from the
  predecessor repository. They are recorded output, so they were deliberately
  **not** rewritten during the rename; editing measurement data to match a new
  brand name would misrepresent what was measured.
- Nothing in this repository compares a fresh run against these files, and
  nothing should. `run_eval.py collect` overwrites `results.json`.
- Any published LinearRAG figure for inkentry needs a fresh collect-and-label
  pass on an inkentry index.
