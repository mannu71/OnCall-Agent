"""One-off splitter: react.py -> react/ package. Run from agent-api/."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app" / "workflow" / "strategies"
SRC = ROOT / "react.py"
PKG = ROOT / "react"
text = SRC.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)


def chunk(start: int, end: int) -> str:
    return "".join(lines[start - 1 : end])


def dedent_method_block(block: str, *, drop_self: bool = True) -> str:
    """Turn indented method body into module-level function body."""
    out: list[str] = []
    for line in block.splitlines(keepends=True):
        if drop_self and re.match(r"^\s+@staticmethod\s*$", line):
            continue
        if drop_self and re.match(r"^\s+async def _", line):
            line = re.sub(r"^    async def (_\w+)\(", r"async def \1(", line)
        elif drop_self and re.match(r"^\s+def _", line):
            line = re.sub(r"^    def (_\w+)\(", r"def \1(", line)
        elif drop_self and re.match(r"^\s+async def ", line):
            line = re.sub(r"^    async def (\w+)\(", r"async def \1(", line)
        elif drop_self and re.match(r"^\s+def ", line):
            line = re.sub(r"^    def (\w+)\(", r"def \1(", line)
        if line.startswith("    "):
            line = line[4:]
        out.append(line)
    return "".join(out)


PKG.mkdir(exist_ok=True)

# --- helpers.py ---
(PKG / "helpers.py").write_text(
    '"""ReAct helper utilities."""\nfrom __future__ import annotations\n\n'
    + chunk(22, 36)
    + chunk(38, 220),
    encoding="utf-8",
)

# --- streaming.py ---
(PKG / "streaming.py").write_text(
    '"""ReAct streaming callback protocol."""\nfrom __future__ import annotations\n\n'
    + chunk(223, 228),
    encoding="utf-8",
)

# --- workflow_config.py ---
wf_body = dedent_method_block(chunk(909, 1158))
(PKG / "workflow_config.py").write_text(
    '''"""Workflow graph config extraction for ReAct agents."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.workflow.llm_config import LLM_NODE_TYPES, resolve_llm_config
from app.workflow.executor.handlers.cloudwatch import _read_cw_config

logger = logging.getLogger(__name__)

'''
    + wf_body.replace("self._get_connected_node_ids", "get_connected_node_ids")
    .replace("def _extract_", "def extract_")
    .replace("def _get_connected_node_ids", "def get_connected_node_ids")
    .replace("async def _resolve_llm_config", "async def resolve_llm_config_for_workflow"),
    encoding="utf-8",
)

# --- tool_setup.py ---
tool_body = dedent_method_block(chunk(1164, 1228)) + dedent_method_block(chunk(1406, 1507))
(PKG / "tool_setup.py").write_text(
    '''"""MCP tool wiring and playbook tools for ReAct agents."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.infrastructure.persistence import mcp_config_repository
from app.workflow.mcp.mcp_langchain_adapter import build_langchain_tools

logger = logging.getLogger(__name__)

'''
    + tool_body.replace("async def _setup_tools", "async def setup_tools")
    .replace("def _build_playbook_tools", "def build_playbook_tools"),
    encoding="utf-8",
)

# --- llm_factory.py ---
(PKG / "llm_factory.py").write_text(
    '''"""LangChain LLM factory for ReAct agents."""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

'''
    + dedent_method_block(chunk(1234, 1400)).replace("def _build_llm", "def build_llm"),
    encoding="utf-8",
)

# --- hitl.py ---
(PKG / "hitl.py").write_text(
    '''"""HITL and checkpoint helpers for ReAct agents."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

'''
    + dedent_method_block(chunk(1513, 1566))
    .replace("async def _make_checkpointer", "async def make_checkpointer")
    .replace("async def _emit_hitl_pause", "async def emit_hitl_pause"),
    encoding="utf-8",
)

# --- agent_builder.py ---
(PKG / "agent_builder.py").write_text(
    '''"""LangGraph ReAct agent construction."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.workflow.strategies.react.tool_setup import build_playbook_tools

logger = logging.getLogger(__name__)

'''
    + dedent_method_block(chunk(1572, 1853))
    .replace("def _build_agent", "def build_agent")
    .replace("self._build_playbook_tools()", "build_playbook_tools()"),
    encoding="utf-8",
)

# --- agent_runner.py ---
runner = dedent_method_block(chunk(1859, 2322))
runner = (
    runner.replace("async def _execute_agent", "async def execute_agent")
    .replace("def _extract_text_content", "def extract_text_content")
    .replace("async def _invoke_agent", "async def invoke_agent")
    .replace("async def _execute_agent_stream", "async def execute_agent_stream")
    .replace("self._extract_text_content", "extract_text_content")
    .replace("self._execute_agent_stream", "execute_agent_stream")
    .replace("self._invoke_agent", "invoke_agent")
    .replace("await self._emit_hitl_pause", "await emit_hitl_pause")
    .replace("input_state = _compact_input_state", "input_state = compact_input_state")
)
(PKG / "agent_runner.py").write_text(
    '''"""ReAct agent execution — invoke, stream, and result parsing."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

from app.core.error_classifier import classify_error
from app.core.retry import with_retry
from app.core.telemetry import agent_span, get_current_trace_id
from app.workflow.strategies.react.helpers import compact_input_state
from app.workflow.strategies.react.hitl import emit_hitl_pause
from app.workflow.strategies.react.streaming import StreamCallback

logger = logging.getLogger(__name__)

'''
    + runner,
    encoding="utf-8",
)

# --- learning.py ---
(PKG / "learning.py").write_text(
    '''"""Post-execution learning and graceful fallback for ReAct agents."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from app.core.redact import redact
from app.workflow.strategies.react.helpers import collect_failed_tools, estimate_confidence

logger = logging.getLogger(__name__)

'''
    + dedent_method_block(chunk(702, 867))
    .replace("async def _auto_learn", "async def auto_learn")
    .replace("def _exec_fallback", "def exec_fallback")
    .replace("_estimate_confidence", "estimate_confidence")
    .replace("_collect_failed_tools", "collect_failed_tools"),
    encoding="utf-8",
)

# Fix helpers.py - rename functions without leading underscore for public API
helpers_text = (PKG / "helpers.py").read_text(encoding="utf-8")
helpers_text = helpers_text.replace("def _cap_context_block", "def cap_context_block")
helpers_text = helpers_text.replace("def _build_recall_context", "def build_recall_context")
helpers_text = helpers_text.replace("def _compact_input_state", "def compact_input_state")
helpers_text = helpers_text.replace("def _collect_failed_tools", "def collect_failed_tools")
helpers_text = helpers_text.replace("def _estimate_confidence", "def estimate_confidence")
(PKG / "helpers.py").write_text(helpers_text, encoding="utf-8")

print(f"Split complete -> {PKG}")
