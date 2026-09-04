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
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_OPS_SCHEMA = os.environ.get("ATTRIBUTION_OPS_SCHEMA", "workspace.attribution_ops")
_LOCAL_DELIVERY_KEYS: dict[str, str] = {}
_LOCAL_DELIVERY_LOCK = threading.Lock()


def report_delivery_key(
    client_id: str,
    report_month: str,
    attribution_model: str,
    *,
    report_id: str = "",
    delivery_config_fingerprint: str = "",
) -> str:
    """Return a stable key for one exact report artifact and delivery envelope."""
    base = f"report:{client_id}:{report_month}:{attribution_model}"
    if report_id or delivery_config_fingerprint:
        if not report_id or not delivery_config_fingerprint:
            raise ValueError(
                "report_id and delivery_config_fingerprint must be supplied together"
            )
        return f"{base}:{report_id}:{delivery_config_fingerprint}"
    return base


@dataclass
class DeliveryClaim:
    """At-most-once delivery claim created before the email provider is called."""

    key: str
    acquired: bool
    blob: object | None = None
    generation: int | None = None
    state: str | None = None

    def complete(self) -> None:
        if not self.acquired:
            return
        if self.blob is None:
            with _LOCAL_DELIVERY_LOCK:
                _LOCAL_DELIVERY_KEYS[self.key] = "sent"
            self.state = "sent"
            return
        payload = {
            "key": self.key,
            "status": "sent",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        self.blob.upload_from_string(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            content_type="application/json",
            if_generation_match=self.generation,
        )
        self.generation = int(self.blob.generation)
        self.state = "sent"

    def release(self) -> None:
        """Release only when the provider definitively failed before delivery."""
        if not self.acquired or self.state == "sent":
            return
        if self.blob is None:
            with _LOCAL_DELIVERY_LOCK:
                _LOCAL_DELIVERY_KEYS.pop(self.key, None)
            return
        self.blob.delete(if_generation_match=self.generation)


def claim_report_delivery(key: str) -> DeliveryClaim:
    """Atomically claim a report delivery key using GCS generation zero.

    Cloud deployments set ``ARIE_DELIVERY_IDEMPOTENCY_BUCKET``. The in-process
    fallback preserves the dependency-free SQLite demo path.
    """
    bucket_name = os.environ.get("ARIE_DELIVERY_IDEMPOTENCY_BUCKET", "").strip()
    if not bucket_name:
        with _LOCAL_DELIVERY_LOCK:
            acquired = key not in _LOCAL_DELIVERY_KEYS
            if acquired:
                _LOCAL_DELIVERY_KEYS[key] = "claimed"
            state = _LOCAL_DELIVERY_KEYS.get(key)
        return DeliveryClaim(key=key, acquired=acquired, state=state)

    from google.api_core.exceptions import PreconditionFailed
    from google.cloud import storage

    object_hash = hashlib.sha256(key.encode()).hexdigest()
    blob = (
        storage.Client().bucket(bucket_name).blob(f"report-delivery/{object_hash}.json")
    )
    payload = {
        "key": key,
        "status": "claimed",
        "claimed_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        blob.upload_from_string(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            content_type="application/json",
            if_generation_match=0,
        )
    except PreconditionFailed:
        state = None
        try:
            existing = json.loads(blob.download_as_text())
            if isinstance(existing, dict) and existing.get("key") == key:
                candidate = str(existing.get("status") or "").lower()
                if candidate in {"claimed", "sent"}:
                    state = candidate
        except Exception as exc:
            logger.warning(
                "[Idempotency] Existing delivery claim could not be verified: %s",
                exc,
            )
        return DeliveryClaim(key=key, acquired=False, blob=blob, state=state)
    return DeliveryClaim(
        key=key,
        acquired=True,
        blob=blob,
        generation=int(blob.generation),
        state="claimed",
    )


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
