from __future__ import annotations

import pytest

from prometheus_mcp import __main__ as entrypoint
from prometheus_mcp import server as server_module
from prometheus_mcp.config import ServerConfig
from prometheus_mcp.server import lifespan, mcp


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


async def testlifespan_closes_clients_on_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[bool] = []

    async def fake_aclose() -> None:
        closed.append(True)

    monkeypatch.setattr(server_module, "aclose_clients", fake_aclose)
    monkeypatch.setattr(
        server_module,
        "SERVERS",
        {"x": ServerConfig(slug="x", url="https://prom/")},
    )
    async with lifespan(mcp):
        pass
    assert closed == [True]
