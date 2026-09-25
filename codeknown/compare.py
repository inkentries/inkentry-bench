#!/usr/bin/env python3
"""Paired comparison of two codeknown result JSONs over the same eval set.

Pairs the two runs query by query and reports, for Recall@10:

  - each run's R@10, and the difference B - A
  - a paired bootstrap 95% CI of that difference. Targets are resampled, not
    queries: each target carries three queries, and those are not independent
    draws, so resampling queries would give a CI that is too narrow
  - found only by A / found only by B: the discordant queries

Refuses to compare runs over different eval sets, different set versions, or
different query ids. Comparing two conditions (text against hybrid) over one
set is allowed; the conditions are printed so a reader can see which it is.

Usage:
    python3 codeknown/compare.py results/codeknown-A.json results/codeknown-B.json
"""

import argparse
import json
import random
import sys
from pathlib import Path

BOOTSTRAP_ITERS = 10000
BOOTSTRAP_SEED = 20260925  # fixed so a CI is reproducible
K = 10


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def hits_at_k(result: dict, k: int = K) -> dict[str, bool]:
    return {q["qid"]: q["rank"] is not None and q["rank"] <= k for q in result["per_query"]}


def check_comparable(a: dict, b: dict) -> None:
    """Raise ValueError unless a and b ran the same set, version and queries."""
    sa = (a.get("evalset"), a.get("evalset_version"))
    sb = (b.get("evalset"), b.get("evalset_version"))
    if None in sa or None in sb:
        raise ValueError("both files must be codeknown results (evalset and evalset_version)")
    if sa != sb:
        raise ValueError(f"refusing to compare different eval sets: {sa[0]}/v{sa[1]} vs {sb[0]}/v{sb[1]}")
    qa = {q["qid"] for q in a["per_query"]}
    qb = {q["qid"] for q in b["per_query"]}
    if qa != qb:
        raise ValueError(f"the runs cover different queries ({len(qa ^ qb)} differ); both must be complete runs")


def paired_bootstrap(a: dict, b: dict, tid_of: dict[str, object],
                     iters: int = BOOTSTRAP_ITERS, seed: int = BOOTSTRAP_SEED) -> tuple[float, float]:
    """95% percentile CI of R@k(b) - R@k(a), resampling targets with replacement."""
    by_target: dict[object, list[int]] = {}
    for qid in a:
        by_target.setdefault(tid_of[qid], []).append(int(b[qid]) - int(a[qid]))
    groups = list(by_target.values())
    rng = random.Random(seed)
    diffs = []
    for _ in range(iters):
        total = n = 0
        for _ in range(len(groups)):
            g = groups[rng.randrange(len(groups))]
            total += sum(g)
            n += len(g)
        diffs.append(total / n)
    diffs.sort()
    return diffs[int(0.025 * iters)], diffs[int(0.975 * iters)]


def compare(a: dict, b: dict) -> dict:
    check_comparable(a, b)
    ha, hb = hits_at_k(a), hits_at_k(b)
    tid_of = {q["qid"]: q["tid"] for q in a["per_query"]}
    n = len(ha)
    lo, hi = paired_bootstrap(ha, hb, tid_of)
    return {
        "n_queries": n,
        "n_targets": len(set(tid_of.values())),
        "r10_a": sum(ha.values()) / n,
        "r10_b": sum(hb.values()) / n,
        "r10_diff": (sum(hb.values()) - sum(ha.values())) / n,
        "ci95": (lo, hi),
        "only_a": sorted(q for q in ha if ha[q] and not hb[q]),
        "only_b": sorted(q for q in ha if hb[q] and not ha[q]),
    }


def describe(r: dict) -> str:
    return (f"{r.get('condition')}  {r.get('inkentry_version')}  "
            f"bin {str(r.get('inkentry_bin_sha256', ''))[:12]}  {r.get('run_id')}")


def report(a: dict, b: dict, c: dict) -> str:
    lo, hi = c["ci95"]
    verdict = "no detectable change (CI contains 0)" if lo <= 0 <= hi else (
        "B higher" if lo > 0 else "B lower")
    lines = [
        f"## {a['evalset']}/v{a['evalset_version']}: paired over {c['n_queries']} queries, {c['n_targets']} targets",
        "",
        f"A: {describe(a)}",
        f"B: {describe(b)}",
        "",
        "| run | R@1 | R@5 | R@10 | MRR@10 | file-R@10 |",
        "|-----|-----|-----|------|--------|-----------|",
    ]
    for label, r in (("A", a), ("B", b)):
        m = r["metrics"]["all"]
        lines.append(f"| {label} | {m['recall_at_1']:.3f} | {m['recall_at_5']:.3f} | {m['recall_at_10']:.3f} "
                     f"| {m['mrr_at_10']:.3f} | {m['file_recall_at_10']:.3f} |")
    lines += [
        "",
        f"R@10 B - A: {c['r10_diff']:+.3f}   95% CI [{lo:+.3f}, {hi:+.3f}] "
        f"(paired bootstrap over targets, {BOOTSTRAP_ITERS} iters)   {verdict}",
        f"found only by A: {len(c['only_a'])}   found only by B: {len(c['only_b'])}",
    ]
    if a.get("condition") != b.get("condition"):
        lines.append(f"note: conditions differ ({a.get('condition')} vs {b.get('condition')})")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("a", help="result JSON A (the baseline)")
    ap.add_argument("b", help="result JSON B (the change)")
    ap.add_argument("--list", action="store_true", help="also list the discordant query ids")
    args = ap.parse_args()
    a, b = load(args.a), load(args.b)
    try:
        c = compare(a, b)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(report(a, b, c))
    if args.list:
        print("\nonly A: " + " ".join(c["only_a"]))
        print("only B: " + " ".join(c["only_b"]))


if __name__ == "__main__":
    main()
