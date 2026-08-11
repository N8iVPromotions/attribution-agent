"""
utils/checkpoint.py
-------------------
Delta-backed pipeline checkpointing.

Tracks which steps have completed for a given (run_id, client_id).
On resume, completed steps are skipped.
"""

from __future__ import annotations
import logging
import os
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_OPS_SCHEMA = os.environ.get("ATTRIBUTION_OPS_SCHEMA", "workspace.attribution_ops")


class Checkpointer:
    def start_step(
        self, run_id: str, agency_id: str, client_id: str, step_name: str
    ) -> str:
        """Record step start. Returns checkpoint_id."""
        checkpoint_id = uuid.uuid4().hex[:16]
        self._write(checkpoint_id, run_id, agency_id, client_id, step_name, "started")
        return checkpoint_id

    def complete_step(
        self,
        run_id: str,
        agency_id: str,
        client_id: str,
        step_name: str,
        result: dict | None = None,
    ) -> None:
        import json

        self._upsert_step(
            run_id,
            client_id,
            step_name,
            "completed",
            result_json=json.dumps(result or {}, default=str),
        )

    def fail_step(
        self,
        run_id: str,
        agency_id: str,
        client_id: str,
        step_name: str,
        error: str = "",
    ) -> None:
        self._upsert_step(run_id, client_id, step_name, "failed", error_detail=error)

    def get_completed_steps(self, run_id: str, client_id: str) -> set[str]:
        """Return names of steps that completed successfully for this run+client."""
        try:
            from utils.databricks_writer import (
                _get_connection,
                _is_databricks,
                _get_spark,
            )

            query = (
                f"SELECT step_name FROM {_OPS_SCHEMA}.pipeline_checkpoints "
                f"WHERE run_id = '{run_id}' AND client_id = '{client_id}' "
                f"AND status = 'completed'"
            )
            if _is_databricks():
                rows = _get_spark().sql(query).collect()
                return {r["step_name"] for r in rows}
            conn = _get_connection()
            cursor = conn.cursor()
            cursor.execute(query)
            steps = {r[0] for r in cursor.fetchall()}
            cursor.close()
            conn.close()
            return steps
        except Exception as exc:
            logger.debug(f"[Checkpoint] get_completed_steps failed: {exc}")
            return set()

    def get_step_result(self, run_id: str, client_id: str, step_name: str) -> dict:
        """Return a completed step's stored result."""
        import json

        try:
            from utils.databricks_writer import (
                _get_connection,
                _get_spark,
                _is_databricks,
            )

            query = (
                f"SELECT result_json FROM {_OPS_SCHEMA}.pipeline_checkpoints "
                f"WHERE run_id = '{run_id}' AND client_id = '{client_id}' "
                f"AND step_name = '{step_name}' AND status = 'completed' "
                "ORDER BY completed_at DESC LIMIT 1"
            )
            if _is_databricks():
                rows = _get_spark().sql(query).collect()
                value = rows[0]["result_json"] if rows else "{}"
            else:
                conn = _get_connection()
                cursor = conn.cursor()
                cursor.execute(query)
                row = cursor.fetchone()
                cursor.close()
                conn.close()
                value = row[0] if row else "{}"
            if not value:
                raise RuntimeError(
                    f"No completed checkpoint result for {client_id}/{step_name}"
                )
            result = json.loads(value)
            if not isinstance(result, dict):
                raise TypeError("Checkpoint result must be a JSON object")
            return result
        except Exception as exc:
            logger.error(f"[Checkpoint] get_step_result failed: {exc}")
            raise RuntimeError(
                f"Unable to restore checkpoint result for {client_id}/{step_name}"
            ) from exc

    def _write(
        self,
        checkpoint_id: str,
        run_id: str,
        agency_id: str,
        client_id: str,
        step_name: str,
        status: str,
        result_json: str = "{}",
        error_detail: str = "",
    ) -> None:
        try:
            import pandas as pd
            from utils.databricks_writer import _upsert_dataframe

            now = datetime.now(timezone.utc)
            df = pd.DataFrame(
                [
                    {
                        "checkpoint_id": checkpoint_id,
                        "run_id": run_id,
                        "agency_id": agency_id,
                        "client_id": client_id,
                        "step_name": step_name,
                        "status": status,
                        "started_at": now,
                        "completed_at": now
                        if status in ("completed", "failed")
                        else None,
                        "result_json": result_json,
                        "error_detail": error_detail,
                    }
                ]
            )
            _upsert_dataframe(
                df, _OPS_SCHEMA, "pipeline_checkpoints", ["checkpoint_id"]
            )
        except Exception as exc:
            logger.debug(f"[Checkpoint] write failed for {step_name}: {exc}")

    def _upsert_step(
        self,
        run_id: str,
        client_id: str,
        step_name: str,
        status: str,
        result_json: str = "{}",
        error_detail: str = "",
    ) -> None:
        try:
            from utils.databricks_writer import _run_sql

            now = datetime.now(timezone.utc).isoformat()
            _run_sql(
                f"UPDATE {_OPS_SCHEMA}.pipeline_checkpoints "
                f"SET status = '{status}', "
                f"    completed_at = CAST('{now}' AS TIMESTAMP), "
                f"    result_json = '{result_json.replace(chr(39), '')}', "
                f"    error_detail = '{error_detail.replace(chr(39), '')}' "
                f"WHERE run_id = '{run_id}' AND client_id = '{client_id}' "
                f"AND step_name = '{step_name}' AND status = 'started'"
            )
        except Exception as exc:
            logger.debug(f"[Checkpoint] upsert_step failed: {exc}")
