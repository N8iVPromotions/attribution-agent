"""
api/main.py
-----------
FastAPI application. Exposes the attribution pipeline via REST.
"""
from __future__ import annotations
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from api.version import __version__, build_info
from api.models import HealthResponse

logger = logging.getLogger(__name__)

_ALLOW_ORIGINS = [o.strip() for o in os.environ.get("API_CORS_ORIGINS", "*").split(",")]


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        from utils.databricks_writer import ensure_ops_tables
        ensure_ops_tables()
    except Exception as exc:
        logger.warning(f"[API] ensure_ops_tables on startup: {exc}")
    yield


app = FastAPI(
    title="Attribution Agent API",
    version=__version__,
    description="REST API for the N8iV attribution pipeline",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def audit_middleware(request: Request, call_next) -> Response:
    request_id = str(uuid.uuid4())[:8]
    start = time.monotonic()
    response = await call_next(request)
    elapsed_ms = int((time.monotonic() - start) * 1000)

    try:
        from utils.audit_logger import log_event
        log_event(
            "API_REQUEST",
            actor=request.headers.get("X-Actor", "api"),
            resource=str(request.url.path),
            action=request.method,
            outcome="success" if response.status_code < 400 else "failure",
            detail={
                "status_code": response.status_code,
                "elapsed_ms": elapsed_ms,
                "request_id": request_id,
            },
        )
    except Exception:
        pass

    response.headers["X-Request-Id"] = request_id
    return response


# ── Routers ───────────────────────────────────────────────────────────────────

from api.routers import pipeline, clients, reports, approvals  # noqa: E402
from agents.a2a.server import router as a2a_router  # noqa: E402

app.include_router(pipeline.router, prefix="/api/v1")
app.include_router(clients.router, prefix="/api/v1")
app.include_router(reports.router, prefix="/api/v1")
app.include_router(approvals.router, prefix="/api/v1")
app.include_router(a2a_router)  # A2A discovery + dispatch (well-known paths, no prefix)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    info = build_info()
    return HealthResponse(
        status="ok",
        version=info["version"],
        git_sha=info["git_sha"],
        deploy_target=info["deploy_target"],
    )
