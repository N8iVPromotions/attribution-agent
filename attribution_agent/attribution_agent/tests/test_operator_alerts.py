from __future__ import annotations

from datetime import datetime, timedelta, timezone

from config.client_config import ClientConfig
from utils.operator_alerts import (
    build_attribution_coverage_alerts,
    build_credential_alerts,
    build_source_failure_alerts,
)


def _future_date(days: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()


def test_build_credential_alerts_warns_on_expiring_token(monkeypatch):
    monkeypatch.setenv("ARIE_TOKEN_EXPIRY_WARNING_DAYS", "14")
    cfg = ClientConfig(
        client_id="acme",
        client_name="Acme",
        meta_enabled=True,
        meta_token_expires_at=_future_date(7),
    )

    alerts = build_credential_alerts(
        cfg,
        tokens={"meta": "token-present"},
        run_id="run-123",
    )

    assert len(alerts) == 1
    assert alerts[0].severity == "warning"
    assert alerts[0].category == "token_expiring"
    assert alerts[0].source == "meta"
    assert alerts[0].run_id == "run-123"


def test_build_credential_alerts_marks_missing_enabled_token_critical():
    cfg = ClientConfig(client_id="acme", client_name="Acme", hubspot_enabled=True)

    alerts = build_credential_alerts(cfg, tokens={"hubspot": ""})

    assert len(alerts) == 1
    assert alerts[0].severity == "critical"
    assert alerts[0].category == "credential_missing"
    assert alerts[0].source == "hubspot"


def test_build_source_failure_alerts_classifies_auth_failures():
    cfg = ClientConfig(client_id="acme", client_name="Acme", google_ads_enabled=True)

    alerts = build_source_failure_alerts(
        cfg,
        {"pull-google-ads": "401 Unauthorized: invalid token"},
    )

    assert len(alerts) == 1
    assert alerts[0].severity == "critical"
    assert alerts[0].category == "source_auth_failure"
    assert alerts[0].source == "google_ads"


def test_build_attribution_coverage_alerts_warns_on_high_unattributed(monkeypatch):
    monkeypatch.setenv("ARIE_UNATTRIBUTED_REVENUE_ALERT_PCT", "0.30")
    cfg = ClientConfig(client_id="acme", client_name="Acme")

    alerts = build_attribution_coverage_alerts(
        cfg,
        {"total_revenue": 100000, "unattributed_revenue": 45000},
    )

    assert len(alerts) == 1
    assert alerts[0].severity == "warning"
    assert alerts[0].category == "attribution_accuracy"
