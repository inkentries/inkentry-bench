# tests/fixtures/

**Every number in this directory is invented.** These files exist so
`paired_stats.py` and the harness tests have well-formed input to parse. They
are not measurements of inkentry, or of anything else — the per-task token
counts and wall times are literally identical across tasks because a human
typed them.

Do not cite them, compare against them, or copy them into `results/`.

| File | Feeds |
|------|-------|
| `swebench-local-baseline.json` | `paired_stats.py` — the control arm of a paired comparison |
| `swebench-local-treatment.json` | `paired_stats.py` — the treatment arm |
| `swebench-harness-matrix.json` | the provenance-contract test: one record per harness/endpoint/effort cell, checking every required field is carried through |

Real runs land in `results/`, which is gitignored.
