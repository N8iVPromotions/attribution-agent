"""
evals/golden_dataset.py
-----------------------
Manages golden eval samples for behavioral regression testing.

Samples are promoted from real pipeline runs and stored in
workspace.attribution_ops.eval_golden_dataset Delta table.
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


class GoldenDatasetManager:
    def promote_sample(
        self,
        agent_name: str,
        input_text: str,
        expected_output: str,
        expected_fields: dict | None = None,
        source: str = "manual",
        run_id: str = "",
    ) -> str:
        """Promote a real run output to a golden sample. Returns sample_id."""
        sample_id = uuid.uuid4().hex
        input_hash = hashlib.sha256(input_text.encode()).hexdigest()[:32]
        try:
            import pandas as pd
            from utils.databricks_writer import _upsert_dataframe

            df = pd.DataFrame(
                [
                    {
                        "sample_id": sample_id,
                        "created_at": datetime.now(timezone.utc),
                        "agent_name": agent_name,
                        "input_hash": input_hash,
                        "input_summary": input_text[:500],
                        "expected_output": expected_output,
                        "expected_fields": json.dumps(expected_fields or {}),
                        "tolerance_json": json.dumps({}),
                        "source": source,
                        "run_id": run_id,
                        "is_active": True,
                    }
                ]
            )
            _upsert_dataframe(df, _OPS_SCHEMA, "eval_golden_dataset", ["sample_id"])
            logger.info(f"[GoldenDataset] Promoted sample {sample_id} for {agent_name}")
        except Exception as exc:
            logger.warning(f"[GoldenDataset] promote_sample failed: {exc}")
        return sample_id

    def load_samples(self, agent_name: str) -> list[dict]:
        try:
            from utils.databricks_writer import (
                _get_connection,
                _is_databricks,
                _get_spark,
            )

            query = (
                f"SELECT * FROM {_OPS_SCHEMA}.eval_golden_dataset "
                f"WHERE agent_name = '{agent_name}' AND is_active = true "
                f"ORDER BY created_at DESC"
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
        except Exception as exc:
            logger.debug(f"[GoldenDataset] load_samples failed: {exc}")
            return []

    def evaluate(self, agent_name: str, actual_output: str, sample: dict) -> dict:
        """Compare actual output against a golden sample. Returns field-level results."""
        expected_fields = {}
        try:
            expected_fields = json.loads(sample.get("expected_fields", "{}"))
        except Exception:
            pass

        if not expected_fields:
            return {"passed": True, "score": 1.0, "field_results": {}}

        field_results = {}
        passed_count = 0

        try:
            actual = (
                json.loads(actual_output)
                if actual_output.strip().startswith("{")
                else {}
            )
        except Exception:
            actual = {}

        for field, expected_val in expected_fields.items():
            actual_val = actual.get(field)
            passed = actual_val is not None
            if isinstance(expected_val, (int, float)) and isinstance(
                actual_val, (int, float)
            ):
                tolerance = float(sample.get("tolerance_json", "{}") or "{}").get(
                    field, 0.1
                )
                try:
                    tol_dict = json.loads(sample.get("tolerance_json", "{}") or "{}")
                    tolerance = tol_dict.get(field, 0.1)
                except Exception:
                    tolerance = 0.1
                if expected_val != 0:
                    passed = (
                        abs(actual_val - expected_val) / abs(expected_val) <= tolerance
                    )
                else:
                    passed = actual_val == 0
            field_results[field] = {
                "passed": passed,
                "expected": expected_val,
                "actual": actual_val,
            }
            if passed:
                passed_count += 1

        score = passed_count / len(expected_fields) if expected_fields else 1.0
        return {"passed": score >= 0.80, "score": score, "field_results": field_results}
