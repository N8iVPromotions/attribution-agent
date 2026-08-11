"""
utils/observability_queries.py
-------------------------------
SQL helper functions for operator observability surfaces.
Each function returns a list[dict] and swallows DB errors gracefully.
"""

from __future__ import annotations
import logging
import os

logger = logging.getLogger(__name__)
_OPS_SCHEMA = os.environ.get("ATTRIBUTION_OPS_SCHEMA", "workspace.attribution_ops")


def _query(sql: str) -> list[dict]:
    try:
        from utils.databricks_writer import _get_connection, _is_databricks, _get_spark

        if _is_databricks():
            rows = _get_spark().sql(sql).collect()
            return [r.asDict() for r in rows]
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        cols = [d[0] for d in cursor.description]
        result = [dict(zip(cols, r)) for r in cursor.fetchall()]
        cursor.close()
        conn.close()
        return result
    except Exception as exc:
        logger.debug(f"[Observability] query failed: {exc}")
        return []


def get_monthly_cost_by_agency() -> list[dict]:
    """Token spend by agency for the current calendar month."""
    return _query(f"""
        SELECT agency_id,
               SUM(input_tokens + output_tokens) AS total_tokens,
               SUM(cost_usd_estimate) AS cost_usd
        FROM {_OPS_SCHEMA}.cost_ledger
        WHERE date_trunc('month', event_time) = date_trunc('month', current_timestamp())
        GROUP BY agency_id
        ORDER BY cost_usd DESC
    """)


def get_pipeline_health_last_30d() -> list[dict]:
    """Daily success/failure counts for the past 30 days."""
    return _query(f"""
        SELECT date_trunc('day', started_at) AS day,
               COUNT(*) AS total_runs,
               SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
               SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failures
        FROM {_OPS_SCHEMA}.pipeline_runs
        WHERE started_at >= date_sub(current_timestamp(), 30)
        GROUP BY 1 ORDER BY 1 DESC
    """)


def get_latest_eval_scores() -> list[dict]:
    """Most recent eval score per agent."""
    return _query(f"""
        SELECT agent_name,
               score,
               passed,
               regression,
               run_at
        FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY agent_name ORDER BY run_at DESC) AS rn
            FROM {_OPS_SCHEMA}.eval_results
        ) t WHERE rn = 1
        ORDER BY agent_name
    """)


def get_recent_audit_events(limit: int = 20) -> list[dict]:
    """Most recent audit log events."""
    return _query(f"""
        SELECT event_time, event_type, actor, client_id, resource, action, outcome
        FROM {_OPS_SCHEMA}.audit_log
        ORDER BY event_time DESC
        LIMIT {int(limit)}
    """)


def get_memory_snapshot(client_id: str, limit: int = 5) -> list[dict]:
    """Most recent agent memories for a client."""
    return _query(f"""
        SELECT memory_type, content, importance, created_at
        FROM {_OPS_SCHEMA}.agent_memory
        WHERE client_id = '{client_id}'
        ORDER BY created_at DESC
        LIMIT {int(limit)}
    """)
