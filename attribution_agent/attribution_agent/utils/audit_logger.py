"""
utils/audit_logger.py
----------------------
Fire-and-forget audit event logger.

Writes to workspace.attribution_ops.audit_log via Databricks.
All writes are dispatched on a background thread so they never block
the pipeline. Falls back to logging.warning if Databricks is unreachable.

Usage:
    from utils.audit_logger import log_event
    log_event("PIPELINE_START", actor="system", client_id="demo_client", ...)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_OPS_SCHEMA = os.environ.get("ATTRIBUTION_OPS_SCHEMA", "workspace.attribution_ops")

AUDIT_LOG_DDL = f"""
CREATE TABLE IF NOT EXISTS {_OPS_SCHEMA}.audit_log (
    event_id        STRING,
    event_time      TIMESTAMP,
    event_type      STRING,
    actor           STRING,
    client_id       STRING,
    agency_id       STRING,
    resource        STRING,
    action          STRING,
    outcome         STRING,
    detail_json     STRING,
    run_id          STRING,
    ip_address      STRING,
    session_id      STRING
)
USING DELTA
PARTITIONED BY (event_type)
TBLPROPERTIES ('delta.logRetentionDuration' = 'interval 365 days')
"""

# Valid event types
EVENT_TYPES = {
    "PIPELINE_START",
    "PIPELINE_END",
    "AGENT_CALL",
    "EMAIL_SENT",
    "CONFIG_CHANGE",
    "AUTH_FAILURE",
    "PII_MASKED",
    "APPROVAL_REQUESTED",
    "APPROVAL_RESOLVED",
    "GUARDRAIL_BLOCK",
    "SAGA_COMPENSATION_FAILED",
    "REPORT_GENERATED",
    "CLIENT_CREATED",
    "CLIENT_UPDATED",
    "CLIENT_DELETED",
    "API_REQUEST",
}

_table_ensured = False
_table_ensure_lock = threading.Lock()


def _ensure_table() -> None:
    global _table_ensured
    if _table_ensured:
        return
    with _table_ensure_lock:
        if _table_ensured:
            return
        try:
            from utils.databricks_writer import _run_sql

            _run_sql(AUDIT_LOG_DDL)
            _table_ensured = True
        except Exception as exc:
            logger.debug(f"[AuditLog] Could not ensure table: {exc}")


def _write_event(row: dict) -> None:
    try:
        _ensure_table()
        import pandas as pd
        from utils.databricks_writer import _upsert_dataframe, _OPS_SCHEMA as ops_schema

        df = pd.DataFrame([row])
        _upsert_dataframe(df, ops_schema, "audit_log", ["event_id"])
    except Exception as exc:
        logger.warning(
            f"[AuditLog] Failed to write event {row.get('event_type')} "
            f"for {row.get('client_id')}: {exc}"
        )


def log_event(
    event_type: str,
    actor: str = "system",
    client_id: str = "",
    agency_id: str = "",
    resource: str = "",
    action: str = "",
    outcome: str = "success",
    detail: dict | str | None = None,
    run_id: str = "",
    ip_address: str = "",
    session_id: str = "",
) -> None:
    """
    Asynchronously log an audit event.

    Never raises — failures are swallowed to logging.warning.
    """
    detail_json = ""
    if detail is not None:
        try:
            detail_json = (
                json.dumps(detail, default=str)
                if isinstance(detail, dict)
                else str(detail)
            )
        except Exception:
            detail_json = str(detail)

    row = {
        "event_id": str(uuid.uuid4()),
        "event_time": datetime.now(timezone.utc),
        "event_type": event_type,
        "actor": actor or "system",
        "client_id": client_id or "",
        "agency_id": agency_id or "",
        "resource": resource or "",
        "action": action or "",
        "outcome": outcome or "success",
        "detail_json": detail_json,
        "run_id": run_id or "",
        "ip_address": ip_address or "",
        "session_id": session_id or "",
    }

    t = threading.Thread(target=_write_event, args=(row,), daemon=True)
    t.start()
