"""Scheduled operator health checks for ARIE.

This flow runs outside normal attribution execution so expiring or missing
credentials can alert the operator before the next revenue report is due.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from datetime import datetime, timezone

from config.client_config import CLIENT_REGISTRY, ClientConfig, reload_client_registry
from utils.operator_alerts import (
    OperatorAlert,
    build_credential_alerts,
    dispatch_alerts,
)

logger = logging.getLogger(__name__)


def _safe_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value or "system").strip("-")[:80]


def _stable_health_alert_id(alert: OperatorAlert, run_date: str) -> str:
    parts = [
        "health",
        run_date,
        alert.category,
        alert.client_id,
        alert.source,
    ]
    return "-".join(_safe_id(part) for part in parts if part)


def _token_values(config: ClientConfig) -> dict[str, str]:
    allow_global = os.environ.get(
        "ARIE_ALLOW_GLOBAL_CONNECTOR_CREDENTIALS", "false"
    ).lower() in {"1", "true", "yes"}
    tokens: dict[str, str] = {}
    if config.meta_enabled and (config.meta_access_token_secret_name or allow_global):
        tokens["meta"] = config.meta_access_token
    if config.google_ads_enabled and (
        config.google_ads_refresh_token_secret_name or allow_global
    ):
        tokens["google_ads"] = config.google_ads_refresh_token
    if config.linkedin_ads_enabled and (
        config.linkedin_access_token_secret_name or allow_global
    ):
        tokens["linkedin_ads"] = config.linkedin_access_token
    if config.tiktok_ads_enabled and (
        config.tiktok_access_token_secret_name or allow_global
    ):
        tokens["tiktok_ads"] = config.tiktok_access_token
    if config.hubspot_enabled and (
        config.hubspot_access_token_secret_name or allow_global
    ):
        tokens["hubspot"] = config.hubspot_access_token
    if config.stripe_enabled and (config.stripe_secret_key_secret_name or allow_global):
        tokens["stripe"] = config.stripe_secret_key
    return tokens


def collect_credential_health_alerts(
    *,
    client_ids: list[str] | None = None,
    run_date: str | None = None,
) -> list[OperatorAlert]:
    reload_client_registry()
    selected_ids = set(client_ids or [])
    run_date = run_date or datetime.now(timezone.utc).date().isoformat()
    alerts: list[OperatorAlert] = []

    for client_id, config in sorted(CLIENT_REGISTRY.items()):
        if selected_ids and client_id not in selected_ids:
            continue
        for alert in build_credential_alerts(config, tokens=_token_values(config)):
            alert.alert_id = _stable_health_alert_id(alert, run_date)
            alerts.append(alert)
    return alerts


def run_health_check(
    *,
    client_ids: list[str] | None = None,
    dispatch: bool = True,
) -> dict:
    alerts = collect_credential_health_alerts(client_ids=client_ids)
    if dispatch:
        dispatch_alerts(alerts)
    summary = {
        "checked_clients": len(client_ids or CLIENT_REGISTRY),
        "alerts": len(alerts),
        "critical_alerts": sum(1 for alert in alerts if alert.severity == "critical"),
        "warning_alerts": sum(1 for alert in alerts if alert.severity == "warning"),
        "dispatched": dispatch,
    }
    logger.info("[OperatorHealth] %s", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", action="append", dest="clients")
    parser.add_argument(
        "--no-dispatch",
        action="store_true",
        help="Preview health checks without writing or sending alerts.",
    )
    args = parser.parse_args()
    dispatch = not args.no_dispatch and os.environ.get(
        "ARIE_OPERATOR_HEALTH_DISPATCH", "true"
    ).lower() not in {"0", "false", "no"}
    print(json.dumps(run_health_check(client_ids=args.clients, dispatch=dispatch)))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
