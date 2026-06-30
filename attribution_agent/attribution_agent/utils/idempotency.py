"""
utils/idempotency.py
--------------------
Delta-backed idempotency store.

Prevents double-writes when a pipeline step is retried. Keys are
`{run_id}:{client_id}:{step_name}`. TTL defaults to 7 days.
"""

from __future__ import annotations
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_OPS_SCHEMA = os.environ.get("ATTRIBUTION_OPS_SCHEMA", "workspace.attribution_ops")


def _make_key(run_id: str, client_id: str, step_name: str) -> str:
    raw = f"{run_id}:{client_id}:{step_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class IdempotencyStore:
    def check(self, run_id: str, client_id: str, step_name: str) -> dict | None:
        """Return the previously stored result, or None if step hasn't run."""
        key = _make_key(run_id, client_id, step_name)
        try:
            from utils.databricks_writer import (
                _get_connection,
                _is_databricks,
                _get_spark,
            )

            now = datetime.now(timezone.utc).isoformat()
            query = (
                f"SELECT result_json FROM {_OPS_SCHEMA}.idempotency_store "
                f"WHERE key = '{key}' AND expires_at > CAST('{now}' AS TIMESTAMP) LIMIT 1"
            )
            if _is_databricks():
                rows = _get_spark().sql(query).collect()
                if rows:
                    return json.loads(rows[0]["result_json"])
            else:
                conn = _get_connection()
                cursor = conn.cursor()
                cursor.execute(query)
                row = cursor.fetchone()
                cursor.close()
                conn.close()
                if row:
                    return json.loads(row[0])
        except Exception as exc:
            logger.debug(f"[Idempotency] check failed for {step_name}: {exc}")
        return None

    def record(
        self,
        run_id: str,
        client_id: str,
        step_name: str,
        result: dict,
        ttl_days: int = 7,
    ) -> None:
        """Persist a step result so retries can skip it."""
        key = _make_key(run_id, client_id, step_name)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=ttl_days)
        try:
            import pandas as pd
            from utils.databricks_writer import _upsert_dataframe

            df = pd.DataFrame(
                [
                    {
                        "key": key,
                        "created_at": now,
                        "expires_at": expires_at,
                        "result_json": json.dumps(result, default=str),
                        "step_name": step_name,
                        "run_id": run_id,
                        "client_id": client_id,
                    }
                ]
            )
            _upsert_dataframe(df, _OPS_SCHEMA, "idempotency_store", ["key"])
        except Exception as exc:
            logger.debug(f"[Idempotency] record failed for {step_name}: {exc}")
