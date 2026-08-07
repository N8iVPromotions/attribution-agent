"""
Operator alerting for ARIE.

Creates durable alert records and fans them out to Telegram plus an optional
Genie One webhook. Alerts are best-effort: they should never fail the pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Literal

from utils.secrets import redact_secrets

logger = logging.getLogger(__name__)

AlertSeverity = Literal["info", "warning", "critical"]

_AUTH_FAILURE_RE = re.compile(
    r"401|403|unauthori[sz]ed|invalid[_\s-]?token|expired|reauth|permission",
    re.IGNORECASE,
)
_SOURCE_LABELS = {
    "meta": "Meta Ads",
    "google_ads": "Google Ads",
    "linkedin_ads": "LinkedIn Ads",
    "tiktok_ads": "TikTok Ads",
    "hubspot": "HubSpot",
    "stripe": "Stripe",
}


@dataclass
class OperatorAlert:
    severity: AlertSeverity
    category: str
    title: str
    message: str
    client_id: str = ""
    agency_id: str = ""
    source: str = ""
    run_id: str = ""
    action_required: str = ""
    metadata: dict = field(default_factory=dict)
    alert_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    event_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "open"

    def to_record(self) -> dict:
        payload = asdict(self)
        payload["event_time"] = self.event_time
        payload["metadata_json"] = redact_secrets(json.dumps(self.metadata, default=str))
        payload.pop("metadata", None)
        payload["message"] = redact_secrets(self.message)
        payload["action_required"] = redact_secrets(self.action_required)
        return payload


def _alert_channels() -> set[str]:
    raw = os.environ.get("ARIE_ALERT_CHANNELS", "telegram")
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def _alerts_enabled() -> bool:
    return os.environ.get("ARIE_ALERTS_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def _source_from_label(label: str) -> str:
    source = (label or "").lower().replace("pull-", "").replace("-", "_")
    if source == "google":
        return "google_ads"
    if source == "linkedin":
        return "linkedin_ads"
    if source == "tiktok":
        return "tiktok_ads"
    return source


def _platform_enabled(config, source: str) -> bool:
    return bool(getattr(config, f"{source}_enabled", False))


def _platform_token_field(source: str) -> str:
    return {
        "meta": "meta_access_token",
        "google_ads": "google_ads_refresh_token",
        "linkedin_ads": "linkedin_access_token",
        "tiktok_ads": "tiktok_access_token",
        "hubspot": "hubspot_access_token",
        "stripe": "stripe_secret_key",
    }[source]


def _platform_expiry_field(source: str) -> str:
    return {
        "meta": "meta_token_expires_at",
        "google_ads": "google_ads_token_expires_at",
        "linkedin_ads": "linkedin_token_expires_at",
        "tiktok_ads": "tiktok_token_expires_at",
        "hubspot": "hubspot_token_expires_at",
        "stripe": "stripe_token_expires_at",
    }[source]


def _expiry_env_key(client_id: str, source: str) -> str:
    safe_client = re.sub(r"[^A-Z0-9]+", "_", client_id.upper()).strip("_")
    safe_source = re.sub(r"[^A-Z0-9]+", "_", source.upper()).strip("_")
    return f"ARIE_TOKEN_EXPIRES_{safe_client}_{safe_source}"


def _parse_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _format_telegram(alert: OperatorAlert) -> str:
    header = f"[{alert.severity.upper()}] {alert.title}"
    details = [
        header,
        "",
        alert.message,
    ]
    if alert.client_id:
        details.append(f"Client: `{alert.client_id}`")
    if alert.source:
        details.append(f"Source: `{alert.source}`")
    if alert.run_id:
        details.append(f"Run: `{alert.run_id[:8]}`")
    if alert.action_required:
        details.extend(["", f"Action: {alert.action_required}"])
    return "\n".join(details)


def _send_telegram(alert: OperatorAlert) -> None:
    from agents.control import arie_bot

    arie_bot.notify(_format_telegram(alert))


def _send_genie_one(alert: OperatorAlert) -> None:
    import requests

    url = os.environ.get("GENIE_ONE_WEBHOOK_URL", "").strip()
    if not url:
        logger.debug("[Alerts] GENIE_ONE_WEBHOOK_URL not configured")
        return

    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("GENIE_ONE_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    response = requests.post(
        url,
        json={"source": "ARIE", "alert": alert.to_record()},
        headers=headers,
        timeout=15,
    )
    response.raise_for_status()


def send_operator_alert(alert: OperatorAlert) -> None:
    """Persist and deliver one alert. Never raises."""
    try:
        from utils.databricks_writer import write_operator_alert

        write_operator_alert(alert.to_record())
    except Exception as exc:
        logger.warning(f"[Alerts] Could not persist alert: {exc}")

    if not _alerts_enabled():
        return

    channels = _alert_channels()
    for channel, sender in {
        "telegram": _send_telegram,
        "genie_one": _send_genie_one,
        "genie": _send_genie_one,
    }.items():
        if channel not in channels:
            continue
        try:
            sender(alert)
        except Exception as exc:
            logger.warning(f"[Alerts] {channel} delivery failed: {redact_secrets(str(exc))}")


def build_credential_alerts(
    config,
    *,
    tokens: dict[str, str] | None = None,
    warning_days: int | None = None,
    run_id: str = "",
) -> list[OperatorAlert]:
    warning_days = warning_days or int(os.environ.get("ARIE_TOKEN_EXPIRY_WARNING_DAYS", "14"))
    today = datetime.now(timezone.utc).date()
    alerts: list[OperatorAlert] = []
    tokens = tokens or {}

    for source, label in _SOURCE_LABELS.items():
        if not _platform_enabled(config, source):
            continue

        token_field = _platform_token_field(source)
        token_value = tokens.get(source)
        if token_value is None:
            token_value = getattr(config, token_field, "")
        if not token_value:
            alerts.append(
                OperatorAlert(
                    severity="critical",
                    category="credential_missing",
                    title=f"{label} credential missing",
                    message=f"{label} is enabled but ARIE could not resolve a credential.",
                    client_id=config.client_id,
                    agency_id=getattr(config, "agency_id", ""),
                    source=source,
                    run_id=run_id,
                    action_required=f"Open the client in ARIE and refresh the {label} credential.",
                )
            )

        expiry_value = getattr(config, _platform_expiry_field(source), "")
        expiry_value = expiry_value or os.environ.get(_expiry_env_key(config.client_id, source), "")
        expires_at = _parse_date(expiry_value)
        if not expires_at:
            continue

        days_left = (expires_at - today).days
        if days_left < 0:
            alerts.append(
                OperatorAlert(
                    severity="critical",
                    category="token_expired",
                    title=f"{label} token expired",
                    message=f"{label} token expired on {expires_at.isoformat()}.",
                    client_id=config.client_id,
                    agency_id=getattr(config, "agency_id", ""),
                    source=source,
                    run_id=run_id,
                    action_required=f"Reconnect {label} before the next attribution run.",
                    metadata={"expires_at": expires_at.isoformat(), "days_left": days_left},
                )
            )
        elif days_left <= warning_days:
            alerts.append(
                OperatorAlert(
                    severity="warning",
                    category="token_expiring",
                    title=f"{label} token expires soon",
                    message=f"{label} token expires in {days_left} day(s), on {expires_at.isoformat()}.",
                    client_id=config.client_id,
                    agency_id=getattr(config, "agency_id", ""),
                    source=source,
                    run_id=run_id,
                    action_required=f"Refresh {label} credentials before they expire.",
                    metadata={"expires_at": expires_at.isoformat(), "days_left": days_left},
                )
            )

    return alerts


def build_source_failure_alerts(
    config,
    source_failures: dict,
    *,
    run_id: str = "",
) -> list[OperatorAlert]:
    alerts: list[OperatorAlert] = []
    for label, raw_error in (source_failures or {}).items():
        source = _source_from_label(label)
        error = redact_secrets(str(raw_error))
        auth_failure = bool(_AUTH_FAILURE_RE.search(error))
        severity: AlertSeverity = "critical" if auth_failure else "warning"
        category = "source_auth_failure" if auth_failure else "source_failure"
        platform = _SOURCE_LABELS.get(source, source.replace("_", " ").title())
        action = (
            f"Refresh or reauthorize {platform} credentials."
            if auth_failure
            else f"Review {platform} connector logs and rerun the client when fixed."
        )
        alerts.append(
            OperatorAlert(
                severity=severity,
                category=category,
                title=f"{platform} ingest failed",
                message=error[:900],
                client_id=config.client_id,
                agency_id=getattr(config, "agency_id", ""),
                source=source,
                run_id=run_id,
                action_required=action,
                metadata={"source_label": label},
            )
        )
    return alerts


def build_validation_alerts(config, reports: list, *, run_id: str = "") -> list[OperatorAlert]:
    alerts: list[OperatorAlert] = []
    for report in reports:
        if not report or (not report.warnings and report.passed):
            continue
        severity: AlertSeverity = "critical" if not report.passed else "warning"
        source = str(getattr(report, "source", "") or "")
        messages = []
        if getattr(report, "errors", None):
            messages.extend(report.errors)
        if getattr(report, "warnings", None):
            messages.extend(report.warnings)
        alerts.append(
            OperatorAlert(
                severity=severity,
                category="data_quality",
                title=f"{source.title()} data quality issue",
                message="; ".join(redact_secrets(str(m)) for m in messages)[:1200],
                client_id=config.client_id,
                agency_id=getattr(config, "agency_id", ""),
                source=source,
                run_id=run_id,
                action_required="Review source data quality before trusting the report.",
                metadata={
                    "passed": getattr(report, "passed", None),
                    "warning_count": len(getattr(report, "warnings", []) or []),
                    "error_count": len(getattr(report, "errors", []) or []),
                },
            )
        )
    return alerts


def build_attribution_coverage_alerts(
    config,
    attribution: dict,
    *,
    run_id: str = "",
) -> list[OperatorAlert]:
    if not attribution or attribution.get("attribution_error"):
        return []
    total = float(attribution.get("total_revenue") or 0)
    unattributed = float(attribution.get("unattributed_revenue") or 0)
    if total <= 0:
        return []
    rate = unattributed / total
    threshold = float(os.environ.get("ARIE_UNATTRIBUTED_REVENUE_ALERT_PCT", "0.30"))
    if rate < threshold:
        return []
    return [
        OperatorAlert(
            severity="warning",
            category="attribution_accuracy",
            title="High unattributed revenue",
            message=(
                f"{rate:.0%} of revenue is unattributed "
                f"(${unattributed:,.0f} of ${total:,.0f})."
            ),
            client_id=config.client_id,
            agency_id=getattr(config, "agency_id", ""),
            run_id=run_id,
            action_required="Audit UTMs, HubSpot source fields, contact emails, and payment/deal matching.",
            metadata={"unattributed_rate": rate, "total_revenue": total},
        )
    ]


def dispatch_alerts(alerts: list[OperatorAlert]) -> None:
    for alert in alerts:
        send_operator_alert(alert)
