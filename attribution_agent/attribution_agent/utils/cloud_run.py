"""Cloud Run Jobs submission helpers for ARIE pipeline runs."""

from __future__ import annotations

import os
from dataclasses import dataclass

from utils.secrets import redact_secrets


@dataclass(frozen=True)
class CloudRunJobSettings:
    project_id: str
    region: str
    job_name: str


class CloudRunJobError(RuntimeError):
    """Raised when ARIE cannot submit the configured Cloud Run Job."""


def cloud_run_settings() -> CloudRunJobSettings:
    return CloudRunJobSettings(
        project_id=(
            os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GCP_PROJECT")
            or os.environ.get("PROJECT_ID")
            or os.environ.get("CLOUDSDK_CORE_PROJECT")
            or ""
        ).strip(),
        region=os.environ.get("ATTRIBUTION_CLOUD_RUN_REGION", "us-central1").strip(),
        job_name=os.environ.get(
            "ATTRIBUTION_LAUNCHER_CLOUD_RUN_JOB", "attribution-launcher"
        ).strip(),
    )


def is_cloud_run_configured(settings: CloudRunJobSettings | None = None) -> bool:
    settings = settings or cloud_run_settings()
    return bool(settings.project_id and settings.region and settings.job_name)


def build_pipeline_args(
    agency_id: str,
    client_ids: list[str] | None = None,
    *,
    dry_run: bool = True,
    attribution_model: str = "last_touch",
    run_mode: str = "agency",
) -> list[str]:
    args = ["flows/job_launcher.py", "--agency", agency_id]
    for client_id in client_ids or []:
        args += ["--client", client_id]
    if dry_run:
        args.append("--dry-run")
    args += ["--attribution-model", attribution_model]
    return args


def build_gcloud_command(
    args: list[str],
    settings: CloudRunJobSettings | None = None,
    *,
    wait: bool = False,
) -> str:
    settings = settings or cloud_run_settings()
    quoted_args = ",".join(args)
    command = (
        f"gcloud run jobs execute {settings.job_name} "
        f"--project {settings.project_id} "
        f"--region {settings.region} "
        f'--args "{quoted_args}"'
    )
    if wait:
        command += " --wait"
    return command


def submit_cloud_run_job(
    *,
    agency_id: str,
    client_ids: list[str] | None = None,
    dry_run: bool = True,
    attribution_model: str = "last_touch",
    run_mode: str = "agency",
    settings: CloudRunJobSettings | None = None,
) -> str:
    settings = settings or cloud_run_settings()
    if not is_cloud_run_configured(settings):
        raise CloudRunJobError(
            "GOOGLE_CLOUD_PROJECT/PROJECT_ID, ATTRIBUTION_CLOUD_RUN_REGION, "
            "and ATTRIBUTION_LAUNCHER_CLOUD_RUN_JOB must be configured."
        )

    try:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
    except Exception as exc:  # pragma: no cover - dependency verified in deploy
        raise CloudRunJobError(
            "google-auth is required for Cloud Run job execution."
        ) from exc

    args = build_pipeline_args(
        agency_id,
        client_ids,
        dry_run=dry_run,
        attribution_model=attribution_model,
        run_mode=run_mode,
    )
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    session = AuthorizedSession(credentials)
    url = (
        f"https://run.googleapis.com/v2/projects/{settings.project_id}/locations/"
        f"{settings.region}/jobs/{settings.job_name}:run"
    )
    body = {"overrides": {"containerOverrides": [{"args": args}]}}
    response = session.post(url, json=body, timeout=30)
    if response.status_code >= 400:
        raise CloudRunJobError(
            f"Cloud Run Jobs API returned {response.status_code}: "
            f"{redact_secrets(response.text[:500])}"
        )
    payload = response.json()
    return (
        payload.get("name")
        or payload.get("metadata", {}).get("name")
        or settings.job_name
    )
