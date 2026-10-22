from __future__ import annotations

from contextlib import AsyncExitStack

import pytest
from fastmcp import Client
from fastmcp.client import FastMCPTransport
from mcp.types import Tool

from prometheus_mcp import __main__ as entrypoint
from prometheus_mcp.server import mcp
from prometheus_mcp.tools.effects import READ


def test_server_name() -> None:
    assert mcp.name == "prometheus-mcp"


def test_server_instructions_describe_the_workflow() -> None:
    assert mcp.instructions is not None
    assert "chart_range" in mcp.instructions
    assert "query_range" in mcp.instructions


def test_main_calls_mcp_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(entrypoint.mcp, "run", lambda: calls.append(True))
    entrypoint.main()
    assert calls == [True]


@pytest.fixture
async def tools(mcp_client: Client[FastMCPTransport]) -> list[Tool]:
    return await mcp_client.list_tools()


async def test_every_tool_has_a_title(tools: list[Tool]) -> None:
    assert [tool.name for tool in tools if not tool.title] == []


async def test_every_tool_is_tagged_read(tools: list[Tool]) -> None:
    tags = [set(tool.meta["fastmcp"]["tags"]) for tool in tools if tool.meta]
    assert len(tags) == len(tools)
    assert all(READ in tool_tags and "write" not in tool_tags for tool_tags in tags)


async def test_every_tool_only_reads_a_closed_world(tools: list[Tool]) -> None:
    effects = {
        (tool.annotations.read_only_hint, tool.annotations.open_world_hint)
        for tool in tools
        if tool.annotations
    }
    assert effects == {(True, False)}
    assert all(tool.annotations for tool in tools)


async def test_every_parameter_has_a_description(tools: list[Tool]) -> None:
    undescribed = [
        f"{tool.name}.{name}"
        for tool in tools
        for name, schema in tool.input_schema["properties"].items()
        if not schema.get("description")
    ]
    assert undescribed == []


async def test_parameter_descriptions_carry_no_docstring_indentation(
    tools: list[Tool],
) -> None:
    """A wrapped docstring line keeps its indentation in the schema the model
    reads, so a description must not hold a newline followed by a space."""
    descriptions = [
        f"{tool.name}.{name}: {schema['description']!r}"
        for tool in tools
        for name, schema in tool.input_schema["properties"].items()
        if "\n " in schema.get("description", "")
    ]
    assert descriptions == []


async def test_the_servers_dependency_is_not_a_parameter(tools: list[Tool]) -> None:
    assert [
        tool.name for tool in tools if "servers" in tool.input_schema["properties"]
    ] == []


async def test_a_bad_configuration_stops_the_server_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROMETHEUS_MCP_SERVERS", "prod")
    monkeypatch.delenv("PROMETHEUS_MCP_PROD_URL", raising=False)
    async with AsyncExitStack() as stack:
        with pytest.raises(RuntimeError, match="set PROMETHEUS_MCP_PROD_URL"):
            await stack.enter_async_context(Client(mcp))
