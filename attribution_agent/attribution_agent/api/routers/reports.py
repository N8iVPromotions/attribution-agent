from __future__ import annotations
import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from api.auth import (
    AuthPrincipal,
    require_auth,
    require_client_access,
    require_permission,
)
from api.models import InsightReportResponse
from config.rbac_config import Permission
from utils.sql import sql_literal

router = APIRouter(prefix="/reports", tags=["reports"])


def _row_to_response(row: dict) -> InsightReportResponse:
    key_findings = row.get("key_findings", "[]")
    if isinstance(key_findings, str):
        try:
            key_findings = json.loads(key_findings)
        except Exception:
            key_findings = [key_findings] if key_findings else []
    return InsightReportResponse(
        report_id=row.get("report_id", ""),
        client_id=row.get("client_id", ""),
        agency_id=row.get("agency_id", ""),
        report_month=row.get("report_month", ""),
        narrative=row.get("narrative", ""),
        key_findings=key_findings,
        top_channel=row.get("top_channel", ""),
        total_pipeline=float(row.get("total_pipeline") or 0),
        total_spend=float(row.get("total_spend") or 0),
        overall_roi=float(row.get("overall_roi") or 0),
        collected_revenue=float(row.get("collected_revenue") or 0),
        refund_rate=float(row.get("refund_rate") or 0),
        true_roi=float(row.get("true_roi") or 0),
        attribution_model=row.get("attribution_model", ""),
        generated_at=row.get("generated_at"),
        run_id=row.get("run_id", ""),
        prompt_version=row.get("prompt_version", ""),
        model_id=row.get("model_id", ""),
    )


def _fetch_reports(client_id: str, limit: int = 1) -> list[dict]:
    from utils.databricks_writer import _get_connection, _is_databricks, _get_spark

    _OPS_SCHEMA = __import__("os").environ.get(
        "ATTRIBUTION_OPS_SCHEMA", "workspace.attribution_ops"
    )
    query = (
        f"SELECT * FROM {_OPS_SCHEMA}.insight_reports "
        f"WHERE client_id = {sql_literal(client_id)} "
        f"ORDER BY generated_at DESC LIMIT {int(limit)}"
    )
    try:
        if _is_databricks():
            spark_df = _get_spark().sql(query)
            return [row.asDict() for row in spark_df.collect()]
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(query)
        cols = [d[0] for d in cursor.description]
        rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        cursor.close()
        conn.close()
        return rows
    except Exception:
        return []


@router.get("/{client_id}/latest", response_model=InsightReportResponse)
async def get_latest_report(
    client_id: str,
    principal: AuthPrincipal = Depends(require_auth),
) -> InsightReportResponse:
    require_permission(principal, Permission.VIEW_REPORTS)
    require_client_access(principal, client_id)
    rows = _fetch_reports(client_id, limit=1)
    if not rows:
        raise HTTPException(
            status_code=404, detail=f"No reports found for client '{client_id}'"
        )
    return _row_to_response(rows[0])


@router.get("/{client_id}", response_model=list[InsightReportResponse])
async def list_reports(
    client_id: str,
    limit: int = 10,
    principal: AuthPrincipal = Depends(require_auth),
) -> list[InsightReportResponse]:
    require_permission(principal, Permission.VIEW_REPORTS)
    require_client_access(principal, client_id)
    rows = _fetch_reports(client_id, limit=min(limit, 50))
    return [_row_to_response(r) for r in rows]


@router.post("/{client_id}/generate", status_code=202)
async def trigger_report_generation(
    client_id: str,
    background_tasks: BackgroundTasks,
    principal: AuthPrincipal = Depends(require_auth),
) -> dict:
    require_permission(principal, Permission.RUN_PIPELINE_DRY)
    require_client_access(principal, client_id)
    from config.client_config import CLIENT_REGISTRY, reload_client_registry

    reload_client_registry()
    if client_id not in CLIENT_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Client '{client_id}' not found")

    run_id = str(uuid.uuid4())[:8]

    def _generate():
        try:
            from agents.insight.insight_agent import InsightAgent
            from config.client_config import get_client

            cfg = get_client(client_id)
            agent = InsightAgent(cfg)
            agent.generate_and_store()
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                f"[ReportAPI] Background generation failed for {client_id}: {exc}"
            )

    background_tasks.add_task(_generate)
    return {
        "run_id": run_id,
        "status": "accepted",
        "message": "Report generation started",
    }
