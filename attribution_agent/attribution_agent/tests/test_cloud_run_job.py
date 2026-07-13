from __future__ import annotations

from utils.cloud_run import (
    CloudRunJobSettings,
    build_gcloud_command,
    build_pipeline_args,
    cloud_run_settings,
    is_cloud_run_configured,
)


def test_build_pipeline_args_for_filtered_dry_run():
    assert build_pipeline_args(
        "n8iv_promotions",
        ["client_a", "client_b"],
        dry_run=True,
        attribution_model="w_shape",
        run_mode="agency",
    ) == [
        "flows/agency_flow.py",
        "--agency",
        "n8iv_promotions",
        "--client-filter",
        "client_a",
        "client_b",
        "--dry-run",
        "--attribution-model",
        "w_shape",
        "--run-mode",
        "agency",
    ]


def test_build_pipeline_args_for_live_business_run():
    assert build_pipeline_args(
        "n8iv_promotions",
        ["n8iv_promotions"],
        dry_run=False,
        attribution_model="last_touch",
        run_mode="business",
    ) == [
        "flows/agency_flow.py",
        "--agency",
        "n8iv_promotions",
        "--client-filter",
        "n8iv_promotions",
        "--attribution-model",
        "last_touch",
        "--run-mode",
        "business",
    ]


def test_build_gcloud_command_matches_cloud_run_job_args():
    settings = CloudRunJobSettings(
        project_id="n8iv-analytics-production",
        region="us-central1",
        job_name="attribution-pipeline",
    )
    args = build_pipeline_args(
        "demo_agency",
        ["demo_client"],
        dry_run=True,
        attribution_model="linear",
        run_mode="agency",
    )

    assert build_gcloud_command(args, settings, wait=True) == (
        "gcloud run jobs execute attribution-pipeline "
        "--project n8iv-analytics-production "
        "--region us-central1 "
        '--args "flows/agency_flow.py,--agency,demo_agency,--client-filter,'
        'demo_client,--dry-run,--attribution-model,linear,--run-mode,agency" '
        "--wait"
    )


def test_cloud_run_settings_reads_gcp_project(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "my-project")
    monkeypatch.setenv("ATTRIBUTION_CLOUD_RUN_REGION", "us-central1")
    monkeypatch.setenv("ATTRIBUTION_CLOUD_RUN_JOB", "attribution-pipeline")

    settings = cloud_run_settings()

    assert is_cloud_run_configured(settings)
    assert settings.project_id == "my-project"
    assert settings.region == "us-central1"
    assert settings.job_name == "attribution-pipeline"
