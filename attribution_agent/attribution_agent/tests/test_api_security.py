from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

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
