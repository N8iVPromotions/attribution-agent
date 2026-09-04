from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from api.auth import (
    AuthPrincipal,
    _resolve_key,
    require_client_access,
    require_permission,
)
from api.models import ApprovalResolution, ClientConfigRequest, PipelineRunRequest
from api.routers import approvals, clients, pipeline, reports
from config.client_config import ClientConfig
from config.rbac_config import Permission, Role


def test_client_key_resolves_to_tenant_scoped_principal(monkeypatch):
    from config import client_config

    monkeypatch.setattr(
        client_config,
        "CLIENT_REGISTRY",
        {"acme": ClientConfig(client_id="acme", client_name="Acme")},
    )
    monkeypatch.setenv("API_KEY_ACME", "acme-key")

    assert _resolve_key("acme-key") == AuthPrincipal(Role.ANALYST, "acme")


def test_client_principal_cannot_access_another_tenant():
    with pytest.raises(HTTPException) as exc:
        require_client_access(AuthPrincipal(Role.ANALYST, "acme"), "other")
    assert exc.value.status_code == 403


def test_permission_denial_is_http_403():
    with pytest.raises(HTTPException) as exc:
        require_permission(
            AuthPrincipal(Role.ANALYST, "acme"), Permission.APPROVE_ACTIONS
        )
    assert exc.value.status_code == 403


def test_report_query_escapes_client_id(monkeypatch):
    captured = []

    class Spark:
        def sql(self, query):
            captured.append(query)
            return self

        def collect(self):
            return []

    monkeypatch.setattr("utils.databricks_writer._is_databricks", lambda: True)
    monkeypatch.setattr("utils.databricks_writer._get_spark", Spark)

    reports._fetch_reports("x' OR 1=1 --", 10)

    assert "WHERE client_id = 'x'' OR 1=1 --'" in captured[0]


def test_client_pipeline_request_must_target_own_client():
    request = PipelineRunRequest(agency_id="agency", client_ids=["other"], dry_run=True)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            pipeline.submit_pipeline_run(request, AuthPrincipal(Role.ANALYST, "acme"))
        )
    assert exc.value.status_code == 403


def test_pipeline_request_preserves_completed_report_month():
    request = PipelineRunRequest(agency_id="agency", report_month="2026-08")

    assert request.report_month == "2026-08"
    assert pipeline._pipeline_args(request)[-2:] == ["--report-month", "2026-08"]


def test_pipeline_request_rejects_future_report_month():
    with pytest.raises(ValidationError, match="completed calendar month"):
        PipelineRunRequest(agency_id="agency", report_month="2099-12")


def test_pipeline_request_rejects_unobserved_model():
    with pytest.raises(ValidationError, match="only last_touch"):
        PipelineRunRequest(agency_id="agency", attribution_model="w_shape")


def test_client_request_rejects_unobserved_model():
    with pytest.raises(ValidationError, match="only last_touch"):
        ClientConfigRequest(client_name="Acme", attribution_model="linear")


@pytest.mark.parametrize(
    ("enabled_field", "account_field"),
    [
        ("meta_enabled", "meta_ad_account_id"),
        ("google_ads_enabled", "google_ads_customer_id"),
        ("linkedin_ads_enabled", "linkedin_ads_account_id"),
        ("tiktok_ads_enabled", "tiktok_ads_advertiser_id"),
    ],
)
def test_client_request_requires_enabled_ad_account(enabled_field, account_field):
    with pytest.raises(ValidationError, match=account_field):
        ClientConfigRequest(client_name="Acme", **{enabled_field: True})


def test_client_request_rejects_unsafe_databricks_schema():
    with pytest.raises(ValidationError, match="Unsafe Databricks schema"):
        ClientConfigRequest(
            client_name="Acme",
            databricks_schema="workspace.safe; DROP SCHEMA workspace",
        )


def test_create_client_rejects_existing_generated_id(monkeypatch):
    from config import client_config

    registry = {"acme": ClientConfig(client_id="acme", client_name="Acme")}
    monkeypatch.setattr(client_config, "CLIENT_REGISTRY", registry)
    monkeypatch.setattr(client_config, "reload_client_registry", lambda: registry)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            clients.create_client(
                ClientConfigRequest(client_name="Acme"),
                AuthPrincipal(Role.ADMIN),
            )
        )

    assert exc.value.status_code == 409


@pytest.mark.parametrize(
    "history_start",
    [
        "",
        "2024-02-30",
        (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat(),
    ],
)
def test_stripe_enabled_client_requires_valid_history_start(history_start):
    with pytest.raises(ValidationError, match="stripe_history_start_date"):
        ClientConfigRequest(
            client_name="Acme",
            stripe_enabled=True,
            stripe_history_start_date=history_start,
        )


def test_client_api_maps_stripe_history_start_date():
    request = ClientConfigRequest(
        client_name="Acme",
        stripe_enabled=True,
        stripe_account_id="acct_acme",
        stripe_history_start_date="2019-06-01",
    )
    config = ClientConfig(
        client_id="acme",
        client_name=request.client_name,
        stripe_enabled=request.stripe_enabled,
        stripe_history_start_date=request.stripe_history_start_date,
    )

    assert clients._to_response(config).stripe_history_start_date == "2019-06-01"


def test_client_api_normalizes_and_maps_custom_hubspot_won_stages():
    request = ClientConfigRequest(
        client_name="Acme",
        hubspot_enabled=True,
        hubspot_closed_won_stage_ids=["Closed Won", "enterprise-won_42"],
    )
    config = ClientConfig(
        client_id="acme",
        client_name=request.client_name,
        hubspot_enabled=request.hubspot_enabled,
        hubspot_closed_won_stage_ids=tuple(request.hubspot_closed_won_stage_ids),
    )

    assert request.hubspot_closed_won_stage_ids == [
        "closedwon",
        "enterprisewon42",
    ]
    assert clients._to_response(config).hubspot_closed_won_stage_ids == [
        "closedwon",
        "enterprisewon42",
    ]


@pytest.mark.parametrize("stage_ids", [[], [""], ["---"]])
def test_client_api_rejects_empty_hubspot_won_stage_ids(stage_ids):
    with pytest.raises(ValidationError, match="closed-won stage ID"):
        ClientConfigRequest(
            client_name="Acme",
            hubspot_closed_won_stage_ids=stage_ids,
        )


def test_create_and_update_client_persist_hubspot_won_stage_ids(monkeypatch):
    from config import client_config

    registry: dict[str, ClientConfig] = {}
    saved: list[ClientConfig] = []
    monkeypatch.setattr(client_config, "CLIENT_REGISTRY", registry)
    monkeypatch.setattr(client_config, "reload_client_registry", lambda: registry)
    monkeypatch.setattr(client_config, "save_client_config", saved.append)
    monkeypatch.setattr(
        client_config,
        "attach_client_secret_values",
        lambda config, _secret_values: config,
    )

    created = asyncio.run(
        clients.create_client(
            ClientConfigRequest(
                client_name="Acme",
                hubspot_closed_won_stage_ids=["Closed Won", "enterprise-won"],
            ),
            AuthPrincipal(Role.ADMIN),
        )
    )
    assert created.hubspot_closed_won_stage_ids == ["closedwon", "enterprisewon"]
    assert saved[-1].hubspot_closed_won_stage_ids == ("closedwon", "enterprisewon")

    registry["acme"] = saved[-1]
    updated = asyncio.run(
        clients.update_client(
            "acme",
            ClientConfigRequest(
                client_name="Acme",
                hubspot_closed_won_stage_ids=["contract-signed"],
            ),
            AuthPrincipal(Role.ADMIN),
        )
    )
    assert updated.hubspot_closed_won_stage_ids == ["contractsigned"]
    assert saved[-1].hubspot_closed_won_stage_ids == ("contractsigned",)


def test_approval_resolution_handles_missing_and_existing(monkeypatch):
    principal = AuthPrincipal(Role.ADMIN)
    body = ApprovalResolution(resolution="approved")
    monkeypatch.setattr(approvals, "_fetch_approval_status", lambda _id: None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(approvals.resolve_approval("missing", body, principal))
    assert exc.value.status_code == 404

    statuses = iter(["pending", "approved"])
    monkeypatch.setattr(approvals, "_fetch_approval_status", lambda _id: next(statuses))
    monkeypatch.setattr(approvals, "_resolve_approval", lambda *args, **kwargs: True)
    monkeypatch.setattr("utils.audit_logger.log_event", lambda *args, **kwargs: None)

    result = asyncio.run(approvals.resolve_approval("action-1", body, principal))
    assert result == {"action_id": "action-1", "status": "approved"}
