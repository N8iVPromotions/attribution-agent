"""Durable human-approval records for external report delivery."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from utils.sql import sql_literal

if TYPE_CHECKING:
    from config.agency_config import AgencyConfig


_OPS_SCHEMA = os.environ.get("ATTRIBUTION_OPS_SCHEMA", "workspace.attribution_ops")
_DELIVERY_TEMPLATE_VERSION = "agency-report-v1"


def report_delivery_config_fingerprint(
    *, recipient_email: str, agency_config: "AgencyConfig"
) -> str:
    """Bind approval to the destination and every comms-facing agency field."""
    recipient = str(recipient_email or "")
    if not recipient.strip():
        raise ValueError("recipient_email is required")
    snapshot = {
        "recipient_email": recipient,
        "agency_id": agency_config.agency_id,
        "agency_name": agency_config.agency_name,
        "brand_color": agency_config.brand_color,
        "brand_logo_url": agency_config.brand_logo_url,
        "sender_name": agency_config.sender_name,
        "sender_email": agency_config.sender_email,
        "reply_to": agency_config.reply_to,
        "powerbi_workspace_url": agency_config.powerbi_workspace_url,
        "effective_sender_email": (
            agency_config.sender_email or os.environ.get("GMAIL_SENDER", "")
        ),
        "comms_provider": os.environ.get("COMMS_PROVIDER", "sendgrid").lower(),
        "template_version": _DELIVERY_TEMPLATE_VERSION,
    }
    encoded = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def report_delivery_approval_id(
    report_id: str, delivery_config_fingerprint: str
) -> str:
    if not report_id or not delivery_config_fingerprint:
        raise ValueError("report_id and delivery_config_fingerprint are required")
    return (
        "report-"
        + uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"arie-report-delivery:{report_id}:{delivery_config_fingerprint}",
        ).hex
    )


def ensure_report_delivery_approval(
    *,
    report_id: str,
    client_id: str,
    agency_id: str,
    report_month: str,
    attribution_model: str,
    recipient_email: str,
    delivery_config_fingerprint: str,
    description: str,
) -> str:
    """Insert a pending approval once without resetting an existing decision."""
    from utils.databricks_writer import ensure_approval_queue_table, _run_sql

    action_id = report_delivery_approval_id(report_id, delivery_config_fingerprint)
    payload = json.dumps(
        {
            "report_id": report_id,
            "client_id": client_id,
            "agency_id": agency_id,
            "report_month": report_month,
            "attribution_model": attribution_model,
            "recipient_email": str(recipient_email),
            "delivery_config_fingerprint": delivery_config_fingerprint,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    created_at = datetime.now(timezone.utc).isoformat()
    ensure_approval_queue_table()
    _run_sql(
        f"""
        MERGE INTO {_OPS_SCHEMA}.approval_queue AS target
        USING (
            SELECT
                {sql_literal(action_id)} AS action_id,
                CAST({sql_literal(created_at)} AS TIMESTAMP) AS created_at,
                'attribution_pipeline' AS actor,
                {sql_literal(description)} AS description,
                'report_delivery' AS action_type,
                {sql_literal(payload)} AS payload_json,
                'pending' AS status,
                CAST(NULL AS TIMESTAMP) AS resolved_at,
                '' AS resolved_by,
                '' AS resolution_note,
                'command_center' AS channel
        ) AS source
        ON target.action_id = source.action_id
        WHEN NOT MATCHED THEN INSERT (
            action_id, created_at, actor, description, action_type, payload_json,
            status, resolved_at, resolved_by, resolution_note, channel
        ) VALUES (
            source.action_id, source.created_at, source.actor, source.description,
            source.action_type, source.payload_json, source.status,
            source.resolved_at, source.resolved_by, source.resolution_note,
            source.channel
        )
        """
    )
    return action_id


def _fetch_one(query: str) -> dict | None:
    from utils.databricks_writer import _get_connection, _get_spark, _is_databricks

    if _is_databricks():
        rows = _get_spark().sql(query).collect()
        return rows[0].asDict() if rows else None
    connection = _get_connection()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(query)
            row = cursor.fetchone()
            if row is None:
                return None
            columns = [description[0] for description in cursor.description]
            return dict(zip(columns, row))
        finally:
            cursor.close()
    finally:
        connection.close()


def report_delivery_approval_status(
    report_id: str, *, delivery_config_fingerprint: str
) -> str | None:
    from utils.databricks_writer import ensure_approval_queue_table

    ensure_approval_queue_table()
    action_id = report_delivery_approval_id(report_id, delivery_config_fingerprint)
    row = _fetch_one(
        f"SELECT status FROM {_OPS_SCHEMA}.approval_queue "
        f"WHERE action_id = {sql_literal(action_id)} "
        "AND get_json_object(payload_json, '$.delivery_config_fingerprint') = "
        f"{sql_literal(delivery_config_fingerprint)} LIMIT 1"
    )
    return str(row["status"]).lower() if row and row.get("status") else None


def fetch_latest_approved_report(
    *,
    client_id: str,
    agency_id: str,
    report_month: str,
    attribution_model: str,
    data_version: str,
    delivery_config_fingerprint: str,
) -> dict | None:
    """Return the exact approved, not-yet-delivered report artifact."""
    from utils.databricks_writer import (
        ensure_approval_queue_table,
        ensure_insight_reports_table,
    )

    ensure_insight_reports_table()
    ensure_approval_queue_table()
    if not data_version:
        return None
    return _fetch_one(
        f"""
        SELECT report.*
        FROM {_OPS_SCHEMA}.insight_reports AS report
        INNER JOIN {_OPS_SCHEMA}.approval_queue AS approval
            ON get_json_object(approval.payload_json, '$.report_id') = report.report_id
        WHERE report.client_id = {sql_literal(client_id)}
          AND report.agency_id = {sql_literal(agency_id)}
          AND report.report_month = {sql_literal(report_month)}
          AND report.attribution_model = {sql_literal(attribution_model)}
          AND report.data_version = {sql_literal(data_version)}
          AND report.status = 'generated'
          AND approval.action_type = 'report_delivery'
          AND approval.status = 'approved'
          AND get_json_object(
                approval.payload_json,
                '$.delivery_config_fingerprint'
              ) = {sql_literal(delivery_config_fingerprint)}
        ORDER BY approval.resolved_at DESC, report.generated_at DESC
        LIMIT 1
        """
    )
