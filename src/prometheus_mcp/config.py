"""Configuration of the Prometheus backends this server can query.

Servers are declared with environment variables, all prefixed
``PROMETHEUS_MCP_``:

- ``PROMETHEUS_MCP_SERVERS`` — comma-separated slugs.
- ``PROMETHEUS_MCP_<SLUG>_URL`` — base URL for the slug's Prometheus.
- ``PROMETHEUS_MCP_<SLUG>_HEADER_<NAME>`` — header sent on every request.
  ``<NAME>`` is uppercased SCREAMING_SNAKE_CASE; it is converted to
  ``Header-Case`` for the wire (``AUTHORIZATION`` → ``Authorization``,
  ``X_SCOPE_ORGID`` → ``X-Scope-Orgid``).

Slugs are case-insensitive at the API surface; they are stored lowercased
and uppercased only for env-var lookup.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

__all__ = [
    "ConfigError",
    "ServerConfig",
    "UnknownServerError",
    "get_server",
    "load_servers",
]

_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_EMPTY_HEADERS: Mapping[str, str] = MappingProxyType({})


class ConfigError(ValueError):
    """Raised when the environment doesn't describe a valid server."""


class UnknownServerError(KeyError):
    """Raised when a tool is asked for a slug that wasn't configured."""

    def __init__(self, slug: str, available: list[str]) -> None:
        self.slug = slug
        self.available = available
        if available:
            hint = "available: " + ", ".join(available)
        else:
            hint = "no servers are configured (set PROMETHEUS_MCP_SERVERS)"
        super().__init__(f"unknown Prometheus server {slug!r} ({hint})")


@dataclass(frozen=True)
class ServerConfig:
    """A single Prometheus backend addressable by ``slug``."""

    slug: str
    url: str
    headers: Mapping[str, str] = field(default_factory=lambda: _EMPTY_HEADERS)


def load_servers(env: Mapping[str, str] | None = None) -> dict[str, ServerConfig]:
    """Build the slug → ``ServerConfig`` registry from environment variables.

    Pass an explicit mapping in tests; defaults to ``os.environ``.
    """
    source = os.environ if env is None else env
    raw = source.get("PROMETHEUS_MCP_SERVERS", "")
    slugs = [s.strip().lower() for s in raw.split(",") if s.strip()]

    servers: dict[str, ServerConfig] = {}
    for slug in slugs:
        if not _SLUG_PATTERN.match(slug):
            raise ConfigError(
                f"invalid server slug {slug!r}: must match [a-z0-9][a-z0-9_-]*"
            )
        if slug in servers:
            raise ConfigError(f"duplicate server slug {slug!r}")

        prefix = f"PROMETHEUS_MCP_{slug.upper().replace('-', '_')}_"
        url = source.get(f"{prefix}URL")
        if not url:
            raise ConfigError(f"missing URL for server {slug!r}: set {prefix}URL")

        header_prefix = f"{prefix}HEADER_"
        headers: dict[str, str] = {}
        for key, value in source.items():
            if not key.startswith(header_prefix):
                continue
            name = _normalize_header_name(key[len(header_prefix) :])
            headers[name] = value

        servers[slug] = ServerConfig(slug=slug, url=url, headers=headers)

    return servers


def get_server(slug: str, servers: Mapping[str, ServerConfig]) -> ServerConfig:
    """Look up a server by slug, raising ``UnknownServerError`` with hints."""
    key = slug.strip().lower()
    if key not in servers:
        raise UnknownServerError(slug, sorted(servers))
    return servers[key]


def _normalize_header_name(raw: str) -> str:
    """Turn ``X_SCOPE_ORGID`` into ``X-Scope-Orgid``."""
    parts = raw.split("_")
    return "-".join(part.capitalize() for part in parts if part)
