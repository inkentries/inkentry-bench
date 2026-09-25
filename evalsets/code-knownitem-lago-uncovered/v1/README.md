# code-knownitem-lago-uncovered/v1

A **targeted** known-item set over [getlago/lago](https://github.com/getlago/lago)
at the same commit as `code-knownitem-lago/v1` (`02a4bc8d`, with its `api` and
`front` submodules). There are 30 targets, three queries each (90):

- 20 TypeScript/TSX functions under `front/` defined through a `const` binding,
  almost all arrow functions: components, hooks and utilities
- 10 class-body regions of Rails models under `api/app/models/`: includes,
  constants, associations and scopes that sit outside any method

Chunkers before inkentry commit `60694a1b` left all of this code without a
chunk. The targets were chosen by hand to exercise that code, and the
selection script was not kept. This is **not a random sample**, which is why
it is a set of its own. Its numbers describe how well those two shapes of code
are found. They say nothing about retrieval in general, and must never be
pooled with `code-knownitem-lago/v1`.

The hit rule is the same span-overlap rule as the other sets. The Ruby targets
span large regions (up to 65 lines at the top of a model file), so for them a
chunk hit is close to a file hit.

## Licence

lago is AGPL-3.0. This set holds only paths, line numbers, symbol names, a
sha256 of each target span, and queries written for this set. No lago code is
copied into this repository.

## What it can claim

The queries were written by an LLM that saw each target. They are not blind,
and at 30 targets the set is small. Use it as a guard for coverage of these
code shapes, and nothing more.

Run it with `codeknown/run.sh`; see the top-level README. Because this set and
`code-knownitem-lago/v1` share a corpus, `--reuse-index` after a lago run
evaluates this one without re-indexing.
