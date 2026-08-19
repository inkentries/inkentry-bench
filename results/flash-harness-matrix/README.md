# SWE-bench flash harness matrix

Real Docker-eval SWE-bench Verified results, 50-task slice (49 evaluated; one
task's upstream repo is unresolvable via the current HuggingFace metadata,
tracked separately), model held constant, harness and condition varied.

**Branding note:** this run predates this repo's product rename. The tooling
that produced it still reports itself as `spelunk-cli`/`spelunk-server`, and
the condition values in the raw JSON are `baseline`/`spelunk_search`/
`spelunk_full` rather than this repo's current `inkentry_search`/
`inkentry_full`. The data is copied through unmodified rather than
relabeled, since these are exactly the strings the tooling actually recorded
at the time; the prose below uses this repo's current terminology.

- **Model:** `deepseek-v4-flash`, via its native `/v1` endpoint (`harness=none`)
  and via [opencode](https://opencode.ai) (`harness=opencode`).
- **Conditions:** `baseline` (the shared base tool set, with none of the three
  inkentry tools added), `spelunk_search` (base tools plus semantic code
  search), `spelunk_full` (base tools plus semantic code search, code graph
  traversal, and project memory retrieval).
- **n=3 seeds per cell**, 18 runs total.
- **harness=claude-code excluded from this pass**: a real auth-isolation bug
  blocked it (a stored login was silently overriding an injected API token)
  until shortly before this matrix ran; that fix isn't yet exercised by a full
  benchmark pass.

## Results (mean resolve rate across 3 seeds)

| condition | harness=none | harness=opencode |
|---|---|---|
| baseline | 61.2% | 95.2% |
| spelunk_search | 64.6% | 97.3% |
| spelunk_full | 87.8% | 94.6% |

## Statistical significance (paired vs baseline, see `PAIRED-STATS.md`)

Only one of the four baseline comparisons is significant at this sample size
(McNemar exact test, p<0.05):

| comparison | delta | result |
|---|---|---|
| harness=none: baseline -> spelunk_search | +3.4pp | not significant |
| harness=none: baseline -> spelunk_full | **+26.5pp** | **significant (p=0.0023)** |
| harness=opencode: baseline -> spelunk_search | +2.1pp | not significant |
| harness=opencode: baseline -> spelunk_full | -0.7pp | not significant |

A 50-task slice only reliably detects large effects (roughly ±15pp): the
`spelunk_search` deltas above are real directionally but this slice cannot
confirm them statistically, and a larger question set would be needed for
that.

`opencode` starts from a much higher baseline (95.2%), and in the
`baseline` vs `spelunk_full` cell 45 of the 49 paired tasks pass under both
arms while none fail under both, leaving 4 discordant tasks in total. There is
almost no room left for any condition to move the resolve rate in that cell, so
read it as uninformative rather than as evidence for or against an effect.

## Telemetry and cost

Per-task telemetry was captured for every run in this matrix: `input_tokens`,
`output_tokens`, `turns` and `wall_seconds` sit on each task row of the 18
per-seed files. Aggregated per cell with

```bash
python agents/aggregate_telemetry.py --results-dir results/flash-harness-matrix
```

| Model | Harness | Condition | Filter | Tasks | Input tok mean(med) | Output tok mean(med) | Turns mean(med) | Wall s mean(med) | Raw $ | Effective $ | Priced |
|---|---|---|---|---|---|---|---|---|---|---|---|
| deepseek-v4-flash | none | baseline | - | 147 | 629,997 (519,843) | 9,668 (9,289) | 19 (20) | 110.1 (99.2) | $13.36 | $13.36 | yes |
| deepseek-v4-flash | none | spelunk_full | - | 147 | 560,938 (463,481) | 9,131 (8,929) | 17 (20) | 111.4 (92.7) | $11.92 | $11.92 | yes |
| deepseek-v4-flash | none | spelunk_search | - | 147 | 665,665 (546,511) | 10,161 (10,131) | 18 (20) | 112.4 (104.1) | $14.12 | $14.12 | yes |
| deepseek-v4-flash | opencode | baseline | - | 146 | 24,039 (18,662) | 4,415 (3,285) | 31 (24) | 173.2 (132.9) | $0.67 | $0.67 | yes |
| deepseek-v4-flash | opencode | spelunk_full | - | 147 | 21,952 (19,287) | 3,630 (3,267) | 26 (22) | 138.0 (114.8) | $0.60 | $0.60 | yes |
| deepseek-v4-flash | opencode | spelunk_search | - | 146 | 24,187 (19,296) | 3,898 (3,042) | 28 (23) | 158.8 (122.3) | $0.65 | $0.65 | yes |

Token, turn and wall figures are per-task mean (median) over all three seeds of
a cell, so `Tasks` is 49 evaluated tasks x 3 seeds, less the two `opencode`
task attempts that hit the 900 s harness timeout and recorded no measurement,
and one task skipped per file. `Raw $` is
the whole-cell total, extrapolated from the committed `deepseek-v4-flash` list
price in `agents/pricing.json` (verified 2026-07-10); per task that is $0.0909
(`none`/`baseline`), $0.0960 (`none`/`spelunk_search`), $0.0811
(`none`/`spelunk_full`), and, for the three `opencode` cells, $0.0046 (`baseline`), $0.0041
(`spelunk_full`) and $0.0045 (`spelunk_search`). No row in this matrix carries `cache_read_input_tokens`, so effective
cost equals raw cost throughout. The tool's prospective-cost projection for a
not-yet-run cell is omitted here, being unrelated to this run.

Read these as tokens-to-outcome, never as a token saving. Under `harness=none`,
`spelunk_full` reached 87.8% at 560,938 input tokens and $0.0811 per task,
`baseline` 61.2% at 629,997 tokens and $0.0909, `spelunk_search` 64.6% at
665,665 tokens and $0.0960. Tokens are the price of each outcome, not the
result: with n=3 seeds, no per-tool call counts recorded, and three tools
varying together in `spelunk_full`, this run cannot say what moved the token
profile, and a lower token count alongside a higher resolve rate is not by
itself a claim about efficiency.

The two harnesses differ by roughly 26x in input tokens per task (about 630k
under `harness=none` against about 24k under `opencode`, for the same model on
the same tasks). That is a large difference in how much context each harness
feeds the model, and it is worth holding next to the last caveat below, which
flags `opencode`'s resolve-rate gap as needing independent scrutiny before it
is read as a harness-quality finding.

## Files

- `swebench-<condition>[-opencode]-<timestamp>.json`: 18 per-seed result
  files (this repo's own `{aggregate, tasks}` format), local absolute paths
  scrubbed.
- `raw-eval-reports/`: the official `swebench` harness's own per-run report
  format (`total_instances`, `resolved_instances`, `completed_ids`, etc.),
  one per run, kept alongside for provenance/audit.
- `PAIRED-STATS.md`: full `paired_stats.py` output for all four baseline
  comparisons.

## Caveats

- **What the +26.5pp can be attributed to.** Under `harness=none`, the
  `spelunk_full` arm adds three tools at once to the shared base tool set:
  semantic code search, code graph traversal, and project memory retrieval.
  `baseline` is that same base tool set with none of the three added, and
  `spelunk_search` adds only the first.

    Two things differ between `baseline` and `spelunk_full`, not one. Besides
    the tools, the two arms get different system prompts: `baseline` gets the
    plain one, and both tool arms get a variant naming the three tools and
    directing the model to use them before opening files (`agents/agent.py`,
    `get_system_prompt`). So the +26.5pp is what that whole treatment bought,
    and this run cannot say how much of it was the tools and how much was
    being told to search first. It is not a memory result, not a search
    result, and not a tools-alone result.

    The cleaner contrast is `spelunk_search` to `spelunk_full`, which holds
    the prompt constant and varies two tools, graph traversal and memory. Note
    the prompt names all three tools in both arms, so the search arm is told
    about two it does not have. Isolating any single tool needs one condition
    per tool, with the prompt held fixed across all of them.
  - Memory was **not** structurally empty in this run. `setup_repos.sh` clones
    each task repo blobless (`--filter=blob:none --no-checkout`, which carries
    the complete commit graph) or falls back to a full clone, neither path
    using `--depth`, and only then checks out the task's base commit, leaving
    full ancestry in `.git`. `swebench_run.sh` runs
    `inkentry memory harvest --git-range HEAD~50..HEAD` per task on the
    `spelunk_full` arm, so harvest was pointed at up to 50 real upstream commits
    rather than at an empty history. How many each base commit actually had,
    and whether any harvest call succeeded, is not recoverable from what was
    recorded.
  - What was not recorded is how much memory actually reached the model on any
    given run: per-run memory contents and per-tool call counts are absent from
    the result rows, and harvest failures were swallowed by
    `2>&1 | tail -1 || true` (see the harvest caveat below). So the arm is
    known to have had history to harvest, and unknown in how much retrieved
    context it put in front of the model.

- Memory harvest (`spelunk_full`) was intermittently unreliable even after a
  fix for a DeepSeek `response_format` incompatibility landed mid-run: some
  batches still fail under back-to-back/concurrent load on the same
  inference server instance, not yet root-caused. Harvest is best-effort by
  design, so it degrades gracefully rather than blocking a run, but the
  `spelunk_full` numbers above may understate its ceiling if harvest
  reliability improves further.
- `opencode`'s large gap over the native harness (mid-90s% vs 60-90%) is
  worth independent scrutiny before treating it as a harness-quality finding
  rather than an artifact of this specific setup. A sample of `opencode`
  patches was spot-checked for corruption (none found), but this was not an
  exhaustive review.
