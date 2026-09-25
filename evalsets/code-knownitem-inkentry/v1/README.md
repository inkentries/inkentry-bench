# code-knownitem-inkentry/v1

Known-item code retrieval over inkentry's own repository at commit
`ead04c853b8d1991e6ec505b61503688a7132de0`. There are 125 targets, three queries
each (375):

- 100 Rust definitions (functions, structs, enums, traits), not test code
- 25 Markdown sections from `docs/`, excluding `docs/adr/`

Targets are a seeded random sample, at most one per file. `manifest.json`
records the seed and the filters.

A target is a path and a line span at the pinned commit. A query hits when a
top-10 result is in that file and its span overlaps the target's. Nothing in
the set refers to a chunk id, so it survives chunker changes. `span_sha256`
lets the harness confirm that the materialized corpus is the one the set was
built on.

## What it can claim

The queries were written by an LLM that saw each target: one plain-language
query, one of 2 to 5 keywords, and one that names an identifier. They are not
blind, and the identifier queries name the target's own symbols. Use the set as a **guard** (did a change break code
retrieval?) and as a development signal for ranking work. It cannot show that
retrieval is good, and it is not a comparison against other tools.

It is also inkentry's own code, which the people tuning the ranking know well.
Pair it with `code-knownitem-lago/v1` before reading anything into a change.

Run it with `codeknown/run.sh`; see the top-level README.
