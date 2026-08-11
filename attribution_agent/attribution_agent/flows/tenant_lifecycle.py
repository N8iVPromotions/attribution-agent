"""Databricks notebook for safe agency and business lifecycle operations.

This module is intentionally self-contained so it can be imported into a
Databricks workspace as a SOURCE notebook. The job is the mutation boundary:
the Command Center never supplies a schema name or arbitrary SQL.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

OPS_SCHEMA = "workspace.attribution_ops"
CATALOG = "workspace"
ALLOWED_COMMANDS = {
    "create_agency",
    "create_business",
    "delete_business",
    "delete_agency",
}
ALLOWED_ATTRIBUTION_MODELS = {
    "last_touch",
    "first_touch",
    "linear",
    "time_decay",
    "u_shape",
    "w_shape",
}
PROTECTED_AGENCY_IDS = {"demo_agency", "n8iv_promotions"}
PROTECTED_BUSINESS_IDS = {"demo_client", "n8iv_promotions"}
SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

SqlExecutor = Callable[[str], list[dict]]


@dataclass(frozen=True)
class LifecycleRequest:
    command: str
    entity_id: str
    request_id: str
    requested_by: str
    entity_name: str = ""
    agency_id: str = ""
    report_email: str = ""
    attribution_model: str = "last_touch"
    confirmation: str = ""
    databricks_run_id: str = ""


def _literal(value: object) -> str:
    return "'" + str(value or "").replace("'", "''") + "'"


def _validate_slug(value: str, label: str) -> str:
    raw = str(value or "").strip()
    normalized = raw.lower()
    if raw != normalized or not SLUG_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{label} must start with a letter and contain only lowercase "
            "letters, numbers, or underscores (maximum 63 characters)"
        )
    return normalized


def business_schema(business_id: str) -> str:
    return f"{CATALOG}.attribution_{_validate_slug(business_id, 'business_id')}"


def agency_schema(agency_id: str) -> str:
    return f"{CATALOG}.agency_{_validate_slug(agency_id, 'agency_id')}"


def _validate_request(request: LifecycleRequest) -> LifecycleRequest:
    command = str(request.command or "").strip().lower()
    if command not in ALLOWED_COMMANDS:
        raise ValueError(f"Unsupported lifecycle command: {command!r}")

    entity_id = _validate_slug(request.entity_id, "entity_id")
    request_id = str(request.request_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", request_id):
        raise ValueError("request_id must be an 8-64 character opaque identifier")

    requested_by = str(request.requested_by or "").strip()[:200]
    if not requested_by:
        raise ValueError("requested_by is required")

    entity_name = str(request.entity_name or "").strip()
    agency_id = str(request.agency_id or "").strip().lower()
    report_email = str(request.report_email or "").strip().lower()
    attribution_model = str(request.attribution_model or "last_touch").strip().lower()

    if command.startswith("create_") and not 2 <= len(entity_name) <= 120:
        raise ValueError("entity_name must be between 2 and 120 characters")
    if command == "create_business":
        agency_id = _validate_slug(agency_id, "agency_id")
        if report_email and not EMAIL_PATTERN.fullmatch(report_email):
            raise ValueError("report_email is not valid")
        if attribution_model not in ALLOWED_ATTRIBUTION_MODELS:
            raise ValueError("Unsupported attribution_model")

    entity_type = "agency" if command.endswith("agency") else "business"
    protected_ids = (
        PROTECTED_AGENCY_IDS if entity_type == "agency" else PROTECTED_BUSINESS_IDS
    )
    if command.startswith("delete_"):
        if entity_id in protected_ids:
            raise ValueError(f"Protected {entity_type} '{entity_id}' cannot be deleted")
        expected = f"DELETE {entity_type.upper()} {entity_id}"
        if request.confirmation != expected:
            raise ValueError(f"Deletion requires exact confirmation: {expected}")

    return LifecycleRequest(
        command=command,
        entity_id=entity_id,
        request_id=request_id,
        requested_by=requested_by,
        entity_name=entity_name,
        agency_id=agency_id,
        report_email=report_email,
        attribution_model=attribution_model,
        confirmation=request.confirmation,
        databricks_run_id=str(request.databricks_run_id or "").strip()[:100],
    )


def _ensure_control_tables(sql: SqlExecutor) -> None:
    sql(f"CREATE SCHEMA IF NOT EXISTS {OPS_SCHEMA}")
    sql(
        f"""CREATE TABLE IF NOT EXISTS {OPS_SCHEMA}.agency_registry (
            agency_id STRING, agency_name STRING, config_json STRING,
            is_active BOOLEAN, created_at TIMESTAMP, updated_at TIMESTAMP
        ) USING DELTA"""
    )
    sql(
        f"""CREATE TABLE IF NOT EXISTS {OPS_SCHEMA}.client_registry (
            client_id STRING, config_json STRING, is_active BOOLEAN,
            updated_at TIMESTAMP
        ) USING DELTA"""
    )
    sql(
        f"""CREATE TABLE IF NOT EXISTS {OPS_SCHEMA}.tenant_lifecycle_operations (
            request_id STRING, command STRING, entity_type STRING,
            entity_id STRING, agency_id STRING, schema_name STRING,
            requested_by STRING, requested_at TIMESTAMP, started_at TIMESTAMP,
            completed_at TIMESTAMP, status STRING, databricks_run_id STRING,
            ddl_statement STRING, error_message STRING, result_json STRING
        ) USING DELTA"""
    )


def _select_one(sql: SqlExecutor, statement: str) -> dict | None:
    rows = sql(statement)
    return rows[0] if rows else None


def _record_started(
    sql: SqlExecutor, request: LifecycleRequest, schema_name: str
) -> None:
    entity_type = "agency" if request.command.endswith("agency") else "business"
    sql(
        f"""MERGE INTO {OPS_SCHEMA}.tenant_lifecycle_operations AS target
        USING (SELECT {_literal(request.request_id)} AS request_id) AS source
        ON target.request_id = source.request_id
        WHEN NOT MATCHED THEN INSERT (
            request_id, command, entity_type, entity_id, agency_id, schema_name,
            requested_by, requested_at, started_at, completed_at, status,
            databricks_run_id, ddl_statement, error_message, result_json
        ) VALUES (
            {_literal(request.request_id)}, {_literal(request.command)},
            {_literal(entity_type)}, {_literal(request.entity_id)},
            {_literal(request.agency_id)}, {_literal(schema_name)},
            {_literal(request.requested_by)}, current_timestamp(), current_timestamp(),
            NULL, 'running', {_literal(request.databricks_run_id)}, '', '', ''
        )"""
    )


def _record_finished(
    sql: SqlExecutor,
    request_id: str,
    *,
    status: str,
    ddl_statement: str = "",
    error_message: str = "",
    result: dict | None = None,
) -> None:
    sql(
        f"""UPDATE {OPS_SCHEMA}.tenant_lifecycle_operations
        SET status = {_literal(status)}, completed_at = current_timestamp(),
            ddl_statement = {_literal(ddl_statement)},
            error_message = {_literal(error_message[:4000])},
            result_json = {_literal(json.dumps(result or {}, sort_keys=True))}
        WHERE request_id = {_literal(request_id)}"""
    )


def _existing_operation(sql: SqlExecutor, request_id: str) -> dict | None:
    return _select_one(
        sql,
        "SELECT request_id, status, result_json "
        f"FROM {OPS_SCHEMA}.tenant_lifecycle_operations "
        f"WHERE request_id = {_literal(request_id)} "
        "ORDER BY requested_at DESC LIMIT 1",
    )


def _active_agency(sql: SqlExecutor, agency_id: str) -> dict | None:
    return _select_one(
        sql,
        "SELECT agency_id, agency_name, config_json "
        f"FROM {OPS_SCHEMA}.agency_registry "
        f"WHERE agency_id = {_literal(agency_id)} AND is_active = TRUE LIMIT 1",
    )


def _active_business(sql: SqlExecutor, business_id: str) -> dict | None:
    return _select_one(
        sql,
        "SELECT client_id, config_json "
        f"FROM {OPS_SCHEMA}.client_registry "
        f"WHERE client_id = {_literal(business_id)} AND is_active = TRUE LIMIT 1",
    )


def _create_agency(sql: SqlExecutor, request: LifecycleRequest) -> tuple[str, dict]:
    if _active_agency(sql, request.entity_id):
        raise ValueError(f"Agency '{request.entity_id}' is already active")
    schema_name = agency_schema(request.entity_id)
    ddl = f"CREATE SCHEMA IF NOT EXISTS {schema_name}"
    sql(ddl)
    config = {
        "agency_id": request.entity_id,
        "agency_name": request.entity_name,
        "client_ids": [],
    }
    config_json = json.dumps(config, sort_keys=True)
    sql(
        f"""MERGE INTO {OPS_SCHEMA}.agency_registry AS target
        USING (SELECT {_literal(request.entity_id)} AS agency_id) AS source
        ON target.agency_id = source.agency_id
        WHEN MATCHED THEN UPDATE SET agency_name = {_literal(request.entity_name)},
            config_json = {_literal(config_json)}, is_active = TRUE,
            updated_at = current_timestamp()
        WHEN NOT MATCHED THEN INSERT (
            agency_id, agency_name, config_json, is_active, created_at, updated_at
        ) VALUES (
            {_literal(request.entity_id)}, {_literal(request.entity_name)},
            {_literal(config_json)}, TRUE, current_timestamp(), current_timestamp()
        )"""
    )
    return ddl, {"agency_id": request.entity_id, "schema_name": schema_name}


def _create_business(sql: SqlExecutor, request: LifecycleRequest) -> tuple[str, dict]:
    if not _active_agency(sql, request.agency_id):
        raise ValueError(f"Agency '{request.agency_id}' is not active")
    if _active_business(sql, request.entity_id):
        raise ValueError(f"Business '{request.entity_id}' is already active")
    schema_name = business_schema(request.entity_id)
    ddl = f"CREATE SCHEMA IF NOT EXISTS {schema_name}"
    sql(ddl)
    config = {
        "client_id": request.entity_id,
        "client_name": request.entity_name,
        "client_display_name": request.entity_name,
        "agency_id": request.agency_id,
        "attribution_model": request.attribution_model,
        "client_report_email": request.report_email,
        "databricks_schema": schema_name,
        "lookback_days": 30,
        "meta_enabled": False,
        "google_ads_enabled": False,
        "linkedin_ads_enabled": False,
        "tiktok_ads_enabled": False,
        "hubspot_enabled": False,
        "stripe_enabled": False,
    }
    config_json = json.dumps(config, sort_keys=True)
    sql(
        f"""MERGE INTO {OPS_SCHEMA}.client_registry AS target
        USING (SELECT {_literal(request.entity_id)} AS client_id) AS source
        ON target.client_id = source.client_id
        WHEN MATCHED THEN UPDATE SET config_json = {_literal(config_json)},
            is_active = TRUE, updated_at = current_timestamp()
        WHEN NOT MATCHED THEN INSERT (
            client_id, config_json, is_active, updated_at
        ) VALUES (
            {_literal(request.entity_id)}, {_literal(config_json)}, TRUE,
            current_timestamp()
        )"""
    )
    return ddl, {
        "business_id": request.entity_id,
        "agency_id": request.agency_id,
        "schema_name": schema_name,
    }


def _delete_business(sql: SqlExecutor, request: LifecycleRequest) -> tuple[str, dict]:
    business = _active_business(sql, request.entity_id)
    if not business:
        raise ValueError(f"Business '{request.entity_id}' is not active")
    config_json = business.get("config_json") or "{}"
    try:
        config = json.loads(config_json)
    except json.JSONDecodeError as exc:
        raise ValueError("Business registry config_json is malformed") from exc
    expected_schema = business_schema(request.entity_id)
    if config.get("databricks_schema") != expected_schema:
        raise ValueError(
            "Registry schema does not match the guarded tenant schema; refusing DROP"
        )
    active_runs = _select_one(
        sql,
        "SELECT COUNT(*) AS active_runs "
        f"FROM {OPS_SCHEMA}.pipeline_runs "
        f"WHERE client_id = {_literal(request.entity_id)} "
        "AND lower(status) IN ('queued', 'running')",
    )
    if active_runs and int(active_runs.get("active_runs") or 0) > 0:
        raise ValueError("Business has an active or queued pipeline run")
    ddl = f"DROP SCHEMA IF EXISTS {expected_schema} CASCADE"
    sql(ddl)
    sql(
        f"""UPDATE {OPS_SCHEMA}.client_registry
        SET is_active = FALSE, updated_at = current_timestamp()
        WHERE client_id = {_literal(request.entity_id)}"""
    )
    return ddl, {"business_id": request.entity_id, "schema_name": expected_schema}


def _delete_agency(sql: SqlExecutor, request: LifecycleRequest) -> tuple[str, dict]:
    if not _active_agency(sql, request.entity_id):
        raise ValueError(f"Agency '{request.entity_id}' is not active")
    active_clients = _select_one(
        sql,
        "SELECT COUNT(*) AS active_clients "
        f"FROM {OPS_SCHEMA}.client_registry "
        "WHERE is_active = TRUE "
        f"AND get_json_object(config_json, '$.agency_id') = {_literal(request.entity_id)}",
    )
    if active_clients and int(active_clients.get("active_clients") or 0) > 0:
        raise ValueError(
            "Agency still has active businesses; delete or move them first"
        )
    schema_name = agency_schema(request.entity_id)
    ddl = f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"
    sql(ddl)
    sql(
        f"""UPDATE {OPS_SCHEMA}.agency_registry
        SET is_active = FALSE, updated_at = current_timestamp()
        WHERE agency_id = {_literal(request.entity_id)}"""
    )
    return ddl, {"agency_id": request.entity_id, "schema_name": schema_name}


def execute_lifecycle(request: LifecycleRequest, sql: SqlExecutor) -> dict:
    request = _validate_request(request)
    _ensure_control_tables(sql)
    existing = _existing_operation(sql, request.request_id)
    if existing:
        status = str(existing.get("status") or "").lower()
        if status == "succeeded":
            try:
                return json.loads(existing.get("result_json") or "{}")
            except json.JSONDecodeError:
                return {"request_id": request.request_id, "status": "succeeded"}
        if status in {"queued", "running"}:
            raise RuntimeError(
                f"Lifecycle request {request.request_id} is already {status}"
            )

    schema_name = (
        agency_schema(request.entity_id)
        if request.command.endswith("agency")
        else business_schema(request.entity_id)
    )
    _record_started(sql, request, schema_name)
    try:
        handlers = {
            "create_agency": _create_agency,
            "create_business": _create_business,
            "delete_business": _delete_business,
            "delete_agency": _delete_agency,
        }
        ddl, result = handlers[request.command](sql, request)
        result.update(
            {
                "request_id": request.request_id,
                "command": request.command,
                "status": "succeeded",
            }
        )
        _record_finished(
            sql,
            request.request_id,
            status="succeeded",
            ddl_statement=ddl,
            result=result,
        )
        return result
    except Exception as exc:
        _record_finished(
            sql,
            request.request_id,
            status="failed",
            error_message=str(exc),
        )
        raise


def _widget(name: str, default: str = "") -> str:
    try:
        return globals()["dbutils"].widgets.get(name)
    except Exception:
        return default


def _spark_sql(statement: str) -> list[dict]:
    rows = globals()["spark"].sql(statement).collect()
    return [row.asDict(recursive=True) for row in rows]


def _run_notebook() -> None:
    request = LifecycleRequest(
        command=_widget("command"),
        entity_id=_widget("entity_id"),
        entity_name=_widget("entity_name"),
        agency_id=_widget("agency_id"),
        report_email=_widget("report_email"),
        attribution_model=_widget("attribution_model", "last_touch"),
        confirmation=_widget("confirmation"),
        request_id=_widget("request_id"),
        requested_by=_widget("requested_by", "arie-command-center"),
        databricks_run_id=_widget("databricks_run_id"),
    )
    result = execute_lifecycle(request, _spark_sql)
    globals()["dbutils"].notebook.exit(json.dumps(result, sort_keys=True))


if "dbutils" in globals() and "spark" in globals():
    _run_notebook()
