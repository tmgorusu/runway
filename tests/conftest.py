import socket
from datetime import datetime, timezone

import pytest

from runway.contracts import Call

JUNE_4CP = datetime(2025, 6, 23, 21, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Every Machine test runs with the network disabled."""

    def guard(*args, **kwargs):
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)


@pytest.fixture
def make_call():
    def _make(mw, duration_min=90, call_id="test"):
        return Call(call_id, JUNE_4CP, duration_min, mw, peak_odds=1.0, ambient_c=37.0)

    return _make


NO_NET_SITE = """
import socket
def _blocked(*a, **k):
    raise RuntimeError("network disabled in tests")
socket.socket.connect = _blocked
socket.create_connection = _blocked
"""


@pytest.fixture
def offline_env(tmp_path):
    """Environment for subprocesses that blocks every socket connect."""
    import os

    site = tmp_path / "nonet"
    site.mkdir()
    (site / "sitecustomize.py").write_text(NO_NET_SITE)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(site), env.get("PYTHONPATH", "")]))
    return env
