"""Entry point: ``python -m prometheus_mcp`` and the ``prometheus-mcp`` script."""

from __future__ import annotations

from prometheus_mcp.server import mcp


def main() -> None:
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
