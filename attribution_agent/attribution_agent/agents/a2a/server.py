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

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from agents.a2a.agent_card import AGENT_CARDS
from agents.a2a.dispatcher import run_local

logger = logging.getLogger(__name__)

router = APIRouter(tags=["a2a"])


class DispatchRequest(BaseModel):
    agent_id: str
    input: dict = {}


class DispatchResponse(BaseModel):
    agent_id: str
    output: dict | str | list | None


@router.get("/.well-known/agent-cards")
async def agent_cards() -> dict:
    """A2A discovery — list the agents this node serves."""
    return {"agent_cards": [card.to_dict() for card in AGENT_CARDS.values()]}


@router.post("/a2a/dispatch", response_model=DispatchResponse)
async def dispatch(req: DispatchRequest) -> DispatchResponse:
    if req.agent_id not in AGENT_CARDS:
        raise HTTPException(status_code=404, detail=f"Unknown agent_id: {req.agent_id}")
    try:
        output = run_local(req.agent_id, req.input)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # agent execution failure
        logger.exception(f"[A2A] dispatch to {req.agent_id} failed")
        raise HTTPException(status_code=502, detail=f"Agent execution failed: {exc}") from exc
    return DispatchResponse(agent_id=req.agent_id, output=output)


# Standalone app: `uvicorn agents.a2a.server:app`
app = FastAPI(title="Attribution Agent — A2A Node", description="A2A network surface")
app.include_router(router)
