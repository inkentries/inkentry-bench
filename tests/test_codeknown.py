"""codeknown: the hit rule, the metrics, the compare guard, and the frozen sets.

The hit rule and the metrics are what every number the harness prints rests
on; a mistake in either scores plausibly and raises nothing. The compare guard
is what stops a paired comparison across two different sets. The eval-set
checks hold the committed files to the shape the harness and the manifest
promise, since a set is frozen once published and can only be replaced by a
new version.

Run: uv run --with pytest pytest tests/test_codeknown.py -q
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ck = _load("codeknown_evaluate", "codeknown/evaluate.py")
cmp = _load("codeknown_compare", "codeknown/compare.py")

TARGET = {"path": "src/a.rs", "start_line": 10, "end_line": 20}


def res(path="src/a.rs", start=10, end=20):
    return {"path": path, "start": start, "end": end}


def envelope(path="src/a.rs", start=10, end=20, rank=1):
    return {"type": "code", "fused_rank": rank, "fused_score": 0.1, "corpus_rank": rank,
            "code": {"file_path": path, "start_line": start, "end_line": end, "name": "f"}}


# ── hit rule ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("start,end", [(10, 20), (1, 10), (20, 30), (12, 14), (1, 100)])
def test_overlapping_span_in_the_same_file_is_a_hit(start, end):
    assert ck.hit_rank([res(start=start, end=end)], TARGET) == 1


@pytest.mark.parametrize("start,end", [(1, 9), (21, 30)])
def test_adjacent_span_is_a_miss(start, end):
    assert ck.hit_rank([res(start=start, end=end)], TARGET) is None


def test_other_file_with_the_same_span_is_a_miss():
    assert ck.hit_rank([res(path="src/b.rs")], TARGET) is None


def test_path_match_is_exact_not_suffix():
    assert ck.hit_rank([res(path="x/src/a.rs")], TARGET) is None


def test_rank_is_the_first_hit():
    results = [res(path="src/b.rs"), res(start=1, end=5), res(start=15, end=16), res()]
    assert ck.hit_rank(results, TARGET) == 3


def test_file_level_ignores_the_span():
    results = [res(path="src/b.rs"), res(start=1, end=5)]
    assert ck.hit_rank(results, TARGET) is None
    assert ck.hit_rank(results, TARGET, "file") == 2


def test_only_the_top_ten_count():
    results = [res(path="src/b.rs")] * 10 + [res()]
    assert ck.hit_rank(results, TARGET) is None
    assert ck.hit_rank(results[1:], TARGET) == 10


def test_ranked_hits_reads_the_nested_envelope():
    assert ck.ranked_hits([envelope(start=3, end=4)]) == [{"path": "src/a.rs", "start": 3, "end": 4}]


def test_ranked_hits_tolerates_the_flat_shape():
    flat = {"file_path": "src/a.rs", "start_line": 3, "end_line": 4, "name": "f"}
    assert ck.ranked_hits([flat]) == [{"path": "src/a.rs", "start": 3, "end": 4}]


def test_ranked_hits_drops_unranked_appendix_members():
    appendix = envelope(path="src/graph.rs", rank=None)
    appendix["fused_rank"] = None
    hits = ck.ranked_hits([envelope(path="src/b.rs"), appendix, envelope()])
    assert [h["path"] for h in hits] == ["src/b.rs", "src/a.rs"]
    assert ck.hit_rank(hits, TARGET) == 2


def test_ranked_hits_skips_memory_envelopes_and_junk():
    memory = {"type": "memory", "fused_rank": 1, "memory": {"id": "x", "title": "t"}}
    assert ck.ranked_hits([memory, "junk", None, envelope()]) == [res()]


# ── metrics ──────────────────────────────────────────────────────────────────


def test_metrics_by_hand():
    ranks = [1, 3, 7, None]
    m = ck.metrics(ranks, [1, 1, 2, 4])
    assert m["n"] == 4
    assert m["recall_at_1"] == 0.25
    assert m["recall_at_5"] == 0.5
    assert m["recall_at_10"] == 0.75
    assert m["mrr_at_10"] == round((1 + 1 / 3 + 1 / 7) / 4, 4)
    assert m["file_recall_at_10"] == 1.0


def test_metrics_all_misses_is_zero_not_an_error():
    m = ck.metrics([None, None], [None, None])
    assert (m["recall_at_10"], m["mrr_at_10"], m["file_recall_at_10"]) == (0.0, 0.0, 0.0)


def test_breakdown_groups_by_style_and_language():
    rows = [
        {"style": "concept", "language": "rust", "rank": 1, "file_rank": 1},
        {"style": "keywords", "language": "rust", "rank": None, "file_rank": 2},
        {"style": "identifier", "language": "markdown", "rank": 2, "file_rank": 1},
    ]
    b = ck.breakdown(rows)
    assert b["all"]["recall_at_10"] == round(2 / 3, 4)
    assert b["by_style"]["keywords"]["recall_at_10"] == 0.0
    assert set(b["by_language"]) == {"rust", "markdown"}
    assert b["by_language"]["rust"]["n"] == 2


# ── index checks ─────────────────────────────────────────────────────────────


def test_check_index_rejects_a_discarded_index():
    with pytest.raises(SystemExit):
        ck.check_index({"index_rebuilt_from": 16, "total_chunks": 0}, "text")


def test_check_index_rejects_hybrid_over_an_unembedded_index():
    st = {"index_rebuilt_from": None, "total_chunks": 10, "embedding_pending": 10,
          "has_semantic_search": True, "server_url": "http://127.0.0.1:4655"}
    ck.check_index(st, "text")
    with pytest.raises(SystemExit):
        ck.check_index(st, "hybrid")


def test_check_index_rejects_hybrid_through_a_remote_server():
    st = {"index_rebuilt_from": None, "total_chunks": 10, "embedding_pending": 0,
          "has_semantic_search": True, "server_url": "https://api.inkentry.com"}
    with pytest.raises(SystemExit):
        ck.check_index(st, "hybrid")
    ck.check_index(st | {"server_url": "http://127.0.0.1:4655"}, "hybrid")


def test_harness_env_drops_inherited_inkentry_vars(monkeypatch, tmp_path):
    monkeypatch.setenv("INKENTRY_SERVER_URL", "https://example.invalid")
    monkeypatch.setenv("INKENTRY_MODE", "cloud_first")
    env = ck.harness_env("hybrid", tmp_path)
    assert "INKENTRY_SERVER_URL" not in env
    assert "INKENTRY_MODE" not in env
    assert env["INKENTRY_SECRET_STORE"] == "file"
    assert ck.harness_env("text", tmp_path)["INKENTRY_MODE"] == "offline"


def test_span_sha256_is_over_the_inclusive_line_range(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"one\ntwo\nthree\n")
    import hashlib
    assert ck.span_sha256(f, 2, 3) == hashlib.sha256(b"two\nthree").hexdigest()
    assert ck.span_sha256(f, 3, 9) is None
    assert ck.span_sha256(tmp_path / "missing", 1, 1) is None


# ── compare ──────────────────────────────────────────────────────────────────


def run(name="code-knownitem-inkentry", version=1, ranks=None, condition="text"):
    ranks = ranks if ranks is not None else {"0:concept": 1, "0:keywords": None, "1:concept": 4, "1:keywords": 11}
    return {
        "evalset": name, "evalset_version": version, "condition": condition,
        "per_query": [{"qid": q, "tid": int(q.split(":")[0]), "rank": r} for q, r in ranks.items()],
    }


def test_compare_refuses_a_different_set():
    with pytest.raises(ValueError, match="different eval sets"):
        cmp.compare(run(), run(name="code-knownitem-lago"))


def test_compare_refuses_a_different_version():
    with pytest.raises(ValueError, match="different eval sets"):
        cmp.compare(run(), run(version=2))


def test_compare_refuses_different_queries():
    with pytest.raises(ValueError, match="different queries"):
        cmp.compare(run(), run(ranks={"0:concept": 1}))


def test_compare_refuses_a_non_codeknown_file():
    with pytest.raises(ValueError):
        cmp.compare({"benchmark": "codesearchnet", "per_query": []}, run())


def test_compare_allows_different_conditions_over_one_set():
    c = cmp.compare(run(), run(condition="hybrid"))
    assert c["r10_diff"] == 0


def test_compare_counts_discordant_queries():
    a = run(ranks={"0:concept": 1, "0:keywords": None, "1:concept": 4, "1:keywords": 11})
    b = run(ranks={"0:concept": None, "0:keywords": 2, "1:concept": 4, "1:keywords": 3})
    c = cmp.compare(a, b)
    assert c["only_a"] == ["0:concept"]
    assert c["only_b"] == ["0:keywords", "1:keywords"]
    assert c["r10_a"] == 0.5 and c["r10_b"] == 0.75
    assert c["r10_diff"] == 0.25


def test_identical_runs_have_a_zero_width_ci():
    c = cmp.compare(run(), run())
    assert c["ci95"] == (0.0, 0.0)


def test_bootstrap_is_reproducible():
    a = run(ranks={f"{t}:concept": (1 if t % 2 else None) for t in range(40)})
    b = run(ranks={f"{t}:concept": (1 if t % 3 else None) for t in range(40)})
    assert cmp.compare(a, b)["ci95"] == cmp.compare(a, b)["ci95"]


# ── the committed eval sets ──────────────────────────────────────────────────

SETS = sorted(p.parent for p in (REPO / "evalsets").glob("code-knownitem-*/v*/manifest.json"))


def test_there_are_code_knownitem_sets():
    assert SETS


@pytest.mark.parametrize("d", SETS, ids=lambda d: f"{d.parent.name}/{d.name}")
def test_eval_set_is_consistent(d):
    manifest = json.loads((d / "manifest.json").read_text())
    targets = json.loads((d / "targets.json").read_text())
    queries = json.loads((d / "queries.json").read_text())

    assert (manifest["name"], f"v{manifest['version']}") == (d.parent.name, d.name)
    assert len(manifest["corpus"]["commit"]) == 40

    fields = {"tid", "path", "start_line", "end_line", "name", "language", "node_type", "span_sha256"}
    for t in targets:
        # No code leaves the source repository: no content, docstring, or
        # index-specific chunk id.
        assert set(t) == fields
        assert 1 <= t["start_line"] <= t["end_line"]
    tids = [t["tid"] for t in targets]
    assert len(set(tids)) == len(tids)

    assert all(set(q) == {"tid", "style", "query"} and q["query"].strip() == q["query"] != "" for q in queries)
    pairs = {(q["tid"], q["style"]) for q in queries}
    assert pairs == {(t, s) for t in tids for s in ck.STYLES}
    assert len(pairs) == len(queries)

    counts = manifest["counts"]
    assert counts["targets"] == len(targets)
    assert counts["queries"] == len(queries)
    assert sum(counts["targets_by_language"].values()) == len(targets)
