"""
MCP Client Manager — Re-export of the real implementation.

This module re-exports MCPClientManager from app.services.mcp_client_manager
so that code importing from app.workflow.mcp.manager gets the functional
SDK-backed implementation instead of the previous stub.
"""
from app.services.mcp_client_manager import MCPClientManager  # noqa: F401

__all__ = ["MCPClientManager"]
