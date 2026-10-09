# code-knownitem-lago-railsbody/v1

A known-item set over [getlago/lago](https://github.com/getlago/lago) at the
same commit as `code-knownitem-lago/v1` (`02a4bc8d`, with its `api` and
`front` submodules). Each of its 50 targets is **one statement** from the
class body of a large Rails model: an association, scope, validation, enum,
`monetize`, callback or `delegate`. There are three queries per target (150).

It exists because these declarations sit outside any method, and a chunker
that splits a large class into its methods has to do something else with
them. In `code-knownitem-lago-uncovered/v1` the Rails targets are whole
regions of up to 65 lines, so any chunk near the top of the model counts as a
hit. Here a target is a single statement (one to eighteen lines), so a hit
means the result actually holds the declaration that was asked about.

Targets are a seeded random sample, at most two per file, from models whose
class is longer than one 512-token chunk; `manifest.json` records the full
procedure. The queries were written by an LLM shown the statement, its class
and a few lines around it, and nothing about how the corpus is indexed.

## Licence

lago is AGPL-3.0. This set holds only paths, line numbers, a short label per
target (class and macro name), a sha256 of each target span, and queries
written for this set. No lago code is copied into this repository.

## What it can claim

The queries are not blind: their author saw each target, and the identifier
style names the target's own symbols by construction. Use the set as a guard
and a development signal for class-level declarations in large classes, not
as evidence of retrieval quality in general, and never pool it with the other
lago sets.

Run it with `codeknown/run.sh`. It shares a corpus with the other lago sets,
so `--reuse-index` after a lago run evaluates it without re-indexing.
