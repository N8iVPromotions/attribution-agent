"""
tests/test_a2a_dispatch.py
--------------------------
A2A dispatch — local and network transports, plus the HTTP server surface.

No live agents are invoked: `run_local` is stubbed so the tests exercise routing,
the transport contract, and the server endpoints without Anthropic credentials.
"""

from __future__ import annotations

import pytest

from agents.a2a import dispatcher as disp
from agents.a2a.dispatcher import AgentDispatcher, HttpA2ATransport, LocalTransport


@pytest.fixture
def stub_local(monkeypatch):
    """Replace run_local with an echo so routing is observable without real agents."""
    calls = []

    def _fake(agent_id, input_data):
        calls.append((agent_id, input_data))
        return {"echoed": agent_id, "input": input_data}

    monkeypatch.setattr(disp, "run_local", _fake)
    return calls


# ── Dispatcher + LocalTransport ─────────────────────────────────────────────────


def test_local_dispatch_routes_to_run_local(stub_local):
    d = AgentDispatcher()  # defaults to LocalTransport
    out = d.dispatch("revenue-analyst", {"client_id": "c1"})
    assert out == {"echoed": "revenue-analyst", "input": {"client_id": "c1"}}
    assert stub_local == [("revenue-analyst", {"client_id": "c1"})]


def test_dispatch_rejects_unknown_agent(stub_local):
    d = AgentDispatcher(transport=LocalTransport())
    with pytest.raises(ValueError, match="Unknown agent_id"):
        d.dispatch("nope", {})


# ── HttpA2ATransport ────────────────────────────────────────────────────────────


def test_http_transport_posts_and_unwraps_output(monkeypatch):
    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"agent_id": "data-quality", "output": {"passed": True}}

    def _fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _Resp()

    import requests

    monkeypatch.setattr(requests, "post", _fake_post)

    transport = HttpA2ATransport("http://peer:8000/", headers={"X-API-Key": "k"})
    d = AgentDispatcher(transport=transport)
    out = d.dispatch("data-quality", {"client_id": "c1"})

    assert out == {"passed": True}
    assert captured["url"] == "http://peer:8000/a2a/dispatch"
    assert captured["json"] == {
        "agent_id": "data-quality",
        "input": {"client_id": "c1"},
    }
    assert captured["headers"] == {"X-API-Key": "k"}


# ── Server (discovery + dispatch) ───────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from agents.a2a import server

    def _fake(agent_id, input_data):
        return {"echoed": agent_id}

    monkeypatch.setattr(server, "run_local", _fake)
    monkeypatch.setenv("API_KEY_ADMIN", "test-admin-key")
    return TestClient(server.app)


def test_discovery_lists_agent_cards(client):
    resp = client.get("/.well-known/agent-cards")
    assert resp.status_code == 200
    ids = {c["agent_id"] for c in resp.json()["agent_cards"]}
    assert {
        "data-quality",
        "revenue-analyst",
        "executive-reporting",
        "governance-reviewer",
    } <= ids


def test_server_dispatch_runs_agent(client):
    resp = client.post(
        "/a2a/dispatch",
        headers={"X-API-Key": "test-admin-key"},
        json={"agent_id": "data-quality", "input": {"client_id": "c1"}},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "agent_id": "data-quality",
        "output": {"echoed": "data-quality"},
    }


def test_server_dispatch_unknown_agent_404(client):
    resp = client.post(
        "/a2a/dispatch",
        headers={"X-API-Key": "test-admin-key"},
        json={"agent_id": "ghost", "input": {}},
    )
    assert resp.status_code == 404


def test_server_dispatch_requires_authentication(client):
    resp = client.post(
        "/a2a/dispatch", json={"agent_id": "data-quality", "input": {}}
    )
    assert resp.status_code == 401


def test_server_dispatch_rate_limits_principal(client, monkeypatch):
    from agents.a2a import server

    server._request_times.clear()
    monkeypatch.setattr(server, "_RATE_LIMIT_PER_MINUTE", 1)
    request = {"agent_id": "data-quality", "input": {"client_id": "c1"}}
    headers = {"X-API-Key": "test-admin-key"}

    assert client.post("/a2a/dispatch", headers=headers, json=request).status_code == 200
    assert client.post("/a2a/dispatch", headers=headers, json=request).status_code == 429
