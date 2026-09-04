"""Snapshot a client manifest, run the task array, then run its finalizer."""

from __future__ import annotations

import argparse
import logging
import os
import time

from flows.agency_flow import build_client_work_items
from utils.report_period import resolve_report_period
from utils.work_manifest import (
    create_work_manifest,
    validate_warehouse_attribution_model,
)

logger = logging.getLogger(__name__)

_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def _session():
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(scopes=[_SCOPE])
    return AuthorizedSession(credentials)


def _request_json(session, method: str, url: str, **kwargs) -> dict:
    response = session.request(method, url, timeout=60, **kwargs)
    response.raise_for_status()
    return response.json()


def _wait_operation(session, operation: dict, timeout_seconds: int) -> dict:
    deadline = time.monotonic() + timeout_seconds
    operation_url = f"https://run.googleapis.com/v2/{operation['name']}"
    while not operation.get("done"):
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Cloud Run operation timed out: {operation['name']}")
        time.sleep(5)
        operation = _request_json(session, "GET", operation_url)
    if operation.get("error"):
        raise RuntimeError(f"Cloud Run operation failed: {operation['error']}")
    return operation.get("response", {})


def _wait_execution(session, execution: dict, timeout_seconds: int) -> dict:
    deadline = time.monotonic() + timeout_seconds
    execution_url = f"https://run.googleapis.com/v2/{execution['name']}"
    while not execution.get("completionTime"):
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Cloud Run execution timed out: {execution['name']}")
        time.sleep(10)
        execution = _request_json(session, "GET", execution_url)
    failed = int(execution.get("failedCount", 0))
    cancelled = int(execution.get("cancelledCount", 0))
    task_count = int(execution.get("taskCount", 1))
    succeeded = int(execution.get("succeededCount", 0))
    if failed or cancelled or succeeded != task_count:
        raise RuntimeError(
            "Cloud Run execution did not complete successfully: "
            f"succeeded={succeeded} failed={failed} cancelled={cancelled} "
            f"task_count={task_count}"
        )
    return execution


def run_job(
    session,
    *,
    project_id: str,
    region: str,
    job_name: str,
    environment: dict[str, str],
    task_count: int | None = None,
    timeout_seconds: int = 7_200,
) -> dict:
    name = f"projects/{project_id}/locations/{region}/jobs/{job_name}"
    overrides: dict = {
        "containerOverrides": [
            {
                "env": [
                    {"name": key, "value": value}
                    for key, value in sorted(environment.items())
                ]
            }
        ]
    }
    if task_count is not None:
        overrides["taskCount"] = task_count
    operation = _request_json(
        session,
        "POST",
        f"https://run.googleapis.com/v2/{name}:run",
        json={"overrides": overrides},
    )
    execution = _wait_operation(session, operation, timeout_seconds)
    return _wait_execution(session, execution, timeout_seconds)


def launch(
    *,
    agency_id: str | None,
    client_filter: list[str] | None,
    dry_run: bool,
    attribution_model: str,
    report_month: str | None = None,
    expected_client_count: int | None = None,
    simulate_vendor_error: str | None = None,
    simulate_vendor_error_client: str | None = None,
) -> dict:
    selected_model = validate_warehouse_attribution_model(attribution_model)
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("PROJECT_ID")
    if not project_id:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT or PROJECT_ID is required")
    region = os.environ.get("ATTRIBUTION_CLOUD_RUN_REGION", "us-central1")
    pipeline_job = os.environ.get("ATTRIBUTION_CLOUD_RUN_JOB", "attribution-pipeline")
    finalizer_job = os.environ.get(
        "ATTRIBUTION_BENCHMARK_FINALIZER_JOB", "attribution-benchmark-finalizer"
    )
    work_items = build_client_work_items(agency_id, client_filter)
    if expected_client_count is not None and len(work_items) != expected_client_count:
        raise RuntimeError(
            f"Expected {expected_client_count} clients, found {len(work_items)}"
        )
    period = resolve_report_period(report_month)
    manifest_uri, manifest = create_work_manifest(
        work_items,
        dry_run=dry_run,
        attribution_model=selected_model,
        report_month=period.month,
    )
    environment = {
        "ARIE_PERIOD_END": period.end.isoformat(),
        "ARIE_PERIOD_START": period.start.isoformat(),
        "ARIE_WORK_MANIFEST_URI": manifest_uri,
        "ARIE_REPORT_MONTH": period.month,
        "ARIE_REPORT_TIMEZONE": period.timezone_name,
        "ARIE_STREAMING_INGEST": "true",
    }
    if simulate_vendor_error:
        if not dry_run:
            raise ValueError("Staging vendor fault injection requires --dry-run")
        environment.update(
            {
                "ARIE_STAGING_VALIDATION": "true",
                "ARIE_STAGING_FAIL_SOURCE": simulate_vendor_error,
            }
        )
        if simulate_vendor_error_client:
            environment["ARIE_STAGING_FAIL_CLIENT_ID"] = simulate_vendor_error_client
    session = _session()
    pipeline_execution = run_job(
        session,
        project_id=project_id,
        region=region,
        job_name=pipeline_job,
        environment=environment,
        task_count=len(work_items),
    )
    finalizer_execution = run_job(
        session,
        project_id=project_id,
        region=region,
        job_name=finalizer_job,
        environment={
            "ARIE_PERIOD_END": period.end.isoformat(),
            "ARIE_PERIOD_START": period.start.isoformat(),
            "ARIE_REPORT_MONTH": period.month,
            "ARIE_REPORT_TIMEZONE": period.timezone_name,
            "ARIE_WORK_MANIFEST_URI": manifest_uri,
        },
    )
    return {
        "run_id": manifest["run_id"],
        "manifest_uri": manifest_uri,
        "report_month": period.month,
        "task_count": len(work_items),
        "pipeline_execution": pipeline_execution["name"],
        "finalizer_execution": finalizer_execution["name"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agency")
    parser.add_argument("--client", action="append", dest="clients")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--attribution-model", default="last_touch")
    parser.add_argument("--report-month")
    parser.add_argument("--expected-client-count", type=int)
    parser.add_argument("--simulate-vendor-error")
    parser.add_argument("--simulate-vendor-error-client")
    args = parser.parse_args()
    logger.info(
        "[Launcher] %s",
        launch(
            agency_id=args.agency,
            client_filter=args.clients,
            dry_run=args.dry_run,
            attribution_model=args.attribution_model,
            report_month=args.report_month,
            expected_client_count=args.expected_client_count,
            simulate_vendor_error=args.simulate_vendor_error,
            simulate_vendor_error_client=args.simulate_vendor_error_client,
        ),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
