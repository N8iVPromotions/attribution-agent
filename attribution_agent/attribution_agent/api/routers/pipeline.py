from __future__ import annotations
import os
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from api.auth import require_auth
from api.models import PipelineRunRequest, PipelineRunResponse, PipelineRunRecord
from config.rbac_config import Role, Permission, require_permission

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


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

    job_id_str = os.environ.get("ATTRIBUTION_JOB_ID", "")
    if not job_id_str:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ATTRIBUTION_JOB_ID not configured",
        )

    try:
        from databricks.sdk import WorkspaceClient

        params: list[str] = ["--agency", req.agency_id]
        if req.client_filter:
            params += ["--client-filter", req.client_filter]
        if req.dry_run:
            params.append("--dry-run")
        params += ["--attribution-model", req.attribution_model]

        w = WorkspaceClient()
        run = w.jobs.run_now(job_id=int(job_id_str), python_params=params)
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
