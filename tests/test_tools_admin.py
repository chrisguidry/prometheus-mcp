from __future__ import annotations

import httpx
import pytest
import respx
from fastmcp import Client

from prometheus_mcp.server import mcp


def build_info(version: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": "success",
            "data": {
                "version": version,
                "revision": "cb7cbad5f9a2823a622aaa668833ca04f50a0ea7",
                "branch": "HEAD",
                "buildUser": "root@builder",
                "buildDate": "20260501-10:00:00",
                "goVersion": "go1.26.2",
            },
        },
    )


@pytest.fixture
def two_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMETHEUS_MCP_SERVERS", "prod,stg")
    monkeypatch.setenv("PROMETHEUS_MCP_PROD_URL", "https://prom-prod/")
    monkeypatch.setenv("PROMETHEUS_MCP_PROD_HEADER_AUTHORIZATION", "Bearer secret")
    monkeypatch.setenv("PROMETHEUS_MCP_STG_URL", "https://reader:hunter2@prom-stg/")


@pytest.fixture
def prod_build_info(mock_router: respx.Router) -> respx.Route:
    return mock_router.get("https://prom-prod/api/v1/status/buildinfo").mock(
        return_value=build_info("3.5.0")
    )


@pytest.fixture
def stg_build_info(mock_router: respx.Router) -> respx.Route:
    return mock_router.get("https://prom-stg/api/v1/status/buildinfo")


async def test_list_servers_returns_slug_url_and_version(
    two_servers: None, prod_build_info: respx.Route, stg_build_info: respx.Route
) -> None:
    stg_build_info.mock(return_value=build_info("3.6.0-rc.0"))
    async with Client(mcp) as client:
        result = await client.call_tool("list_servers", {})
    assert result.structured_content == {
        "result": [
            {"slug": "prod", "url": "https://prom-prod/", "version": "3.5.0"},
            {"slug": "stg", "url": "https://prom-stg/", "version": "3.6.0-rc.0"},
        ]
    }


async def test_list_servers_does_not_leak_credentials(
    two_servers: None, prod_build_info: respx.Route, stg_build_info: respx.Route
) -> None:
    stg_build_info.mock(return_value=build_info("3.5.0"))
    async with Client(mcp) as client:
        result = await client.call_tool("list_servers", {})
    text = str(result.content) + str(result.structured_content)
    assert "secret" not in text
    assert "Authorization" not in text
    assert "hunter2" not in text


async def test_list_servers_asks_for_the_version_with_a_short_timeout(
    two_servers: None, prod_build_info: respx.Route, stg_build_info: respx.Route
) -> None:
    stg_build_info.mock(return_value=build_info("3.5.0"))
    async with Client(mcp) as client:
        await client.call_tool("list_servers", {})
    assert prod_build_info.calls.last.request.extensions["timeout"]["read"] == 5


STG_WITHOUT_A_VERSION = {
    "result": [
        {"slug": "prod", "url": "https://prom-prod/", "version": "3.5.0"},
        {"slug": "stg", "url": "https://prom-stg/", "version": None},
    ]
}


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("connection refused"), httpx.ReadTimeout("timed out")],
)
async def test_list_servers_reports_no_version_for_a_server_that_is_down(
    two_servers: None,
    prod_build_info: respx.Route,
    stg_build_info: respx.Route,
    failure: httpx.HTTPError,
) -> None:
    stg_build_info.mock(side_effect=failure)
    async with Client(mcp) as client:
        result = await client.call_tool("list_servers", {})
    assert result.structured_content == STG_WITHOUT_A_VERSION


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(404, text="404 page not found"),
        httpx.Response(200, text="<html>login</html>"),
        httpx.Response(200, json={"status": "error", "errorType": "x", "error": "y"}),
        httpx.Response(200, json={"status": "success", "data": ["3.5.0"]}),
        httpx.Response(200, json={"status": "success", "data": {}}),
        httpx.Response(200, json={"status": "success", "data": {"version": ""}}),
        httpx.Response(200, json={"status": "success", "data": {"version": 3}}),
    ],
)
async def test_list_servers_reports_no_version_for_a_server_without_one(
    two_servers: None,
    prod_build_info: respx.Route,
    stg_build_info: respx.Route,
    answer: httpx.Response,
) -> None:
    stg_build_info.mock(return_value=answer)
    async with Client(mcp) as client:
        result = await client.call_tool("list_servers", {})
    assert result.structured_content == STG_WITHOUT_A_VERSION


async def test_list_servers_empty_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROMETHEUS_MCP_SERVERS", raising=False)
    async with Client(mcp) as client:
        result = await client.call_tool("list_servers", {})
    assert result.structured_content == {"result": []}
