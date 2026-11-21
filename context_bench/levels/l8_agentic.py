"""L8 — agentic. Target file as initial context, plus tools the model calls to explore the repo.

Variants:
  agent_variant = "grep"    tools: grep, list_files, read_file, find_references
  agent_variant = "hybrid"  adds semantic_search (vector index next to grep)

Active vs legacy code: the agent must be able to tell live code from dead code so it doesn't spend its
budget (or its patch) on it. It gets the *means*, never the instruction: find_references shows where a
name is used, and list_files shows how many modules import each file. Nothing in the prompt or
TOOLS_NOTE says that dead code exists or should be avoided (that would be a hint, like reuse_hint).

Budget: at most 15 tool calls; then the model must return its <file> blocks.
Malformed tool calls are counted; if the run ends with tool errors and no file blocks, the label
is "tool_error".
helper_in_context for L8 = the helper's definition appeared in any tool output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..types import ContextBlock, Task
from ..workspace import Workspace

MAX_TOOL_CALLS = 15

# Mechanics only (what the model has), never strategy (how to search). Goes inside <context>, after
# the target file, so the system prompt stays identical across levels.
TOOLS_NOTE = """You can explore the repository with these tools: {tools}.
You may make at most {max_calls} tool calls. When you are done, reply with the <file> blocks as
described; that reply ends the task."""


def build_context(task: Task, ws: Workspace, agent_variant: str = "grep") -> ContextBlock:
    """Initial context only: the target file, then TOOLS_NOTE listing the variant's tools.
    The rest arrives through tools."""
    raise NotImplementedError


def tool_schemas(agent_variant: str) -> list[dict]:
    """OpenAI-style function tool definitions for the variant."""
    raise NotImplementedError


@dataclass
class RepoTools:
    """Read-only tools over the sandbox copy of the repo."""
    root: Path
    transcript: list[str] = field(default_factory=list)   # every tool output

    def grep(self, pattern: str, path_glob: str = "**/*.py") -> str:
        raise NotImplementedError

    def list_files(self, directory: str = ".") -> str:
        """One line per entry under `directory`: subdirectories, and .py files as
        "path  (N lines, imported by M modules)". M counts distinct modules in the repo that import it
        (absolute or relative imports, via ast), so unused files show "imported by 0"."""
        raise NotImplementedError

    def read_file(self, path: str) -> str:
        raise NotImplementedError

    def find_references(self, symbol: str) -> str:
        """Every place a name is defined, imported or used, as "path:line: <source line>", via ast
        (Name/Attribute/import nodes, not text search). `symbol` is a bare name ("format_money") or a
        dotted path ("shopkit.money.format_money"). Grouped as definitions, imports, uses; capped at
        50 lines with a "(+N more)" note."""
        raise NotImplementedError

    def semantic_search(self, query: str, k: int = 5) -> str:
        raise NotImplementedError


@dataclass
class AgentResult:
    content: str
    tool_calls: int
    tool_errors: int
    tokens_in: int
    tokens_out: int
    peak_ctx_tokens: int      # largest prompt over all turns (context grows with tool output)
    no_fit: bool              # a turn would have exceeded the context window
    latency_s: float


def run_agent(client, messages: list[dict], tools: RepoTools, *, agent_variant: str,
              temperature: float | None, seed: int, max_tokens: int,
              context_window: int) -> AgentResult:
    """Tool loop: call the model, execute tool calls, feed results back, stop on a final answer
    or when the budget is spent. Before every turn, check prompt tokens + max_tokens against the
    context window; if it no longer fits, stop and report no_fit (never let the server truncate)."""
    raise NotImplementedError
