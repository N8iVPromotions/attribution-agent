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
        "flows/job_launcher.py",
        "--agency",
        "n8iv_promotions",
        "--client",
        "client_a",
        "--client",
        "client_b",
        "--dry-run",
        "--attribution-model",
        "w_shape",
    ]


def test_build_pipeline_args_for_live_business_run():
    assert build_pipeline_args(
        "n8iv_promotions",
        ["n8iv_promotions"],
        dry_run=False,
        attribution_model="last_touch",
        run_mode="business",
    ) == [
        "flows/job_launcher.py",
        "--agency",
        "n8iv_promotions",
        "--client",
        "n8iv_promotions",
        "--attribution-model",
        "last_touch",
    ]


def test_build_gcloud_command_matches_cloud_run_job_args():
    settings = CloudRunJobSettings(
        project_id="n8iv-analytics-production",
        region="us-central1",
        job_name="attribution-launcher",
    )
    args = build_pipeline_args(
        "demo_agency",
        ["demo_client"],
        dry_run=True,
        attribution_model="linear",
        run_mode="agency",
    )

    assert build_gcloud_command(args, settings, wait=True) == (
        "gcloud run jobs execute attribution-launcher "
        "--project n8iv-analytics-production "
        "--region us-central1 "
        '--args "flows/job_launcher.py,--agency,demo_agency,--client,'
        'demo_client,--dry-run,--attribution-model,linear" '
        "--wait"
    )


def test_cloud_run_settings_reads_gcp_project(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "my-project")
    monkeypatch.setenv("ATTRIBUTION_CLOUD_RUN_REGION", "us-central1")
    monkeypatch.setenv("ATTRIBUTION_LAUNCHER_CLOUD_RUN_JOB", "attribution-launcher")

    settings = cloud_run_settings()

    assert is_cloud_run_configured(settings)
    assert settings.project_id == "my-project"
    assert settings.region == "us-central1"
    assert settings.job_name == "attribution-launcher"
