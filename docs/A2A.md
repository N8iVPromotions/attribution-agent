# Agent-to-Agent (A2A) Dispatch

The four pipeline agents (`data-quality`, `revenue-analyst`,
`executive-reporting`, `governance-reviewer`) can be invoked either in-process or
across the network through a single dispatcher. The wire contract is identical
both ways, so code written against `AgentDispatcher` does not change when an
agent moves to a remote node.

Modules: `agents/a2a/dispatcher.py` (client) and `agents/a2a/server.py` (server).
Agent interfaces are described machine-readably in `agents/a2a/agent_card.py`.

## Local dispatch (default)

```python
from agents.a2a.dispatcher import AgentDispatcher

d = AgentDispatcher()                      # LocalTransport — runs in-process
out = d.dispatch("revenue-analyst", {
    "client_id": "acme_co",
    "client_name": "Acme Co",
    "channel_data": [...],
    "attribution_model": "last_touch",
})
```

## Network dispatch

Point the dispatcher at a peer node running the A2A server:

```python
from agents.a2a.dispatcher import AgentDispatcher, HttpA2ATransport

transport = HttpA2ATransport(
    "https://peer-node.example.com",       # base URL of the remote A2A node
    headers={"X-API-Key": "..."},          # optional auth headers
    timeout=30,
    retries=3,                             # exponential backoff on transient failures
)
d = AgentDispatcher(transport=transport)
out = d.dispatch("data-quality", {"client_id": "acme_co", ...})
```

`HttpA2ATransport` POSTs `{"agent_id", "input"}` to `{base_url}/a2a/dispatch` and
returns the response's `output` field.

## Serving agents

The A2A router is mounted on the main REST API (`api/main.py`), so any deployment
of that app is already an A2A node. Endpoints (no prefix):

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/.well-known/agent-cards` | Discovery — list served agents and their I/O schemas |
| POST | `/a2a/dispatch` | Run a named agent: body `{"agent_id", "input"}` → `{"agent_id", "output"}` |

Run the A2A surface standalone:

```bash
cd attribution_agent/attribution_agent
uvicorn agents.a2a.server:app --port 8001
```

Error semantics: unknown `agent_id` → `404`; an agent that raises during
execution → `502`.

## Adding an agent

1. Add a handler in `dispatcher.py` (`run_local`) and to `AGENT_IDS`.
2. Add an `AgentCard` to `AGENT_CARDS` in `agent_card.py` so it appears in
   discovery with input/output schemas.

Discovery and dispatch pick it up automatically; no server changes needed.
