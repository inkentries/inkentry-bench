#!/usr/bin/env python3
"""Unified SWE-bench agent — any OpenAI-compatible API, with or without inkentry.

Replaces swebench/agent_*.py (Anthropic, deleted) and
gemma/swebench_local/agent_*.py (local Gemma, kept as templates).

Three conditions, specified via --condition:

    baseline         read_file, run_bash, write_file
    inkentry_search   baseline + inkentry_search (semantic code retrieval)
    inkentry_full     baseline + inkentry_search + inkentry_graph + inkentry_memory_search

Usage:
    python agents/agent.py \\
        --condition inkentry_full \\
        --task-id django__django-11099 \\
        --repo-path /path/to/repo \\
        --issue "Issue description..." \\
        --model deepseek-v4-flash \\
        --api-base-url https://api.deepseek.com/v1 \\
        --api-key $DEEPSEEK_API_KEY \\
        [--max-turns 20] [--seed 42]

Output: single JSON object on stdout (reproducibility contract fields).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai import OpenAI

# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling format)
# ---------------------------------------------------------------------------

BASE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file within the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path relative to the repo root.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": (
                "Run a shell command in the repository directory. "
                "Output is truncated to 10 000 characters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute.",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file within the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path relative to the repo root.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full content to write.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
]

INKENTRY_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "inkentry_search",
        "description": (
            "Semantically search the codebase using inkentry. Returns the most "
            "relevant code chunks for the given query. Use this to quickly locate "
            "relevant functions, classes, or logic without manually browsing files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 10).",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
}

INKENTRY_GRAPH_TOOL = {
    "type": "function",
    "function": {
        "name": "inkentry_graph",
        "description": (
            "Query the inkentry code graph for a given symbol (function, struct, "
            "class, etc.). Returns callers, callees, and import relationships. "
            "Use this to trace how a symbol is used across the codebase."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": (
                        "Symbol name to query, e.g. a function or class name."
                    ),
                }
            },
            "required": ["symbol"],
        },
    },
}

INKENTRY_MEMORY_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "inkentry_memory_search",
        "description": (
            "Search inkentry project memory for decisions, notes, handoffs, and "
            "other contextual information. Use this to find prior design decisions, "
            "architectural context, or notes left by previous sessions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query against project memory.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 10).",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_OUTPUT_CHARS = 10_000

SYSTEM_PROMPT_BASE = (
    "You are an expert software engineer. You are given a GitHub issue and a "
    "repository checkout. Your goal is to produce a minimal patch that fixes the "
    "issue. Use the available tools to explore the codebase, understand the problem, "
    "and apply the fix. When you are done, briefly summarise what you changed."
)

SYSTEM_PROMPT_INKENTRY = (
    "You are an expert software engineer. You are given a GitHub issue and a "
    "repository checkout. Your goal is to produce a minimal patch that fixes the "
    "issue. You have access to inkentry tools for fast semantic code search, "
    "code graph traversal, and project memory retrieval — use them to locate "
    "relevant code and context before diving into files. When you are done, "
    "briefly summarise what you changed."
)

# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------


def read_file(repo_path: Path, path: str) -> str:
    target = (repo_path / path).resolve()
    repo_root = repo_path.resolve()
    if not str(target).startswith(str(repo_root)):
        return "Error: path is outside the repository."
    try:
        return target.read_text(errors="replace")
    except Exception as e:
        return f"Error reading file: {e}"


def run_bash(repo_path: Path, command: str, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        output = f"Error: command timed out after {timeout} seconds."
    except Exception as e:
        output = f"Error running command: {e}"
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n... (output truncated)"
    return output


def write_file(repo_path: Path, path: str, content: str) -> str:
    target = (repo_path / path).resolve()
    repo_root = repo_path.resolve()
    if not str(target).startswith(str(repo_root)):
        return "Error: path is outside the repository."
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"Wrote {len(content)} bytes to {path}."
    except Exception as e:
        return f"Error writing file: {e}"


def _run_inkentry(
    repo_path: Path,
    args: list[str],
    timeout: int = 30,
    exit_1_is_empty: bool = False,
) -> str:
    """Run a inkentry command in repo_path, return stdout or error message.

    `exit_1_is_empty` picks the exit-code convention, which differs between the
    two command families this helper serves.

    `plumbing` commands signal an empty result set with exit 1 and reserve
    exit 2 for errors, so exit 1 there is a legitimate "nothing matched".
    Porcelain commands — `search` — do not: no matches is exit 0 with `[]`, and
    exit 1 means the query never ran (no project here, unreadable index).
    Reporting that to the model as "(no results)" is indistinguishable from
    inkentry genuinely having nothing to say, which is how a container whose
    index never built scores a whole matrix cell at baseline with nothing
    erroring and nothing in the telemetry to show why.
    """
    cmd = ["inkentry"] + args
    try:
        result = subprocess.run(
            cmd, cwd=repo_path, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode == 0:
            output = result.stdout
        elif result.returncode == 1 and exit_1_is_empty:
            output = result.stdout or "(no results)"
        else:
            return (
                f"inkentry {' '.join(args)} failed (exit {result.returncode}): "
                f"{result.stderr.strip()}"
            )
    except FileNotFoundError:
        return "Error: inkentry not found in PATH."
    except subprocess.TimeoutExpired:
        return "Error: inkentry command timed out."
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n... (output truncated)"
    return output or "(no results)"


def inkentry_search(repo_path: Path, query: str, limit: int = 10) -> str:
    # --only-code keeps the conditions separable: memory retrieval is what the
    # inkentry_memory_search tool measures, and letting it leak into this tool
    # would make the inkentry_search condition a partial inkentry_full.
    return _run_inkentry(
        repo_path,
        ["search", query, "--only-code", "--limit", str(limit), "--format", "json"],
    )


def inkentry_graph(repo_path: Path, symbol: str) -> str:
    # The code graph is reached through search rather than a top-level graph
    # command. This tool wants the edges themselves, not ranked chunks, so it
    # uses the plumbing command; the JSONL fields match what a graph query
    # returned before.
    return _run_inkentry(
        repo_path,
        ["plumbing", "graph-edges", "--symbol", symbol],
        exit_1_is_empty=True,
    )


def inkentry_memory_search(repo_path: Path, query: str, limit: int = 10) -> str:
    return _run_inkentry(
        repo_path,
        ["search", query, "--only-memory", "--limit", str(limit), "--format", "json"],
    )


def build_dispatch_table(repo_path: Path) -> dict:
    """Return {tool_name: callable(repo_path, arguments_json) -> str}."""
    return {
        "read_file": lambda args: read_file(repo_path, args["path"]),
        "run_bash": lambda args: run_bash(repo_path, args["command"]),
        "write_file": lambda args: write_file(repo_path, args["path"], args["content"]),
        "inkentry_search": lambda args: inkentry_search(
            repo_path, args["query"], args.get("limit", 10)
        ),
        "inkentry_graph": lambda args: inkentry_graph(repo_path, args["symbol"]),
        "inkentry_memory_search": lambda args: inkentry_memory_search(
            repo_path, args["query"], args.get("limit", 10)
        ),
    }


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


def build_tools(condition: str) -> list[dict]:
    """Build the tool list for the given condition."""
    base = list(BASE_TOOLS)
    if condition == "baseline":
        return base
    elif condition == "inkentry_search":
        return base + [INKENTRY_SEARCH_TOOL]
    elif condition == "inkentry_full":
        return base + [
            INKENTRY_SEARCH_TOOL,
            INKENTRY_GRAPH_TOOL,
            INKENTRY_MEMORY_SEARCH_TOOL,
        ]
    else:
        raise ValueError(f"Unknown condition: {condition}")


def get_system_prompt(condition: str) -> str:
    """Return the appropriate system prompt for the condition."""
    if condition == "baseline":
        return SYSTEM_PROMPT_BASE
    return SYSTEM_PROMPT_INKENTRY


def get_inkentry_version() -> str:
    """Return inkentry version string, or 'unknown' if not available."""
    try:
        result = subprocess.run(
            ["inkentry", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def run_agent(
    task_id: str,
    repo_path: Path,
    issue_text: str,
    client: OpenAI,
    model: str,
    condition: str,
    max_turns: int,
    seed: int,
) -> dict:
    tools = build_tools(condition)
    system_prompt = get_system_prompt(condition)
    dispatch = build_dispatch_table(repo_path)

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"Repository path: {repo_path}\n\nIssue:\n{issue_text}\n\n"
                "Please investigate the issue and apply a fix."
            ),
        },
    ]

    turns = 0
    input_tokens = 0
    output_tokens = 0
    start = time.monotonic()

    while turns < max_turns:
        response = client.chat.completions.create(
            model=model,
            max_tokens=4096,
            tools=tools,
            tool_choice="auto",
            messages=messages,
            seed=seed,
        )
        msg = response.choices[0].message
        input_tokens += response.usage.prompt_tokens if response.usage else 0
        output_tokens += response.usage.completion_tokens if response.usage else 0
        turns += 1

        assistant_entry: dict = {"role": "assistant", "content": msg.content or ""}
        # DeepSeek thinking mode: preserve reasoning_content if present
        if hasattr(msg, "reasoning_content") and msg.reasoning_content:
            assistant_entry["reasoning_content"] = msg.reasoning_content
        if msg.tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant_entry)

        if response.choices[0].finish_reason != "tool_calls" or not msg.tool_calls:
            break

        for tc in msg.tool_calls:
            name = tc.function.name
            handler = dispatch.get(name)
            if handler is None:
                result = f"Unknown tool: {name}"
            else:
                try:
                    args = json.loads(tc.function.arguments)
                    result = handler(args)
                except Exception as e:
                    result = f"Error dispatching {name}: {e}"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    return {
        "task_id": task_id,
        "resolved": False,  # determined externally by SWE-bench harness
        "turns": turns,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "wall_seconds": round(time.monotonic() - start, 2),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unified SWE-bench agent (OpenAI-compatible API)."
    )
    parser.add_argument(
        "--condition",
        required=True,
        choices=["baseline", "inkentry_search", "inkentry_full"],
    )
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--issue", required=True)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--api-base-url", default="https://api.deepseek.com/v1")
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key (falls back to DEEPSEEK_API_KEY env var)",
    )
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--save-patch",
        default=None,
        help="Save git diff to this file after agent finishes (for SWE-bench eval).",
    )
    args = parser.parse_args()

    # Lazy on purpose: importing this module must not require the LLM stack, so
    # the tool schemas stay reachable offline (inkentry_mcp_server.py, tests).
    from dotenv import load_dotenv
    from openai import OpenAI

    # Auto-load .env.local from project root if present
    dotenv_path = Path(__file__).resolve().parents[2] / ".env.local"
    if dotenv_path.exists():
        load_dotenv(dotenv_path)

    repo_path = Path(args.repo_path).resolve()
    if not repo_path.is_dir():
        parser.error(f"repo-path does not exist: {repo_path}")

    # Resolve API key
    api_key = args.api_key or os.environ.get("DEEPSEEK_API_KEY")
    provenance_label = "flag:--api-key" if args.api_key else "env:DEEPSEEK_API_KEY"
    if not api_key:
        parser.error(
            "No API key provided. Use --api-key or set DEEPSEEK_API_KEY env var."
        )

    # Issue text can come from a file path or inline
    issue_text = args.issue
    issue_path = Path(issue_text)
    if issue_path.is_file():
        issue_text = issue_path.read_text()

    client = OpenAI(base_url=args.api_base_url, api_key=api_key)

    # Run the agent
    agent_result = run_agent(
        task_id=args.task_id,
        repo_path=repo_path,
        issue_text=issue_text,
        client=client,
        model=args.model,
        condition=args.condition,
        max_turns=args.max_turns,
        seed=args.seed,
    )

    # Save git diff for SWE-bench evaluation
    patch_path = None
    if args.save_patch:
        try:
            # Stage only known source file extensions so that tooling/runtime
            # artifacts never end up in the saved patch. Without this, junk
            # like `.inkentry/index.db` (binary, written by `inkentry index`),
            # `ISSUE.txt` (written by setup_repos.sh), `uv.lock` (created by
            # `uv run`), and similar files end up in the saved patch and
            # either fail to apply in the SWE-bench Docker container or
            # pollute the diff, causing otherwise-correct fixes to be marked
            # unresolved. A denylist approach (:!.inkentry :!ISSUE.txt
            # :!uv.lock ...) is fragile: shell-mangled filenames such as
            # "=2.6.0," (from a botched `pip install` output redirected into
            # a file) slip through. An allowlist is strictly safer; anything
            # that isn't a recognised source file is ignored.
            SOURCE_PATHSPECS = [
                "*.py",
                "*.rs",
                "*.ts",
                "*.tsx",
                "*.js",
                "*.jsx",
                "*.go",
                "*.java",
                "*.c",
                "*.cpp",
                "*.h",
                "*.rb",
                "*.sh",
                "*.toml",
                "*.json",
                "*.yaml",
                "*.yml",
                "*.md",
            ]
            # Never hand the allowlist itself to `git add`: git treats any
            # pathspec with zero matches across the whole repo as fatal
            # (exit 128, "pathspec '*.rs' did not match any files") and then
            # stages nothing at all — not even the pathspecs that did match.
            # Since a task repo only ever contains files for a few of the 18
            # extensions above, that made patch capture fail on essentially
            # every run, silently saving `patch_file: null` even when a
            # correct fix sat in the working tree. Instead, resolve the
            # allowlist to concrete changed files first — `git diff
            # --name-only` (tracked, modified) and `git ls-files --others`
            # (untracked, new) both tolerate unmatched pathspecs cleanly —
            # and stage only those. Keep in lockstep with
            # harness_common.extract_patch, which uses the same approach.
            def run_git(git_args: list) -> str:
                return subprocess.run(
                    git_args,
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=True,
                ).stdout

            modified = run_git(
                ["git", "diff", "--name-only", "--", *SOURCE_PATHSPECS]
            ).splitlines()
            untracked = run_git(
                ["git", "ls-files", "--others", "--exclude-standard", "--", *SOURCE_PATHSPECS]
            ).splitlines()
            changed_files = [f for f in (*modified, *untracked) if f.strip()]
            if changed_files:
                run_git(["git", "add", "--", *changed_files])
            diff = run_git(
                ["git", "diff", "--cached", "HEAD", "--", *SOURCE_PATHSPECS]
            )
            patch_path = Path(args.save_patch)
            patch_path.parent.mkdir(parents=True, exist_ok=True)
            patch_path.write_text(diff)
        except Exception as e:
            print(f"Warning: failed to save patch: {e}", file=sys.stderr)

    # Reproducibility contract.
    #
    # harness/harness_version/endpoint_kind/run_seed and the
    # question_set_version/instance_filter/judge_* fields are part of the
    # harness-matrix provenance extension (agents/README.md
    # "Provenance contract") — additive-only, so pre-existing consumers
    # (export_patches.py, report.py) that only read specific keys via
    # dict.get() are unaffected. This is the harness=none, "component-clean"
    # cell: no external agent harness, just this script's own tool-calling
    # loop, run directly against the model's own (non-Anthropic-compat) API.
    output = {
        "benchmark": "swebench-verified",
        "condition": args.condition,
        "harness": "none",
        "harness_version": None,
        "endpoint_kind": "native",
        # harness=none has no effort/thinking concept of its own (that's a
        # claude-code-harness-only knob) -- always null here so every
        # harness's result JSON is a strict key-superset of the documented
        # provenance contract (agents/README.md "Reproducibility /
        # provenance contract"), never a per-harness subset.
        "effort": None,
        "thinking": None,
        "model": args.model,
        "model_source": "api",
        "api_base_url": args.api_base_url,
        "api_key_source": provenance_label,
        "inkentry_version": get_inkentry_version(),
        "seed": args.seed,
        "run_seed": args.seed,
        "max_turns": args.max_turns,
        "patch_file": str(patch_path) if patch_path else None,
        # Populated later, once the corresponding infra lands (README §Provenance):
        "question_set_version": None,
        "instance_filter": None,
        "judge_model": None,
        "judge_version": None,
        "judge_error_rate": None,
        **agent_result,
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
