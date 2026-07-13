from __future__ import annotations
import os
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from api.auth import require_auth
from api.models import PipelineRunRequest, PipelineRunResponse, PipelineRunRecord
from config.rbac_config import Role, Permission, require_permission
from utils.cloud_run import (
    build_pipeline_args,
    is_cloud_run_configured,
    submit_cloud_run_job as submit_configured_cloud_run_job,
)
from utils.secrets import redact_secrets

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


def _client_ids(req: PipelineRunRequest) -> list[str]:
    client_ids = req.client_ids or []
    if not client_ids and req.client_filter:
        client_ids = [
            value.strip()
            for value in req.client_filter.replace(",", " ").split()
            if value.strip()
        ]
    return client_ids


def _pipeline_args(req: PipelineRunRequest) -> list[str]:
    return build_pipeline_args(
        req.agency_id,
        _client_ids(req),
        dry_run=req.dry_run,
        attribution_model=req.attribution_model,
        run_mode=req.run_mode,
    )


def _submit_cloud_run_job(req: PipelineRunRequest) -> str | None:
    if not is_cloud_run_configured():
        return None
    return submit_configured_cloud_run_job(
        agency_id=req.agency_id,
        client_ids=_client_ids(req),
        dry_run=req.dry_run,
        attribution_model=req.attribution_model,
        run_mode=req.run_mode,
    )


@router.post("/run", response_model=PipelineRunResponse)
async def submit_pipeline_run(
    req: PipelineRunRequest,
    role: Role = Depends(require_auth),
) -> PipelineRunResponse:
    if req.dry_run:
        require_permission(role, Permission.RUN_PIPELINE_DRY)
    else:
        require_permission(role, Permission.RUN_PIPELINE_LIVE)

    local_run_id = str(uuid.uuid4())[:8]

    try:
        cloud_run_operation = _submit_cloud_run_job(req)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to submit Cloud Run job: {redact_secrets(str(exc))}",
        ) from exc

    if cloud_run_operation:
        return PipelineRunResponse(
            run_id=local_run_id,
            cloud_run_operation=cloud_run_operation,
            status="submitted",
            message=f"Cloud Run job submitted: {cloud_run_operation}",
        )

    job_id_str = os.environ.get("ATTRIBUTION_JOB_ID", "")
    if not job_id_str:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "ATTRIBUTION_CLOUD_RUN_JOB/REGION or ATTRIBUTION_JOB_ID "
                "must be configured"
            ),
        )

    try:
        from databricks.sdk import WorkspaceClient

        w = WorkspaceClient()
        run = w.jobs.run_now(job_id=int(job_id_str), python_params=_pipeline_args(req)[1:])
        return PipelineRunResponse(
            run_id=local_run_id,
            databricks_run_id=run.run_id,
            status="submitted",
            message=f"Databricks run {run.run_id} submitted",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to submit Databricks job: {exc}",
        ) from exc


@router.get("/runs", response_model=list[PipelineRunRecord])
async def list_pipeline_runs(
    limit: int = 20,
    role: Role = Depends(require_auth),
) -> list[PipelineRunRecord]:
    require_permission(role, Permission.VIEW_REPORTS)
    from utils.databricks_writer import fetch_recent_pipeline_runs

    rows = fetch_recent_pipeline_runs(limit=limit)
    return [PipelineRunRecord(**r) for r in rows]


@router.get("/runs/{run_id}", response_model=PipelineRunRecord)
async def get_pipeline_run(
    run_id: str,
    role: Role = Depends(require_auth),
) -> PipelineRunRecord:
    require_permission(role, Permission.VIEW_REPORTS)
    from utils.databricks_writer import fetch_recent_pipeline_runs

    rows = fetch_recent_pipeline_runs(limit=200)
    for r in rows:
        if r.get("run_id") == run_id:
            return PipelineRunRecord(**r)
    raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
