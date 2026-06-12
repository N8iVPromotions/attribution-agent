"""
utils/ab_testing.py
--------------------
Deterministic hash-based A/B experiment assignment.

Variant assignment is stable per (experiment_id, client_id) — the same
client always lands in the same bucket. No p-values; descriptive stats only.
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_OPS_SCHEMA = os.environ.get("ATTRIBUTION_OPS_SCHEMA", "workspace.attribution_ops")


class ABTestingManager:
    def assign_variant(self, experiment_id: str, client_id: str) -> str:
        """Return 'A' or 'B' — deterministic for the (experiment_id, client_id) pair."""
        raw = f"{experiment_id}:{client_id}"
        digest = hashlib.sha256(raw.encode()).hexdigest()
        bucket = int(digest[:8], 16) % 100
        experiment = self._get_experiment(experiment_id)
        if not experiment:
            return "A"
        traffic_split = float(experiment.get("traffic_split", 50))
        return "B" if bucket >= traffic_split else "A"

    def record_outcome(
        self,
        experiment_id: str,
        client_id: str,
        run_id: str,
        variant: str,
        outcome: dict,
    ) -> None:
        try:
            import pandas as pd
            from utils.databricks_writer import _upsert_dataframe
            df = pd.DataFrame([{
                "assignment_id": uuid.uuid4().hex,
                "experiment_id": experiment_id,
                "run_id": run_id,
                "client_id": client_id,
                "variant": variant,
                "assigned_at": datetime.now(timezone.utc),
                "outcome_json": json.dumps(outcome, default=str),
            }])
            _upsert_dataframe(df, _OPS_SCHEMA, "ab_assignments", ["assignment_id"])
        except Exception as exc:
            logger.debug(f"[ABTesting] record_outcome failed: {exc}")

    def compute_results(self, experiment_id: str) -> dict:
        """Return descriptive stats (no p-values) per variant."""
        try:
            from utils.databricks_writer import _get_connection, _is_databricks, _get_spark
            query = (
                f"SELECT variant, COUNT(*) AS assignments "
                f"FROM {_OPS_SCHEMA}.ab_assignments "
                f"WHERE experiment_id = '{experiment_id}' "
                f"GROUP BY variant ORDER BY variant"
            )
            if _is_databricks():
                rows = _get_spark().sql(query).collect()
                data = [r.asDict() for r in rows]
            else:
                conn = _get_connection()
                cursor = conn.cursor()
                cursor.execute(query)
                cols = [d[0] for d in cursor.description]
                data = [dict(zip(cols, r)) for r in cursor.fetchall()]
                cursor.close()
                conn.close()
            return {"experiment_id": experiment_id, "variants": data}
        except Exception as exc:
            logger.debug(f"[ABTesting] compute_results failed: {exc}")
            return {"experiment_id": experiment_id, "variants": []}

    def get_active_experiments(self) -> list[dict]:
        try:
            from utils.databricks_writer import _get_connection, _is_databricks, _get_spark
            query = (
                f"SELECT * FROM {_OPS_SCHEMA}.ab_experiments "
                f"WHERE status = 'running'"
            )
            if _is_databricks():
                rows = _get_spark().sql(query).collect()
                return [r.asDict() for r in rows]
            conn = _get_connection()
            cursor = conn.cursor()
            cursor.execute(query)
            cols = [d[0] for d in cursor.description]
            result = [dict(zip(cols, r)) for r in cursor.fetchall()]
            cursor.close()
            conn.close()
            return result
        except Exception:
            return []

    def _get_experiment(self, experiment_id: str) -> dict | None:
        try:
            from utils.databricks_writer import _get_connection, _is_databricks, _get_spark
            query = (
                f"SELECT * FROM {_OPS_SCHEMA}.ab_experiments "
                f"WHERE experiment_id = '{experiment_id}' LIMIT 1"
            )
            if _is_databricks():
                rows = _get_spark().sql(query).collect()
                return rows[0].asDict() if rows else None
            conn = _get_connection()
            cursor = conn.cursor()
            cursor.execute(query)
            cols = [d[0] for d in cursor.description]
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            return dict(zip(cols, row)) if row else None
        except Exception:
            return None
