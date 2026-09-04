"""Immutable Cloud Run work manifests stored in GCS."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from attribution_models import normalize_model
from utils.report_period import resolve_report_period

_WAREHOUSE_ATTRIBUTION_MODEL = "last_touch"


def validate_warehouse_attribution_model(attribution_model: str) -> str:
    selected_model = normalize_model(attribution_model)
    if selected_model != _WAREHOUSE_ATTRIBUTION_MODEL:
        raise ValueError(
            "Production attribution supports only last_touch until observed "
            "multi-touch evidence is available."
        )
    return selected_model


def _validate_items(work_items: list[tuple[str, str]]) -> None:
    if not work_items:
        raise ValueError("Work manifest cannot be empty")
    client_ids = [client_id for _, client_id in work_items]
    if len(client_ids) != len(set(client_ids)):
        raise ValueError("Work manifest contains duplicate client_id assignments")


def create_work_manifest(
    work_items: list[tuple[str, str]],
    *,
    run_id: str | None = None,
    dry_run: bool = False,
    attribution_model: str = "last_touch",
    report_month: str | None = None,
    bucket_name: str | None = None,
) -> tuple[str, dict]:
    """Create an immutable manifest and return its gs:// URI plus payload."""
    from google.cloud import storage

    _validate_items(work_items)
    selected_model = validate_warehouse_attribution_model(attribution_model)
    bucket = bucket_name or os.environ.get("ARIE_WORK_MANIFEST_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError("ARIE_WORK_MANIFEST_BUCKET is required")
    execution_run_id = run_id or uuid.uuid4().hex
    period = resolve_report_period(report_month)
    payload = {
        "version": 2,
        "run_id": execution_run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "attribution_model": selected_model,
        "report_month": period.month,
        "report_timezone": period.timezone_name,
        "period_start": period.start.isoformat(),
        "period_end": period.end.isoformat(),
        "work_items": [
            {"task_index": index, "agency_id": agency_id, "client_id": client_id}
            for index, (agency_id, client_id) in enumerate(work_items)
        ],
    }
    object_name = f"work-manifests/run_id={execution_run_id}/manifest.json"
    blob = storage.Client().bucket(bucket).blob(object_name)
    blob.upload_from_string(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        content_type="application/json",
        if_generation_match=0,
    )
    return f"gs://{bucket}/{object_name}", payload


def load_work_manifest(uri: str) -> dict:
    from google.cloud import storage

    if not uri.startswith("gs://"):
        raise ValueError("Work manifest URI must use gs://")
    bucket_name, _, object_name = uri[5:].partition("/")
    if not bucket_name or not object_name:
        raise ValueError("Invalid work manifest URI")
    payload = json.loads(
        storage.Client().bucket(bucket_name).blob(object_name).download_as_text()
    )
    if payload.get("version") != 2:
        raise ValueError("Work manifest version 2 is required")
    payload["attribution_model"] = validate_warehouse_attribution_model(
        str(payload.get("attribution_model") or "")
    )
    report_month = payload.get("report_month")
    if not all(
        payload.get(field)
        for field in ("report_month", "report_timezone", "period_start", "period_end")
    ):
        raise ValueError("Work manifest requires a complete report period")
    period = resolve_report_period(
        report_month,
        timezone_name=payload["report_timezone"],
    )
    if (
        payload["period_start"] != period.start.isoformat()
        or payload["period_end"] != period.end.isoformat()
    ):
        raise ValueError("Work manifest report-period boundaries are inconsistent")
    work_items = payload.get("work_items", [])
    indexes = [item.get("task_index") for item in work_items]
    if indexes != list(range(len(work_items))):
        raise ValueError("Work manifest task indexes are not contiguous")
    _validate_items([(item["agency_id"], item["client_id"]) for item in work_items])
    return payload
