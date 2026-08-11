"""Immutable GCS archival for vendor response pages."""

from __future__ import annotations

import gzip
import hashlib
import os
from datetime import datetime, timezone


def _safe_segment(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value)


def archive_raw_page(
    *,
    source: str,
    client_id: str,
    run_id: str,
    page_number: int,
    body: bytes,
    endpoint: str = "data",
) -> str:
    """Archive one provider page before parsing; no-op when no bucket is set."""
    bucket_name = os.environ.get("ARIE_RAW_ARCHIVE_BUCKET", "").strip()
    if not bucket_name:
        return ""

    from google.api_core.exceptions import PreconditionFailed
    from google.cloud import storage

    now = datetime.now(timezone.utc)
    digest = hashlib.sha256(body).hexdigest()
    object_name = (
        f"raw/source={_safe_segment(source)}/"
        f"client_id={_safe_segment(client_id or 'unknown')}/"
        f"ingest_date={now.date().isoformat()}/"
        f"run_id={_safe_segment(run_id or 'adhoc')}/"
        f"{_safe_segment(endpoint)}-page-{page_number:06d}-{digest[:12]}.json.gz"
    )
    blob = storage.Client().bucket(bucket_name).blob(object_name)
    blob.metadata = {
        "sha256": digest,
        "source": source,
        "client_id": client_id,
        "run_id": run_id,
        "archived_at": now.isoformat(),
    }
    try:
        blob.upload_from_string(
            gzip.compress(body),
            content_type="application/gzip",
            if_generation_match=0,
        )
    except PreconditionFailed:
        pass
    return f"gs://{bucket_name}/{object_name}"
