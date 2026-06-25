"""
agents/a2a/dispatcher.py
------------------------
Agent dispatch with pluggable transport.

`AgentDispatcher` routes a task to a named agent. By default it runs the agent
locally in-process (`LocalTransport`); pass `HttpA2ATransport` to route the same
call to a remote A2A endpoint over HTTP (e.g. a peer service running
`agents/a2a/server.py`). The wire contract is shared by both ends, so a request
behaves identically whether it is served locally or across the network.
"""
from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)

AGENT_IDS = ("data-quality", "revenue-analyst", "executive-reporting", "governance-reviewer")


# ── Local routing ───────────────────────────────────────────────────────────────

def run_local(agent_id: str, input_data: dict) -> dict | str | list:
    """Run an agent in-process. Shared by LocalTransport and the A2A server."""
    handlers = {
        "data-quality": _data_quality,
        "revenue-analyst": _revenue_analyst,
        "executive-reporting": _executive_reporting,
        "governance-reviewer": _governance_reviewer,
    }
    handler = handlers.get(agent_id)
    if not handler:
        raise ValueError(f"Unknown agent_id: {agent_id}. Available: {list(handlers.keys())}")
    return handler(input_data)


def _data_quality(inp: dict) -> dict:
    from agents.intelligence.n8iv_agents import run_data_quality_agent
    return run_data_quality_agent(
        client_id=inp.get("client_id", ""),
        validation_reports=inp.get("validation_reports", []),
        ingest_summary=inp.get("ingest_summary", {}),
    )


def _revenue_analyst(inp: dict) -> str:
    from agents.intelligence.n8iv_agents import run_revenue_analyst_agent
    return run_revenue_analyst_agent(
        client_id=inp.get("client_id", ""),
        client_name=inp.get("client_name", ""),
        channel_data=inp.get("channel_data", []),
        attribution_model=inp.get("attribution_model", "last_touch"),
    )


def _executive_reporting(inp: dict) -> dict:
    from agents.intelligence.n8iv_agents import run_executive_reporting_agent
    return run_executive_reporting_agent(
        client_id=inp.get("client_id", ""),
        client_name=inp.get("client_name", ""),
        analyst_output=inp.get("analyst_output", ""),
        channel_data=inp.get("channel_data", []),
        attribution_model=inp.get("attribution_model", "last_touch"),
    )


def _governance_reviewer(inp: dict) -> list[str]:
    from agents.intelligence.n8iv_agents import run_governance_review
    return run_governance_review(
        client_id=inp.get("client_id", ""),
        client_name=inp.get("client_name", ""),
        report_narrative=inp.get("report_narrative", ""),
        report_json=inp.get("report_json", {}),
    )


# ── Transports ──────────────────────────────────────────────────────────────────

class Transport(Protocol):
    def send(self, agent_id: str, input_data: dict) -> dict | str | list: ...


class LocalTransport:
    """Runs the agent in-process."""

    def send(self, agent_id: str, input_data: dict) -> dict | str | list:
        return run_local(agent_id, input_data)


class HttpA2ATransport:
    """Routes the agent call to a remote A2A endpoint over HTTP.

    POSTs ``{"agent_id": ..., "input": ...}`` to ``{base_url}/a2a/dispatch`` and
    returns the ``output`` field of the response. Transient failures are retried
    with exponential backoff.
    """

    def __init__(
        self,
        base_url: str,
        headers: dict | None = None,
        timeout: int = 30,
        retries: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self.timeout = timeout
        self.retries = retries

    def send(self, agent_id: str, input_data: dict) -> dict | str | list:
        import requests
        from tenacity import retry, stop_after_attempt, wait_exponential

        @retry(stop=stop_after_attempt(self.retries),
               wait=wait_exponential(min=2, max=10), reraise=True)
        def _post() -> dict:
            resp = requests.post(
                f"{self.base_url}/a2a/dispatch",
                json={"agent_id": agent_id, "input": input_data},
                headers=self.headers,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()

        body = _post()
        return body.get("output")


# ── Dispatcher ──────────────────────────────────────────────────────────────────

class AgentDispatcher:
    def __init__(self, transport: Transport | None = None) -> None:
        self.transport: Transport = transport or LocalTransport()

    def dispatch(self, agent_id: str, input_data: dict) -> dict | str | list:
        """Dispatch a task to a named agent and return its output.

        Routing (local or network) is determined by the configured transport.
        """
        if agent_id not in AGENT_IDS:
            raise ValueError(f"Unknown agent_id: {agent_id}. Available: {list(AGENT_IDS)}")
        logger.info(f"[Dispatcher] Dispatching to {agent_id} via {type(self.transport).__name__}")
        return self.transport.send(agent_id, input_data)
