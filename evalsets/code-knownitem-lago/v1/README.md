# code-knownitem-lago/v1

Known-item code retrieval over [getlago/lago](https://github.com/getlago/lago)
at commit `02a4bc8d9c0ab4201875d8615a09c48a2f2a4d31`. It includes the `api`
(`731388fc`) and `front` (`dbde5273`) submodules at the commits that commit
pins. There are 125 targets, three queries each (375):

- 70 Ruby methods and classes under `api/`
- 20 TypeScript/TSX functions, methods, interfaces, classes and type aliases under `front/`
- 15 Go functions, methods and structs
- 20 Markdown sections

Targets are a seeded random sample, at most one per file, with test,
vendored and `extra/` code excluded. `manifest.json` records the seed and the
filters. When the set was sampled, the chunker produced no chunk for a
function bound with `const f = () => ...`, so no such function could be
picked. `code-knownitem-lago-uncovered/v1` covers them.

A target is a path and a line span at the pinned commit. A query hits when a
top-10 result is in that file and its span overlaps the target's. Nothing in
the set refers to a chunk id, so it survives chunker changes.

## Licence

lago is AGPL-3.0. This set holds only paths, line numbers, symbol names, a
sha256 of each target span, and queries written for this set. No lago code is
copied into this repository. The harness fetches the source at run time.

## What it can claim

The queries were written by an LLM that saw each target: one plain-language
query, one of 2 to 5 keywords, and one that names an identifier. They are not
blind, and the identifier queries name the target's own symbols. Use the set as a **guard** (did a change break code
retrieval?) and as a development signal for ranking work, on a codebase
nobody here tuned against. It cannot show that retrieval is good, and it is
not a comparison against other tools.

Run it with `codeknown/run.sh`; see the top-level README.
