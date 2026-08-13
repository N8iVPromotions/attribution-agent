from __future__ import annotations

from config.client_config import ClientConfig
from flows import operator_health_check as health


def test_collect_credential_health_alerts_uses_stable_daily_ids(monkeypatch):
    cfg = ClientConfig(
        client_id="acme",
        client_name="Acme",
        hubspot_enabled=True,
    )
    monkeypatch.setattr(health, "reload_client_registry", lambda: None)
    monkeypatch.setattr(health, "CLIENT_REGISTRY", {"acme": cfg})

    alerts = health.collect_credential_health_alerts(
        client_ids=["acme"],
        run_date="2026-08-13",
    )

    assert len(alerts) == 1
    assert alerts[0].alert_id == "health-2026-08-13-credential_missing-acme-hubspot"
    assert alerts[0].severity == "critical"


def test_run_health_check_can_preview_without_dispatch(monkeypatch):
    cfg = ClientConfig(client_id="quiet", client_name="Quiet")
    dispatched = []
    monkeypatch.setattr(health, "reload_client_registry", lambda: None)
    monkeypatch.setattr(health, "CLIENT_REGISTRY", {"quiet": cfg})
    monkeypatch.setattr(health, "dispatch_alerts", lambda alerts: dispatched.extend(alerts))

    summary = health.run_health_check(client_ids=["quiet"], dispatch=False)

    assert summary["checked_clients"] == 1
    assert summary["alerts"] == 0
    assert dispatched == []
