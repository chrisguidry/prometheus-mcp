from __future__ import annotations

import pytest

from prometheus_mcp import __main__ as entrypoint
from prometheus_mcp.server import mcp


def test_server_name() -> None:
    assert mcp.name == "prometheus-mcp"


def test_main_calls_mcp_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(entrypoint.mcp, "run", lambda: calls.append(True))
    entrypoint.main()
    assert calls == [True]
