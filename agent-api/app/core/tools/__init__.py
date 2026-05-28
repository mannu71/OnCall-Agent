"""Tool-routing utilities (top-K filter, schema sizing).

Sibling to ``app.core.tool_registry`` — this package owns *strategies* for
choosing which tools to expose per turn, while the registry owns
*storage* of tool definitions.
"""
from app.core.tools.router import (
    ToolRouter,
    rank_tools,
    schema_token_estimate,
)

__all__ = ["ToolRouter", "rank_tools", "schema_token_estimate"]
