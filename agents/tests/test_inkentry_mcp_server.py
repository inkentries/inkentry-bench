"""Tests for agents/inkentry_mcp_server.py.

Run:
    uv run --with pytest pytest agents/tests/ -v

Network-free, no inkentry binary, no MCP handshake: these target the tool
layer the server builds before any client connects (schemas, condition
gating, spawn argv, telemetry).
"""

import json
import sys

import agent
import pytest
from inkentry_mcp_server import (
    SERVER_NAME,
    INKENTRY_CONDITIONS,
    mcp_server_command,
    mcp_tool_names_for_condition,
    read_telemetry,
    inkentry_tools_for_condition,
    tool_names_for_condition,
)

AGENT_BASE_TOOL_NAMES = {t["function"]["name"] for t in agent.BASE_TOOLS}


class TestEquivalenceWithAgentPy:
    """The property the whole harness matrix rests on: a `inkentry` condition
    must mean the same capability whichever harness carries it.

    Importing agent.py's objects makes that hold by construction, so these
    assert *identity*, not equality. An inlined copy of a schema passes `==`
    while being free to drift; only `is` catches it. Anchored on
    agent.build_tools(), never a list restated here.
    """

    @pytest.mark.parametrize("condition", INKENTRY_CONDITIONS)
    def test_exposed_tools_are_agent_py_objects_not_copies(self, condition):
        agent_tools = agent.build_tools(condition)
        exposed = inkentry_tools_for_condition(condition)
        assert exposed, "a inkentry condition must expose at least one tool"
        for tool in exposed:
            assert any(tool is t for t in agent_tools), (
                f"{tool['function']['name']} is not agent.py's own object. "
                "A copied schema drifts silently and makes the cross-harness "
                "comparison dishonest."
            )

    @pytest.mark.parametrize("condition", INKENTRY_CONDITIONS)
    def test_exposes_exactly_the_inkentry_tools_agent_py_would(self, condition):
        expected = {
            t["function"]["name"] for t in agent.build_tools(condition)
        } - AGENT_BASE_TOOL_NAMES
        assert set(tool_names_for_condition(condition)) == expected

    @pytest.mark.parametrize("condition", INKENTRY_CONDITIONS)
    def test_never_re_exposes_tools_the_harness_ships_natively(self, condition):
        # Each harness has its own read_file/run_bash/write_file. Re-exposing
        # them over MCP would hand the model two of each.
        assert not AGENT_BASE_TOOL_NAMES & set(tool_names_for_condition(condition))

    def test_inkentry_full_is_a_strict_superset_of_inkentry_search(self):
        assert set(tool_names_for_condition("inkentry_search")) < set(
            tool_names_for_condition("inkentry_full")
        )


class TestConditionGate:
    def test_baseline_is_refused(self):
        # On baseline this server must never be spawned at all; if it is,
        # refusing beats silently exposing an empty toolset that reads as a
        # real inkentry cell.
        with pytest.raises(ValueError, match="condition must be one of"):
            inkentry_tools_for_condition("baseline")

    def test_unknown_condition_is_refused(self):
        with pytest.raises(ValueError, match="condition must be one of"):
            inkentry_tools_for_condition("inkentry_xxx")

    @pytest.mark.parametrize("condition", INKENTRY_CONDITIONS)
    def test_positive_control_inkentry_conditions_are_accepted(self, condition):
        # Proves the two raises above are the gate firing, not the call
        # raising for every input.
        assert inkentry_tools_for_condition(condition)


class TestMcpToolNames:
    @pytest.mark.parametrize("condition", INKENTRY_CONDITIONS)
    def test_names_are_namespaced_but_track_agent_pys_bare_names(self, condition):
        # The one accepted difference from harness=none: transport renames,
        # capability does not.
        assert mcp_tool_names_for_condition(condition) == [
            f"mcp__{SERVER_NAME}__{n}" for n in tool_names_for_condition(condition)
        ]


class TestMcpServerCommand:
    def test_spawns_this_interpreter_not_a_bare_python3(self, tmp_path):
        # The harness runs under `uv run --with-requirements`; only that
        # interpreter has the mcp SDK. A bare "python3" resolves to the
        # host's system interpreter and fails to import.
        cmd = mcp_server_command("inkentry_search", tmp_path)
        assert cmd[0] == sys.executable

    def test_carries_repo_path_and_condition(self, tmp_path):
        cmd = mcp_server_command("inkentry_full", tmp_path)
        assert cmd[cmd.index("--repo-path") + 1] == str(tmp_path.resolve())
        assert cmd[cmd.index("--condition") + 1] == "inkentry_full"

    def test_telemetry_log_is_opt_in(self, tmp_path):
        log = tmp_path / "calls.jsonl"
        without = mcp_server_command("inkentry_search", tmp_path)
        with_log = mcp_server_command("inkentry_search", tmp_path, telemetry_log=log)
        assert "--telemetry-log" not in without
        assert with_log[with_log.index("--telemetry-log") + 1] == str(log)


class TestReadTelemetry:
    """Separates the three outcomes a inkentry-labelled cell otherwise can't
    be told apart by: never spawned, spawned but unused, used."""

    def test_missing_log_reports_never_spawned(self, tmp_path):
        summary = read_telemetry(tmp_path / "absent.jsonl")
        assert summary["inkentry_mcp_server_spawned"] is False
        assert summary["inkentry_tool_calls"] == 0
        assert summary["inkentry_tool_calls_by_tool"] is None

    def test_no_log_requested_reports_never_spawned(self):
        assert read_telemetry(None)["inkentry_mcp_server_spawned"] is False

    def test_spawned_but_unused_is_distinct_from_never_spawned(self, tmp_path):
        log = tmp_path / "calls.jsonl"
        log.write_text(json.dumps({"event": "server_start", "condition": "inkentry_search"}) + "\n")
        summary = read_telemetry(log)
        assert summary["inkentry_mcp_server_spawned"] is True
        assert summary["inkentry_tool_calls"] == 0

    def test_counts_calls_per_tool(self, tmp_path):
        log = tmp_path / "calls.jsonl"
        log.write_text(
            "\n".join(
                json.dumps(r)
                for r in (
                    {"event": "server_start", "condition": "inkentry_full"},
                    {"event": "tool_call", "tool": "inkentry_search"},
                    {"event": "tool_call", "tool": "inkentry_search"},
                    {"event": "tool_call", "tool": "inkentry_graph"},
                )
            )
            + "\n"
        )
        summary = read_telemetry(log)
        assert summary["inkentry_mcp_server_spawned"] is True
        assert summary["inkentry_tool_calls"] == 3
        assert summary["inkentry_tool_calls_by_tool"] == {
            "inkentry_search": 2,
            "inkentry_graph": 1,
        }

    def test_survives_a_truncated_trailing_line(self, tmp_path):
        # The server appends and flushes per call; a killed run can leave a
        # half-written line. Losing it must not lose the whole record.
        log = tmp_path / "calls.jsonl"
        log.write_text(
            json.dumps({"event": "server_start", "condition": "inkentry_search"}) + "\n"
            + json.dumps({"event": "tool_call", "tool": "inkentry_search"}) + "\n"
            + '{"event": "tool_ca'
        )
        summary = read_telemetry(log)
        assert summary["inkentry_mcp_server_spawned"] is True
        assert summary["inkentry_tool_calls"] == 1
