"""An MCP server for querying multiple Prometheus instances."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("prometheus-mcp")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
