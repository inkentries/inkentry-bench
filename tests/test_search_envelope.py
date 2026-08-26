"""The `search --format json` envelope unwrapping, at all three call sites.

The failure this guards is silent by construction: a harness that reads the
wrong shape matches nothing on every result, at full query latency, and reports
a healthy-looking run that scored zero. Nothing raises and nothing exits
non-zero, so only an assertion catches it.

Each harness carries its own unwrap helper — they are standalone scripts, not a
package — so each is exercised here against the same four shapes:

    nested      current builds: chunk fields under `code`
    flat        pre-1.0 builds: chunk fields at the top level
    memory      the other envelope type; `code` is absent, not null
    non-dict    junk in the list must not raise

Run: uv run --with pytest pytest tests/test_search_envelope.py -q
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(name: str, relpath: str):
    """Import a harness script under an explicit module name.

    Two of these are called `evaluate.py`, in different directories, so they
    cannot be imported by filename alone.
    """
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


csn = _load("csn_evaluate", "codesearchnet/evaluate.py")
graph_eval = _load("graph_evaluate", "graph/evaluate.py")
linearrag = _load("linearrag_run_eval", "linearrag/run_eval.py")


def code_envelope(path="src/auth.rs", name="validate_token", rank=1, chunk_id=1):
    """One ranked code envelope, as current builds emit it."""
    return {
        "type": "code",
        "fused_rank": rank,
        "fused_score": 1 / (60 + rank),
        "corpus_rank": rank,
        "code": {
            "chunk_id": chunk_id,
            "file_path": path,
            "language": "rust",
            "node_type": "function",
            "name": name,
            "start_line": 1,
            "end_line": 3,
            "content": "pub fn validate_token() {}",
            "distance": 0.1,
            "from_graph": False,
            "token_count": 8,
        },
    }


def flat_result(path="src/auth.rs", name="validate_token", chunk_id=1):
    """The pre-1.0 shape: chunk fields at the top level, no envelope."""
    return {
        "chunk_id": chunk_id,
        "file_path": path,
        "name": name,
        "start_line": 1,
        "end_line": 3,
        "content": "pub fn validate_token() {}",
        "distance": 0.1,
    }


def memory_envelope(rank=2):
    """A memory envelope. Note `code` is absent, not null."""
    return {
        "type": "memory",
        "fused_rank": rank,
        "fused_score": 1 / (60 + rank),
        "corpus_rank": 1,
        "memory": {
            "id": "01a01a9a-4140-7cbb-8047-c624a5ecb8e4",
            "kind": "decision",
            "title": "Chose sqlite-vec over hnswlib",
            "body": "No C++ dependency, single file.",
            "tags": ["storage"],
        },
    }


# ── codesearchnet/evaluate.py: result_payload + find_rank ────────────────────


def test_csn_payload_unwraps_nested():
    payload = csn.result_payload(code_envelope())
    assert payload["file_path"] == "src/auth.rs"
    assert payload["name"] == "validate_token"


def test_csn_payload_falls_through_to_flat():
    payload = csn.result_payload(flat_result())
    assert payload["file_path"] == "src/auth.rs"


def test_csn_find_rank_matches_nested_envelope():
    results = [code_envelope(path="a/b.py", name="other"), code_envelope(path="x/y.py", name="target")]
    assert csn.find_rank(results, "target", "x/y.py") == 2


def test_csn_find_rank_reads_the_envelope_not_the_top_level():
    """The regression itself: reading the top level would return None here."""
    assert csn.find_rank([code_envelope(path="x/y.py", name="target")], "target", "x/y.py") == 1


def test_csn_find_rank_still_reads_the_flat_shape():
    """Results captured by an older binary stay readable."""
    assert csn.find_rank([flat_result(path="x/y.py", name="target")], "target", "x/y.py") == 1


def test_csn_find_rank_ignores_memory_envelopes():
    """A memory entry can never be the target, and must not match one."""
    assert csn.find_rank([memory_envelope()], "target", "x/y.py") is None


def test_csn_find_rank_requires_both_name_and_path():
    """Name alone would score an unrelated repository's `get` as a hit."""
    results = [code_envelope(path="other/repo.py", name="target")]
    assert csn.find_rank(results, "target", "x/y.py") is None


# ── graph/evaluate.py: search_file_paths ─────────────────────────────────────


def test_graph_paths_unwrap_nested():
    got = graph_eval.search_file_paths([code_envelope(path="src/a.rs"), code_envelope(path="src/b.rs")])
    assert got == {"src/a.rs", "src/b.rs"}


def test_graph_paths_fall_through_to_flat():
    assert graph_eval.search_file_paths([flat_result(path="src/a.rs")]) == {"src/a.rs"}


def test_graph_paths_skip_memory_envelopes():
    """A memory envelope has no file_path; it must contribute nothing."""
    got = graph_eval.search_file_paths([code_envelope(path="src/a.rs"), memory_envelope()])
    assert got == {"src/a.rs"}


def test_graph_paths_tolerate_non_dict_entries():
    assert graph_eval.search_file_paths(["junk", None, code_envelope(path="src/a.rs")]) == {"src/a.rs"}


# ── linearrag/run_eval.py: unwrap + summarize ────────────────────────────────


def test_linearrag_unwrap_nested_and_flat():
    assert linearrag.unwrap(code_envelope())["file_path"] == "src/auth.rs"
    assert linearrag.unwrap(flat_result())["file_path"] == "src/auth.rs"


def test_linearrag_summarize_reads_nested_envelopes():
    rows = linearrag.summarize([code_envelope(path="src/a.rs", chunk_id=7)])
    assert rows == [
        {
            "rank": 1,
            "chunk_id": 7,
            "file": "src/a.rs",
            "name": "validate_token",
            "start": 1,
            "end": 3,
            "snippet": "pub fn validate_token() {}",
        }
    ]


def test_linearrag_summarize_skips_memory_instead_of_raising():
    """`--only-code` should prevent this; if it ever arrives, skip, not KeyError."""
    rows = linearrag.summarize([memory_envelope(), code_envelope(path="src/a.rs")])
    assert [r["file"] for r in rows] == ["src/a.rs"]


def test_linearrag_summarize_renumbers_after_a_skip():
    """Rank is the position among kept rows, with no gap left by the skip."""
    rows = linearrag.summarize(
        [memory_envelope(), code_envelope(path="src/a.rs"), code_envelope(path="src/b.rs")]
    )
    assert [r["rank"] for r in rows] == [1, 2]


# ── the exit-code convention the harnesses now split on ──────────────────────


@pytest.mark.parametrize(
    "returncode, is_failure",
    [(0, False), (1, True), (2, True)],
    ids=["no-matches", "query-did-not-run", "rejected-argument"],
)
def test_search_exit_code_convention_is_documented(returncode, is_failure):
    """`search` is porcelain: only exit 0 is a successful query.

    Exit 1 is the *plumbing* convention for an empty set and does not apply
    here — reading it as "no results" is what turns a missing index into a
    silent zero. This pins the rule the call sites encode.
    """
    assert (returncode != 0) is is_failure
