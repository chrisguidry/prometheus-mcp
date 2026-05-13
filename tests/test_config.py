from __future__ import annotations

import pytest

from prometheus_mcp.config import (
    ConfigError,
    ServerConfig,
    UnknownServerError,
    get_server,
    load_servers,
)


def test_empty_environment_yields_empty_registry() -> None:
    assert load_servers({}) == {}


def test_loads_multiple_servers() -> None:
    servers = load_servers(
        {
            "PROMETHEUS_MCP_SERVERS": "prod, stg ,dev",
            "PROMETHEUS_MCP_PROD_URL": "https://prom-prod/",
            "PROMETHEUS_MCP_PROD_HEADER_AUTHORIZATION": "Bearer xxx",
            "PROMETHEUS_MCP_PROD_HEADER_X_SCOPE_ORGID": "tenant-7",
            "PROMETHEUS_MCP_STG_URL": "https://prom-stg/",
            "PROMETHEUS_MCP_DEV_URL": "http://localhost:9090/",
        }
    )
    assert set(servers) == {"prod", "stg", "dev"}
    assert servers["prod"] == ServerConfig(
        slug="prod",
        url="https://prom-prod/",
        headers={"Authorization": "Bearer xxx", "X-Scope-Orgid": "tenant-7"},
    )
    assert servers["stg"].headers == {}


def test_hyphenated_slug_works_for_env_lookup() -> None:
    servers = load_servers(
        {
            "PROMETHEUS_MCP_SERVERS": "us-east",
            "PROMETHEUS_MCP_US_EAST_URL": "https://prom-us-east/",
            "PROMETHEUS_MCP_US_EAST_HEADER_AUTHORIZATION": "Bearer y",
        }
    )
    assert servers["us-east"].url == "https://prom-us-east/"
    assert servers["us-east"].headers == {"Authorization": "Bearer y"}


def test_missing_url_raises_with_helpful_message() -> None:
    with pytest.raises(ConfigError, match="missing URL for server 'prod'"):
        load_servers({"PROMETHEUS_MCP_SERVERS": "prod"})


def test_invalid_slug_rejected() -> None:
    with pytest.raises(ConfigError, match=r"invalid server slug 'prod!'"):
        load_servers({"PROMETHEUS_MCP_SERVERS": "Prod!"})


def test_duplicate_slug_rejected() -> None:
    with pytest.raises(ConfigError, match="duplicate server slug 'prod'"):
        load_servers(
            {
                "PROMETHEUS_MCP_SERVERS": "prod,prod",
                "PROMETHEUS_MCP_PROD_URL": "https://prom-prod/",
            }
        )


def test_get_server_is_case_insensitive() -> None:
    servers = load_servers(
        {
            "PROMETHEUS_MCP_SERVERS": "prod",
            "PROMETHEUS_MCP_PROD_URL": "https://prom-prod/",
        }
    )
    assert get_server("PROD", servers).slug == "prod"
    assert get_server("  prod  ", servers).slug == "prod"


def test_get_server_unknown_lists_available() -> None:
    servers = load_servers(
        {
            "PROMETHEUS_MCP_SERVERS": "prod,stg",
            "PROMETHEUS_MCP_PROD_URL": "https://prom-prod/",
            "PROMETHEUS_MCP_STG_URL": "https://prom-stg/",
        }
    )
    with pytest.raises(UnknownServerError) as excinfo:
        get_server("dev", servers)
    assert excinfo.value.slug == "dev"
    assert excinfo.value.available == ["prod", "stg"]
    assert "available: prod, stg" in str(excinfo.value)


def test_get_server_unknown_with_empty_registry() -> None:
    with pytest.raises(UnknownServerError, match="no servers are configured"):
        get_server("prod", {})


def test_load_servers_defaults_to_os_environ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROMETHEUS_MCP_SERVERS", "envtest")
    monkeypatch.setenv("PROMETHEUS_MCP_ENVTEST_URL", "http://envtest/")
    servers = load_servers()
    assert servers["envtest"].url == "http://envtest/"
