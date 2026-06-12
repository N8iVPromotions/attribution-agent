from __future__ import annotations
import os
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from api.auth import require_auth, require_admin
from api.models import ApprovalItem, ApprovalResolution
from config.rbac_config import Role, Permission, require_permission

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


def _resolve_approval(action_id: str, resolution: str, resolved_by: str, note: str) -> bool:
    from utils.databricks_writer import _run_sql
    now = datetime.now(timezone.utc).isoformat()
    try:
        _run_sql(
            f"UPDATE {_OPS_SCHEMA}.approval_queue "
            f"SET status = '{resolution}', "
            f"    resolved_at = CAST('{now}' AS TIMESTAMP), "
            f"    resolved_by = '{resolved_by}', "
            f"    resolution_note = '{note.replace(chr(39), '')}' "
            f"WHERE action_id = '{action_id}' AND status = 'pending'"
        )
        return True
    except Exception:
        return False


@router.get("", response_model=list[ApprovalItem])
async def list_pending_approvals(role: Role = Depends(require_auth)) -> list[ApprovalItem]:
    require_permission(role, Permission.APPROVE_ACTIONS)
    rows = _fetch_pending()
    return [ApprovalItem(**r) for r in rows]


@router.post("/{action_id}/resolve", response_model=dict)
async def resolve_approval(
    action_id: str,
    body: ApprovalResolution,
    role: Role = Depends(require_admin),
) -> dict:
    require_permission(role, Permission.APPROVE_ACTIONS)
    ok = _resolve_approval(action_id, body.resolution, actor="api", note=body.resolution_note)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Pending approval '{action_id}' not found")
    from utils.audit_logger import log_event
    log_event(
        "APPROVAL_RESOLVED",
        actor="api",
        action=body.resolution,
        resource=action_id,
        detail={"note": body.resolution_note},
    )
    return {"action_id": action_id, "status": body.resolution}
