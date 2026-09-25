# Eval sets

Versioned inputs for the retrieval evals, laid out as ADR-098 D4 (in the
inkentry repo) specifies: one directory per set at `evalsets/<name>/v<N>/`,
referred to everywhere by name and version (`code-knownitem-lago/v1`).

**A published version is frozen.** Do not edit its files, fix a typo in a query,
or drop a bad target. Any change is a new `v<N+1>` directory beside the old
one, so a series of results shows the break instead of silently mixing two
sets under one name. Each `manifest.json` records `"frozen": true` and says how
the set was built.

| set | harness | targets | queries | job |
|---|---|---|---|---|
| `code-knownitem-inkentry/v1` | `codeknown/` | 125 | 375 | guard |
| `code-knownitem-lago/v1` | `codeknown/` | 125 | 375 | guard |
| `code-knownitem-lago-uncovered/v1` | `codeknown/` | 30 | 90 | guard (targeted) |

The older suites (`codesearchnet/`, `graph/tasks.json`, `memory/questions-*.json`)
predate this layout and keep their own locations.
