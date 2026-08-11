"""Validate staging task telemetry, GCS leases, and raw-page archives."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path

_root = str(Path(__file__).parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from flows.agency_flow import (  # noqa: E402
    _partial_suppression_alert,
    build_client_work_items,
)
from utils.client_lock import ClientLease, ClientLeaseHeld  # noqa: E402
from utils.raw_archive import archive_raw_page  # noqa: E402
from utils.work_manifest import load_work_manifest  # noqa: E402

_ASSIGNMENT_RE = re.compile(
    r"\[Job\] Task (?P<index>\d+)/(?P<count>\d+) assigned to "
    r"agency=(?P<agency>\S+) client=(?P<client>\S+)"
)


def verify_task_mapping(work_items: list[tuple[str, str]], expected_count: int) -> dict:
    if len(work_items) != expected_count:
        raise AssertionError(
            f"Expected {expected_count} work items, found {len(work_items)}"
        )
    client_ids = [client_id for _, client_id in work_items]
    if len(set(client_ids)) != expected_count:
        raise AssertionError("Task mapping contains duplicate client IDs")
    return {
        "task_count": expected_count,
        "assignments": [
            {"task_index": index, "agency_id": agency, "client_id": client}
            for index, (agency, client) in enumerate(work_items)
        ],
    }


def verify_gcs_lock(bucket_name: str) -> dict:
    probe_id = uuid.uuid4().hex
    client_id = f"staging-lock-probe-{probe_id}"
    run_id = f"staging-{probe_id}"
    previous_bucket = os.environ.get("ARIE_CLIENT_LOCK_BUCKET")
    previous_enabled = os.environ.get("ARIE_CLIENT_LOCKS_ENABLED")
    os.environ["ARIE_CLIENT_LOCK_BUCKET"] = bucket_name
    os.environ["ARIE_CLIENT_LOCKS_ENABLED"] = "true"
    lease = ClientLease(client_id, run_id, ttl_seconds=60)
    blob = None
    try:
        with lease:
            blob = lease._blob
            if blob is None or lease.generation is None or not blob.exists():
                raise AssertionError("Lock object was not created")
            payload = json.loads(blob.download_as_text())
            if payload.get("run_id") != run_id:
                raise AssertionError("Lock payload run_id mismatch")
            try:
                ClientLease(client_id, f"duplicate-{run_id}", ttl_seconds=60).acquire()
            except ClientLeaseHeld:
                duplicate_blocked = True
            else:
                duplicate_blocked = False
            if not duplicate_blocked:
                raise AssertionError("Generation-zero lock did not block a duplicate")
        if blob.exists():
            raise AssertionError("Lock object still exists after release")
        return {
            "acquired_generation": lease.generation,
            "duplicate_blocked": True,
            "released": True,
        }
    finally:
        if previous_bucket is None:
            os.environ.pop("ARIE_CLIENT_LOCK_BUCKET", None)
        else:
            os.environ["ARIE_CLIENT_LOCK_BUCKET"] = previous_bucket
        if previous_enabled is None:
            os.environ.pop("ARIE_CLIENT_LOCKS_ENABLED", None)
        else:
            os.environ["ARIE_CLIENT_LOCKS_ENABLED"] = previous_enabled


def verify_raw_archive(bucket_name: str) -> dict:
    from google.cloud import storage

    run_id = f"staging-{uuid.uuid4().hex}"
    body = json.dumps(
        {"probe": "raw-archive", "run_id": run_id, "rows": [{"id": 1}]},
        sort_keys=True,
    ).encode()
    previous_bucket = os.environ.get("ARIE_RAW_ARCHIVE_BUCKET")
    os.environ["ARIE_RAW_ARCHIVE_BUCKET"] = bucket_name
    try:
        uri = archive_raw_page(
            source="staging-probe",
            client_id="staging-probe",
            run_id=run_id,
            page_number=1,
            endpoint="checksum",
            body=body,
        )
    finally:
        if previous_bucket is None:
            os.environ.pop("ARIE_RAW_ARCHIVE_BUCKET", None)
        else:
            os.environ["ARIE_RAW_ARCHIVE_BUCKET"] = previous_bucket

    object_name = uri.removeprefix(f"gs://{bucket_name}/")
    blob = storage.Client().bucket(bucket_name).blob(object_name)
    blob.reload()
    compressed = blob.download_as_bytes()
    expected_digest = hashlib.sha256(body).hexdigest()
    if not uri.endswith(".json.gz") or gzip.decompress(compressed) != body:
        raise AssertionError("Raw archive is not a valid gzip round trip")
    if (blob.metadata or {}).get("sha256") != expected_digest:
        raise AssertionError("Raw archive sha256 metadata mismatch")
    return {"uri": uri, "sha256": expected_digest, "gzip_verified": True}


def verify_partial_contract() -> dict:
    alert = _partial_suppression_alert(
        client_id="staging-probe",
        agency_id="staging-probe",
        run_id="staging-probe",
        source_failures={"pull-meta": "STAGING_SIMULATED_VENDOR_ERROR:meta"},
    )
    event_code = "PARTIAL_INGESTION_REPORT_SUPPRESSED"
    if alert.title != event_code or alert.metadata.get("event_code") != event_code:
        raise AssertionError("Partial-ingestion suppression alert contract changed")
    return {
        "expected_status": "partial",
        "event_code": event_code,
        "automated_email_allowed": False,
    }


def _entry_text(entry: dict) -> str:
    if "textPayload" in entry:
        return str(entry["textPayload"])
    payload = entry.get("jsonPayload", {})
    return str(payload.get("message") or payload.get("msg") or payload)


def verify_execution_logs(
    project_id: str,
    execution_name: str,
    expected_count: int,
    *,
    require_partial: bool = False,
) -> dict:
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    session = AuthorizedSession(credentials)
    page_token = None
    texts = []
    while True:
        body = {
            "resourceNames": [f"projects/{project_id}"],
            "filter": (
                'resource.type="cloud_run_job" AND '
                f'labels."run.googleapis.com/execution_name"="{execution_name}"'
            ),
            "orderBy": "timestamp asc",
            "pageSize": 1000,
        }
        if page_token:
            body["pageToken"] = page_token
        response = session.post(
            "https://logging.googleapis.com/v2/entries:list", json=body, timeout=60
        )
        response.raise_for_status()
        payload = response.json()
        texts.extend(_entry_text(entry) for entry in payload.get("entries", []))
        page_token = payload.get("nextPageToken")
        if not page_token:
            break

    mappings = {}
    for text in texts:
        match = _ASSIGNMENT_RE.search(text)
        if match:
            mappings[int(match["index"])] = match["client"]
    if set(mappings) != set(range(expected_count)):
        raise AssertionError(
            f"Execution logs contain task indexes {sorted(mappings)}, expected "
            f"0..{expected_count - 1}"
        )
    if len(set(mappings.values())) != expected_count:
        raise AssertionError("Execution logs contain duplicate client assignments")
    partial_suppression_logged = any(
        "PARTIAL_INGESTION_REPORT_SUPPRESSED" in text for text in texts
    )
    partial_status_logged = any(
        "'status': 'partial'" in text or '"status": "partial"' in text for text in texts
    )
    if require_partial and not (partial_suppression_logged and partial_status_logged):
        raise AssertionError(
            "Execution logs do not contain both partial status and delivery suppression"
        )
    return {
        "execution_name": execution_name,
        "task_assignments_verified": expected_count,
        "partial_status_logged": partial_status_logged,
        "partial_suppression_logged": partial_suppression_logged,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-client-count", type=int, default=20)
    parser.add_argument("--manifest-uri")
    parser.add_argument(
        "--lock-bucket", default=os.environ.get("ARIE_CLIENT_LOCK_BUCKET")
    )
    parser.add_argument(
        "--raw-bucket", default=os.environ.get("ARIE_RAW_ARCHIVE_BUCKET")
    )
    parser.add_argument("--project-id", default=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    parser.add_argument("--execution-name")
    parser.add_argument("--require-partial-log", action="store_true")
    args = parser.parse_args()
    if args.manifest_uri:
        manifest = load_work_manifest(args.manifest_uri)
        work_items = [
            (item["agency_id"], item["client_id"]) for item in manifest["work_items"]
        ]
    else:
        work_items = build_client_work_items(None)
    if not args.lock_bucket or not args.raw_bucket:
        parser.error("--lock-bucket and --raw-bucket are required")

    result = {
        "task_mapping": verify_task_mapping(work_items, args.expected_client_count),
        "lock": verify_gcs_lock(args.lock_bucket),
        "raw_archive": verify_raw_archive(args.raw_bucket),
        "partial_contract": verify_partial_contract(),
    }
    if args.execution_name:
        if not args.project_id:
            parser.error("--project-id is required with --execution-name")
        result["execution_logs"] = verify_execution_logs(
            args.project_id,
            args.execution_name,
            args.expected_client_count,
            require_partial=args.require_partial_log,
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
