"""Verify the MCP server registers all expected tools."""

from __future__ import annotations

from insyte.mcp.server import TOOL_NAMES, build_mcp_server


class _StubService:
    def __getattr__(self, name):
        return lambda *args, **kwargs: {}


def test_all_tools_registered() -> None:
    server = build_mcp_server(_StubService())  # type: ignore[arg-type]
    registered = {tool.name for tool in server._tool_manager.list_tools()}
    assert set(TOOL_NAMES) <= registered
    assert len(registered) == len(TOOL_NAMES)


def test_tools_have_descriptions() -> None:
    server = build_mcp_server(_StubService())  # type: ignore[arg-type]
    for tool in server._tool_manager.list_tools():
        assert tool.description  # every tool documents itself for the model
