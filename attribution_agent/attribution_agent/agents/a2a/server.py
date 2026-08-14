"""
agents/a2a/server.py
--------------------
A2A network surface — exposes this node's agents to remote callers.

Two endpoints, mirroring the Google A2A draft shape:
  * GET  /.well-known/agent-cards  — discovery: machine-readable agent cards
  * POST /a2a/dispatch             — run a named agent and return its output

The dispatch endpoint runs the same `run_local` routing used in-process, so a
remote `HttpA2ATransport` call and a local `AgentDispatcher` call are equivalent.
Mount `router` onto an existing FastAPI app, or run this module's `app` standalone.
"""

from __future__ import annotations

import logging
import json
import os
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel

from agents.a2a.agent_card import AGENT_CARDS
from agents.a2a.dispatcher import run_local
from api.auth import AuthPrincipal, require_auth, require_client_access

logger = logging.getLogger(__name__)
_RATE_LIMIT_PER_MINUTE = max(1, int(os.environ.get("A2A_RATE_LIMIT_PER_MINUTE", "30")))
_request_times: dict[str, deque[float]] = defaultdict(deque)
_rate_limit_lock = Lock()

router = APIRouter(tags=["a2a"])


class DispatchRequest(BaseModel):
    agent_id: str
    input: dict = {}


class DispatchResponse(BaseModel):
    agent_id: str
    output: dict | str | list | None


def _enforce_rate_limit(principal: AuthPrincipal) -> None:
    key = principal.client_id or principal.role.value
    now = time.monotonic()
    with _rate_limit_lock:
        requests = _request_times[key]
        while requests and requests[0] <= now - 60:
            requests.popleft()
        if len(requests) >= _RATE_LIMIT_PER_MINUTE:
            raise HTTPException(status_code=429, detail="A2A rate limit exceeded")
        requests.append(now)


@router.get("/.well-known/agent-cards")
async def agent_cards() -> dict:
    """A2A discovery — list the agents this node serves."""
    return {"agent_cards": [card.to_dict() for card in AGENT_CARDS.values()]}


@router.post("/a2a/dispatch", response_model=DispatchResponse)
async def dispatch(
    req: DispatchRequest,
    principal: AuthPrincipal = Depends(require_auth),
) -> DispatchResponse:
    _enforce_rate_limit(principal)
    if len(json.dumps(req.input, default=str).encode("utf-8")) > 65_536:
        raise HTTPException(status_code=413, detail="Agent input exceeds 64 KiB")
    if principal.client_id:
        require_client_access(principal, str(req.input.get("client_id", "")))
    if req.agent_id not in AGENT_CARDS:
        raise HTTPException(status_code=404, detail=f"Unknown agent_id: {req.agent_id}")
    try:
        output = run_local(req.agent_id, req.input)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # agent execution failure
        logger.exception(f"[A2A] dispatch to {req.agent_id} failed")
        raise HTTPException(
            status_code=502, detail=f"Agent execution failed: {exc}"
        ) from exc
    return DispatchResponse(agent_id=req.agent_id, output=output)


# Standalone app: `uvicorn agents.a2a.server:app`
app = FastAPI(title="ARIE A2A Node", description="A2A network surface for ARIE")
app.include_router(router)
