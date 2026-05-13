from __future__ import annotations

import pytest

from prometheus_mcp import server as server_module
from prometheus_mcp.config import ServerConfig
from prometheus_mcp.tools.admin import list_servers


@pytest.fixture
def two_servers(monkeypatch: pytest.MonkeyPatch) -> dict[str, ServerConfig]:
    servers = {
        "prod": ServerConfig(
            slug="prod",
            url="https://prom-prod/",
            headers={"Authorization": "Bearer secret"},
        ),
        "stg": ServerConfig(slug="stg", url="https://prom-stg/"),
    }
    monkeypatch.setattr(server_module, "SERVERS", servers)
    return servers


async def test_list_servers_returns_slug_and_url(
    two_servers: dict[str, ServerConfig],
) -> None:
    result = await list_servers()
    assert sorted(result, key=lambda r: r["slug"]) == [
        {"slug": "prod", "url": "https://prom-prod/"},
        {"slug": "stg", "url": "https://prom-stg/"},
    ]


async def test_list_servers_does_not_leak_headers(
    two_servers: dict[str, ServerConfig],
) -> None:
    result = await list_servers()
    flat = " ".join(f"{k}={v}" for entry in result for k, v in entry.items())
    assert "secret" not in flat
    assert "Authorization" not in flat


async def test_list_servers_empty_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_module, "SERVERS", {})
    assert await list_servers() == []
