from __future__ import annotations

import pytest

from agents.comms import comms_agent
from agents.insight.insight_agent import InsightReport
from config.agency_config import AgencyConfig


def _report(**overrides) -> InsightReport:
    values = {
        "client_id": "client-a",
        "client_name": "Client A",
        "report_month": "2026-08",
        "narrative": "Evidence-backed report.",
        "key_findings": ["Paid search led pipeline."],
        "top_channel": "Paid Search",
        "total_pipeline": 1_000.0,
        "total_spend": 200.0,
        "overall_roi": 5.0,
        "attribution_model": "last_touch",
        "generated_at": "2026-09-03T12:00:00+00:00",
    }
    values.update(overrides)
    return InsightReport(**values)


def _agency(**overrides) -> AgencyConfig:
    values = {
        "agency_id": "agency-a",
        "agency_name": "Agency A",
        "brand_color": "2563EB",
        "sender_name": "Agency Analytics",
        "sender_email": "reports@example.com",
        "reply_to": "reply@example.com",
    }
    values.update(overrides)
    return AgencyConfig(**values)


def test_html_escapes_report_and_branding_and_rejects_unsafe_assets():
    report = _report(
        client_name='<img src=x onerror="alert(1)">',
        report_month="<b>August</b>",
        narrative="Summary <script>alert(1)</script>\n\nNext & final",
        key_findings=["<svg onload=alert(1)>", "Revenue > spend"],
        top_channel="<em>Paid Search</em>",
        attribution_model="<iframe src=evil>",
    )
    agency = _agency(
        agency_name="<strong>Agency A</strong>",
        sender_name="<marquee>Agency Analytics</marquee>",
        brand_color="000000; background:url(javascript:alert(1))",
        brand_logo_url="javascript:alert(1)",
        powerbi_workspace_url="data:text/html,<script>alert(1)</script>",
    )

    rendered = comms_agent._build_html(report, agency_config=agency)

    assert "<script>alert(1)</script>" not in rendered
    assert '<img src=x onerror="alert(1)">' not in rendered
    assert "<svg onload=alert(1)>" not in rendered
    assert "<iframe src=evil>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "&lt;svg onload=alert(1)&gt;" in rendered
    assert "&lt;strong&gt;Agency A&lt;/strong&gt;" in rendered
    assert "javascript:alert(1)" not in rendered
    assert "data:text/html" not in rendered
    assert "background: #1a1a1a;" in rendered
    assert "View Live Dashboard" not in rendered


def test_html_allows_and_attribute_escapes_valid_http_assets():
    agency = _agency(
        brand_logo_url="https://cdn.example.com/logo.png?a=1&b=2",
        powerbi_workspace_url="http://dashboard.example.com/report?a=1&b=2",
    )

    rendered = comms_agent._build_html(_report(), agency_config=agency)

    assert "background: #2563EB;" in rendered
    assert 'src="https://cdn.example.com/logo.png?a=1&amp;b=2"' in rendered
    assert 'href="http://dashboard.example.com/report?a=1&amp;b=2"' in rendered


def test_direct_delivery_entrypoints_fail_closed(monkeypatch):
    calls = []
    monkeypatch.setattr(
        comms_agent,
        "_deliver_report",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    with pytest.raises(comms_agent.DeliveryAuthorizationError, match="disabled"):
        comms_agent.send_report(_report(), "client@example.com")
    with pytest.raises(comms_agent.DeliveryAuthorizationError, match="disabled"):
        comms_agent.run_full_pipeline("client-a", "client@example.com")
    with pytest.raises(comms_agent.DeliveryAuthorizationError, match="agency_flow"):
        comms_agent.send_agency_report(_report(), "client@example.com", _agency())

    assert calls == []


def test_governed_agency_delivery_requires_opaque_authorization(monkeypatch):
    calls = []
    monkeypatch.setattr(
        comms_agent,
        "_deliver_report",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    sent = comms_agent.send_agency_report(
        _report(),
        "client@example.com",
        _agency(),
        delivery_authorization=comms_agent._AGENCY_FLOW_DELIVERY_AUTHORIZATION,
    )

    assert sent is True
    assert len(calls) == 1


def test_email_header_newlines_are_rejected_before_provider_call(monkeypatch):
    calls = []
    monkeypatch.setattr(
        comms_agent,
        "_send_via_sendgrid",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with pytest.raises(
        comms_agent.DeliveryNotAcceptedError, match="sender display name"
    ):
        comms_agent.send_agency_report(
            _report(),
            "client@example.com",
            _agency(sender_name="Agency\r\nBcc: victim@example.com"),
            delivery_authorization=comms_agent._AGENCY_FLOW_DELIVERY_AUTHORIZATION,
        )

    assert calls == []
