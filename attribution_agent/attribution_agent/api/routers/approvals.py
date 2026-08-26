from __future__ import annotations
import os
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from api.auth import AuthPrincipal, require_admin, require_permission
from api.models import ApprovalItem, ApprovalResolution
from config.rbac_config import Permission
from utils.sql import sql_literal

router = APIRouter(prefix="/approvals", tags=["approvals"])

_OPS_SCHEMA = os.environ.get("ATTRIBUTION_OPS_SCHEMA", "workspace.attribution_ops")


def _fetch_pending() -> list[dict]:
    from utils.databricks_writer import _get_connection, _is_databricks, _get_spark

    query = (
        f"SELECT action_id, created_at, actor, description, action_type, status, channel "
        f"FROM {_OPS_SCHEMA}.approval_queue "
        f"WHERE status = 'pending' ORDER BY created_at DESC LIMIT 50"
    )
    try:
        if _is_databricks():
            spark_df = _get_spark().sql(query)
            return [r.asDict() for r in spark_df.collect()]
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


def _resolve_approval(
    action_id: str, resolution: str, resolved_by: str, note: str
) -> bool:
    from utils.databricks_writer import _run_sql

    now = datetime.now(timezone.utc).isoformat()
    try:
        _run_sql(
            f"UPDATE {_OPS_SCHEMA}.approval_queue "
            f"SET status = {sql_literal(resolution)}, "
            f"    resolved_at = CAST({sql_literal(now)} AS TIMESTAMP), "
            f"    resolved_by = {sql_literal(resolved_by)}, "
            f"    resolution_note = {sql_literal(note)} "
            f"WHERE action_id = {sql_literal(action_id)} AND status = 'pending'"
        )
        return True
    except Exception:
        return False


def _fetch_approval_status(action_id: str) -> str | None:
    from utils.databricks_writer import _get_connection, _is_databricks, _get_spark

    query = (
        f"SELECT status FROM {_OPS_SCHEMA}.approval_queue "
        f"WHERE action_id = {sql_literal(action_id)} LIMIT 1"
    )
    if _is_databricks():
        rows = _get_spark().sql(query).collect()
        return str(rows[0]["status"]) if rows else None
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(query)
            row = cursor.fetchone()
            return str(row[0]) if row else None
        finally:
            cursor.close()
    finally:
        conn.close()


@router.get("", response_model=list[ApprovalItem])
async def list_pending_approvals(
    principal: AuthPrincipal = Depends(require_admin),
) -> list[ApprovalItem]:
    require_permission(principal, Permission.APPROVE_ACTIONS)
    rows = _fetch_pending()
    return [ApprovalItem(**r) for r in rows]


@router.post("/{action_id}/resolve", response_model=dict)
async def resolve_approval(
    action_id: str,
    body: ApprovalResolution,
    principal: AuthPrincipal = Depends(require_admin),
) -> dict:
    require_permission(principal, Permission.APPROVE_ACTIONS)
    current_status = _fetch_approval_status(action_id)
    if current_status is None:
        raise HTTPException(status_code=404, detail=f"Approval '{action_id}' not found")
    if current_status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Approval '{action_id}' is already {current_status}",
        )
    ok = _resolve_approval(
        action_id, body.resolution, resolved_by="api", note=body.resolution_note
    )
    if not ok:
        raise HTTPException(
            status_code=502, detail=f"Could not resolve approval '{action_id}'"
        )
    final_status = _fetch_approval_status(action_id)
    if final_status != body.resolution:
        raise HTTPException(
            status_code=409,
            detail=f"Approval '{action_id}' changed concurrently",
        )
    from utils.audit_logger import log_event

    log_event(
        "APPROVAL_RESOLVED",
        actor="api",
        action=body.resolution,
        resource=action_id,
        detail={"note": body.resolution_note},
    )
    return {"action_id": action_id, "status": body.resolution}
