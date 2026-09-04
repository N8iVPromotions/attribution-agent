"""
flows/agency_flow.py
---------------------
Runs the full attribution pipeline for every client in an agency.
White-labeled reports are sent under agency branding.

Run standalone:
    python flows/agency_flow.py --agency demo_agency
    python flows/agency_flow.py --agency demo_agency --dry-run
    python flows/agency_flow.py --agency demo_agency --client-filter demo_client

Or import and call run_agency_pipeline(agency_id) from a scheduler.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

try:
    _root = str(Path(__file__).parent.parent)
except NameError:
    import inspect as _inspect

    _root = str(Path(_inspect.getfile(_inspect.currentframe())).parent.parent)
sys.path.insert(0, _root)

from config.agency_config import AgencyConfig, get_agency, list_agencies
from config.client_config import (
    get_client,
    list_clients,
    normalize_hubspot_closed_won_stage_ids,
)
from flows.ingest_flow import ingest_flow
from agents.insight.insight_agent import (
    InsightReport,
    fetch_report_data_fingerprint,
    generate_insight_report,
)
from agents.comms.comms_agent import (
    _AGENCY_FLOW_DELIVERY_AUTHORIZATION,
    DeliveryNotAcceptedError,
    send_agency_report,
)
from attribution_models import normalize_model
from utils.databricks_writer import (
    _run_sql,
    insight_report_id,
    update_insight_report_status,
    write_insight_report,
    write_pipeline_run,
)
from utils.idempotency import claim_report_delivery, report_delivery_key
from utils.operator_alerts import OperatorAlert, dispatch_alerts
from utils.report_period import ReportPeriod, resolve_report_period
from utils.report_approval import (
    ensure_report_delivery_approval,
    fetch_latest_approved_report,
    report_delivery_config_fingerprint,
    report_delivery_approval_status,
)

try:
    from agents.control import arie_bot as _arie
except Exception:
    _arie = None


_GOVERNANCE_READY_DECISION = "READY FOR HUMAN REVIEW"
_WAREHOUSE_ATTRIBUTION_MODEL = "last_touch"
_DUPLICATE_COLUMN_ERROR_MARKERS = (
    "already exists",
    "column_already_exists",
    "field_already_exists",
    "fields_already_exists",
    "duplicate column",
)
_REPORT_CHECKPOINT_REQUIRED_FIELDS = {
    "client_id",
    "client_name",
    "report_month",
    "narrative",
    "attribution_model",
}


def _notify(text: str) -> None:
    if _arie:
        try:
            _arie.notify(text)
        except Exception:
            pass


def _get_client_recipient(client_id: str) -> str:
    """Look up the report recipient email from the client's config."""
    config = get_client(client_id)
    if not config.client_report_email:
        raise ValueError(
            f"No client_report_email set for client '{client_id}'. "
            "Add it to CLIENT_REGISTRY in config/client_config.py."
        )
    return config.client_report_email


def _partial_suppression_alert(
    *, client_id: str, agency_id: str, run_id: str, source_failures: dict
) -> OperatorAlert:
    event_code = "PARTIAL_INGESTION_REPORT_SUPPRESSED"
    return OperatorAlert(
        severity="warning",
        category="partial_ingestion_report_suppressed",
        title=event_code,
        message=(
            f"{event_code}: report delivery blocked after partial ingestion for "
            f"client {client_id}"
        ),
        client_id=client_id,
        agency_id=agency_id,
        run_id=run_id,
        action_required="Resolve failed sources and rerun before report delivery.",
        metadata={"event_code": event_code, "source_failures": source_failures},
    )


def _report_from_checkpoint(
    payload: object,
    *,
    agency_id: str,
    expected_client_id: str,
    expected_model: str,
    expected_month: str,
) -> tuple[InsightReport, str] | None:
    """Restore only checkpoints that contain a complete, deliverable report."""
    if not isinstance(payload, dict):
        return None
    report_payload = payload.get("report", payload)
    if not isinstance(report_payload, dict):
        return None
    if not _REPORT_CHECKPOINT_REQUIRED_FIELDS.issubset(report_payload):
        return None
    try:
        report = InsightReport(
            **{
                key: value
                for key, value in report_payload.items()
                if key in InsightReport.__dataclass_fields__
            }
        )
    except (TypeError, ValueError):
        return None
    if (
        report.client_id != expected_client_id
        or report.attribution_model != expected_model
        or report.report_month != expected_month
    ):
        raise RuntimeError(
            "Stored report checkpoint does not match the requested client, "
            "attribution model, and report month. Start a new run."
        )
    if not _report_has_attribution_data(report):
        return None
    computed_report_id = insight_report_id({**report.to_dict(), "agency_id": agency_id})
    stored_report_id = str(payload.get("report_id") or "")
    if stored_report_id and stored_report_id != computed_report_id:
        raise RuntimeError(
            "Stored report checkpoint content does not match its report ID. "
            "Start a new run."
        )
    return report, computed_report_id


def _report_checkpoint_payload(report: InsightReport, report_id: str) -> dict:
    return {"report": report.to_dict(), "report_id": report_id}


def _report_has_attribution_data(report: InsightReport) -> bool:
    return bool(
        report.report_month
        and report.report_month != "N/A"
        and (report.total_pipeline > 0 or report.collected_revenue > 0)
    )


def _load_or_generate_report(
    *,
    checkpointer,
    completed_steps: set[str],
    run_id: str,
    agency_id: str,
    client_id: str,
    attribution_model: str,
    report_month: str,
) -> tuple[InsightReport, str]:
    restored = None
    if "generate_report" in completed_steps:
        logger.info(f"[Agency] Resuming — restoring generated report for {client_id}")
        restored = _report_from_checkpoint(
            checkpointer.get_step_result(run_id, client_id, "generate_report"),
            agency_id=agency_id,
            expected_client_id=client_id,
            expected_model=attribution_model,
            expected_month=report_month,
        )
        if restored is None:
            logger.warning(
                "[Agency] Legacy or invalid report checkpoint; regenerating | "
                "run=%s client=%s",
                run_id,
                client_id,
            )

    if restored is None:
        checkpointer.start_step(run_id, agency_id, client_id, "generate_report")
        report = generate_insight_report(
            client_id,
            attribution_model=attribution_model,
            run_id=run_id,
            report_month=report_month,
        )
        if not _report_has_attribution_data(report):
            raise RuntimeError(
                "Report generation produced no attribution data; delivery blocked."
            )
        report_id = insight_report_id({**report.to_dict(), "agency_id": agency_id})
        checkpointer.complete_step(
            run_id,
            agency_id,
            client_id,
            "generate_report",
            _report_checkpoint_payload(report, report_id),
        )
        restored = (report, report_id)
    return restored


def _governance_blocks_live_delivery(review: dict, *, dry_run: bool) -> bool:
    """Fail closed unless governance says the human-approved run is review-ready."""
    if dry_run:
        return False
    return bool(
        review.get("review_failed")
        or review.get("critical_issues")
        or review.get("decision") != _GOVERNANCE_READY_DECISION
    )


def _governance_block_reasons(review: dict) -> list[str]:
    reasons = list(review.get("critical_issues") or [])
    if not reasons:
        reasons = list(review.get("warnings") or [])
    if not reasons:
        reasons = [f"Governance decision: {review.get('decision', 'UNKNOWN')}"]
    return reasons


def _utc_sql_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _hubspot_closed_won_stage_sql(values: tuple[str, ...]) -> str:
    """Render validated exact stage keys as SQL string literals."""
    stage_keys = normalize_hubspot_closed_won_stage_ids(values)
    return ", ".join(f"'{key.replace(chr(39), chr(39) * 2)}'" for key in stage_keys)


def _period_from_ingest_checkpoint(
    payload: object,
    *,
    client_id: str,
    attribution_model: str,
    dry_run: bool,
    requested_month: str | None,
) -> ReportPeriod:
    if not isinstance(payload, dict):
        raise RuntimeError("Ingest checkpoint is missing its run contract.")
    required = {
        "client_id",
        "attribution_model",
        "report_month",
        "period_start",
        "period_end",
        "dry_run",
    }
    if not required.issubset(payload):
        raise RuntimeError(
            "Ingest checkpoint lacks its model, period, client, or delivery mode; "
            "start a new run."
        )
    period = resolve_report_period(str(payload["report_month"]))
    mismatch = bool(
        str(payload["client_id"]) != client_id
        or str(payload["attribution_model"]) != attribution_model
        or bool(payload["dry_run"]) != dry_run
        or str(payload["period_start"]) != period.start.isoformat()
        or str(payload["period_end"]) != period.end.isoformat()
        or (requested_month is not None and requested_month != period.month)
    )
    if mismatch:
        raise RuntimeError(
            "Ingest checkpoint does not match the requested client, attribution "
            "model, report period, or delivery mode. Start a new run."
        )
    return period


def _report_from_persisted_row(
    row: dict,
    *,
    agency_id: str,
    client_id: str,
    attribution_model: str,
    report_month: str,
) -> tuple[InsightReport, str]:
    key_findings = row.get("key_findings") or []
    if isinstance(key_findings, str):
        try:
            key_findings = json.loads(key_findings)
        except json.JSONDecodeError:
            key_findings = [key_findings] if key_findings else []
    payload = {
        "client_id": row.get("client_id"),
        "client_name": row.get("client_name"),
        "report_month": row.get("report_month"),
        "narrative": row.get("narrative") or "",
        "key_findings": key_findings,
        "top_channel": row.get("top_channel") or "",
        "total_pipeline": float(row.get("total_pipeline") or 0.0),
        "total_spend": float(row.get("total_spend") or 0.0),
        "overall_roi": float(row.get("overall_roi") or 0.0),
        "collected_revenue": float(row.get("collected_revenue") or 0.0),
        "refund_rate": float(row.get("refund_rate") or 0.0),
        "true_roi": float(row.get("true_roi") or 0.0),
        "attribution_model": row.get("attribution_model"),
        "data_version": row.get("data_version") or "",
        "generated_at": str(row.get("generated_at") or ""),
    }
    restored = _report_from_checkpoint(
        {"report": payload, "report_id": row.get("report_id")},
        agency_id=agency_id,
        expected_client_id=client_id,
        expected_model=attribution_model,
        expected_month=report_month,
    )
    if restored is None:
        raise RuntimeError("Approved report artifact contains no attributed revenue.")
    if str(row.get("agency_id") or "") != agency_id:
        raise RuntimeError("Approved report artifact belongs to another agency.")
    return restored


def _report_outcome_status(
    *,
    delivery_policy_clear: bool,
    delivery_proven: bool,
) -> str:
    if delivery_proven:
        return "delivered"
    if not delivery_policy_clear:
        return "suppressed"
    return "generated"


def _delivery_was_proven(checkpoint: object) -> bool:
    return bool(isinstance(checkpoint, dict) and checkpoint.get("delivered") is True)


def _build_union_all(agency: AgencyConfig) -> str:
    """Generate the UNION ALL clause across all client schemas for SQL templates."""
    clauses = []
    for client_id in _agency_client_ids(agency):
        config = get_client(client_id)
        client_name = config.client_display_name or config.client_name
        schema = config.databricks_schema
        clauses.append(
            f"SELECT '{client_id}' AS client_id, '{client_name}' AS client_name, * "
            f"FROM {schema}.channel_performance"
        )
    return "\nUNION ALL\n".join(clauses)


def _agency_client_ids(agency: AgencyConfig) -> list[str]:
    client_ids = list(agency.client_ids)
    for client_id in list_clients():
        config = get_client(client_id)
        if config.agency_id == agency.agency_id and client_id not in client_ids:
            client_ids.append(client_id)
    return client_ids


def run_agency_benchmark_sql(agency: AgencyConfig, *, fail_fast: bool = False) -> None:
    """Run the agency_benchmark and agency_dashboard SQL transforms."""
    try:
        union_all_clause = _build_union_all(agency)
        # _root, not __file__: serverless runs this script without __file__ set
        sql_dir = Path(_root) / "transforms"

        for sql_file in ("agency_dashboard.sql", "agency_benchmark.sql"):
            path = sql_dir / sql_file
            if not path.exists():
                logger.warning(f"[Agency] SQL file not found: {sql_file}")
                continue
            sql = path.read_text()
            # Execute each statement separately (split on semicolons)
            for stmt in sql.format(
                agency_id=agency.agency_id,
                union_all_clause=union_all_clause,
            ).split(";"):
                stmt = stmt.strip()
                if stmt:
                    _run_sql(stmt)
            logger.info(f"[Agency] Executed {sql_file}")
    except Exception as exc:
        if fail_fast:
            raise
        logger.warning(f"[Agency] Benchmark SQL failed (non-fatal): {exc}")


def _ensure_channel_performance_v2_schema(schema: str) -> None:
    """Apply additive migrations needed by the cash-revenue scorecard."""
    try:
        _run_sql(
            f"ALTER TABLE {schema}.channel_performance_v2 "
            "ADD COLUMNS (refunded_revenue DOUBLE)"
        )
    except Exception as exc:
        error = str(exc).lower()
        if not any(marker in error for marker in _DUPLICATE_COLUMN_ERROR_MARKERS):
            raise
        logger.debug(
            f"[Databricks] {schema}.channel_performance_v2.refunded_revenue "
            "already exists"
        )


def run_client_attribution_sql(
    client_id: str,
    attribution_model: str,
    *,
    run_started_at: datetime,
    report_period: ReportPeriod,
) -> None:
    """Refresh model-specific closed-revenue attribution tables for a client."""
    selected_model = normalize_model(attribution_model)
    if selected_model != _WAREHOUSE_ATTRIBUTION_MODEL:
        raise ValueError(
            "The production warehouse currently supports only the single-source "
            "last_touch model; observed multi-touch evidence is not available."
        )
    config = get_client(client_id)
    sql_path = Path(_root) / "transforms" / "closed_revenue_attribution.sql"
    sql = sql_path.read_text()
    schema = config.databricks_schema
    cash_scorecard_insert = f"INSERT INTO {schema}.channel_performance_v2"
    for stmt in sql.format(
        schema=schema,
        attribution_model=selected_model,
        lookback_days=int(config.lookback_days),
        run_started_at=_utc_sql_timestamp(run_started_at),
        report_month=report_period.month,
        period_start=report_period.start.isoformat(),
        period_end=report_period.end.isoformat(),
        hubspot_closed_won_stage_keys=_hubspot_closed_won_stage_sql(
            config.hubspot_closed_won_stage_ids
        ),
        reporting_currency=config.reporting_currency,
        report_timezone=report_period.timezone_name,
    ).split(";"):
        stmt = stmt.strip()
        if stmt:
            if stmt.startswith(cash_scorecard_insert):
                _ensure_channel_performance_v2_schema(schema)
            _run_sql(stmt)
    logger.info(
        f"[Attribution] Refreshed closed-revenue tables | "
        f"client={client_id} | model={attribution_model}"
    )


def run_agency_pipeline(
    agency_id: str,
    dry_run: bool = False,
    client_filter: list[str] | None = None,
    attribution_model: str | None = None,
    run_mode: str = "agency",
    resume_run_id: str | None = None,
    execution_run_id: str | None = None,
    run_benchmarks: bool = True,
    report_month: str | None = None,
) -> dict:
    """
    Run the full pipeline for every client in the agency:
    1. ingest_flow (pulls Meta, HubSpot, Stripe → Databricks)
    2. generate_insight_report (Claude narrative)
    3. send white-labeled email (skipped if dry_run=True)

    Runs clients sequentially to avoid API rate limits.
    Pass resume_run_id to resume from the last completed checkpoint.
    """
    from flows.saga import PipelineSaga
    from utils.checkpoint import Checkpointer
    from utils.client_lock import ClientLeaseHeld, client_lease

    agency = get_agency(agency_id)
    client_ids = client_filter or _agency_client_ids(agency)
    selected_model = normalize_model(attribution_model or "last_touch")
    if selected_model != _WAREHOUSE_ATTRIBUTION_MODEL:
        raise ValueError(
            "The production warehouse currently supports only last_touch. "
            "First-touch and multi-touch models require observed touchpoint data."
        )
    if resume_run_id and report_month is None:
        raise ValueError(
            "report_month is required when resuming a run so its completed-period "
            "contract cannot drift."
        )
    requested_period = resolve_report_period(report_month)
    run_id = resume_run_id or execution_run_id or str(uuid.uuid4())
    checkpointer = Checkpointer()

    if resume_run_id:
        logger.info(f"[Agency] Resuming run {resume_run_id}")

    logger.info(
        f"[Agency] Starting pipeline | agency={agency_id} | "
        f"clients={client_ids} | dry_run={dry_run} | model={selected_model}"
    )
    _notify(
        f"⚡ *Pipeline started*\n"
        f"Agency: `{agency_id}` · {len(client_ids)} client{'s' if len(client_ids) != 1 else ''}\n"
        f"Model: `{selected_model}` · {'Dry run' if dry_run else 'Live'}"
    )

    results = []
    errors = []

    for client_id in client_ids:
        started_at = datetime.now(timezone.utc)
        logger.info(f"[Agency] Processing client: {client_id}")

        # Resume: skip clients already completed in a prior run
        completed_steps = checkpointer.get_completed_steps(run_id, client_id)
        if "pipeline_complete" in completed_steps:
            completed_ingest = checkpointer.get_step_result(run_id, client_id, "ingest")
            _period_from_ingest_checkpoint(
                completed_ingest,
                client_id=client_id,
                attribution_model=selected_model,
                dry_run=dry_run,
                requested_month=report_month,
            )
            logger.info(
                f"[Agency] Skipping {client_id} — already completed in run {run_id}"
            )
            continue

        lease = client_lease(client_id, run_id)
        try:
            lease.acquire()
        except ClientLeaseHeld as exc:
            logger.warning("[Agency] %s", exc)
            errors.append({"client_id": client_id, "error": str(exc)})
            continue

        with (
            lease,
            PipelineSaga(
                run_id=run_id,
                client_id=client_id,
                agency_id=agency_id,
            ),
        ):
            try:
                checkpointer.start_step(
                    run_id, agency_id, client_id, "pipeline_complete"
                )
                # 1. Ingest
                if "ingest" not in completed_steps:
                    checkpointer.start_step(run_id, agency_id, client_id, "ingest")
                    ingest_result = ingest_flow(
                        client_id,
                        run_id=run_id,
                        attribution_model=selected_model,
                        report_month=requested_period.month,
                        require_live_mode=not dry_run,
                    )
                    ingest_result = {
                        **ingest_result,
                        "ingest_started_at": started_at.isoformat(),
                        "client_id": client_id,
                        "attribution_model": selected_model,
                        "report_month": requested_period.month,
                        "period_start": requested_period.start.isoformat(),
                        "period_end": requested_period.end.isoformat(),
                        "dry_run": dry_run,
                    }
                    checkpointer.complete_step(
                        run_id, agency_id, client_id, "ingest", ingest_result
                    )
                else:
                    logger.info(f"[Agency] Resuming — skipping ingest for {client_id}")
                    ingest_result = checkpointer.get_step_result(
                        run_id, client_id, "ingest"
                    )

                client_period = _period_from_ingest_checkpoint(
                    ingest_result,
                    client_id=client_id,
                    attribution_model=selected_model,
                    dry_run=dry_run,
                    requested_month=report_month,
                )
                if report_month is None:
                    requested_period = client_period

                ingest_status = ingest_result.get("status", "complete")
                source_failures = ingest_result.get("source_failures", {})

                # 2. Refresh attribution outputs for the selected model
                if "attribution_sql" not in completed_steps:
                    ingest_started_at = ingest_result.get("ingest_started_at")
                    if not ingest_started_at:
                        raise RuntimeError(
                            "Ingest checkpoint lacks its freshness boundary; "
                            "start a new run before refreshing attribution."
                        )
                    checkpointer.start_step(
                        run_id, agency_id, client_id, "attribution_sql"
                    )
                    run_client_attribution_sql(
                        client_id,
                        selected_model,
                        run_started_at=datetime.fromisoformat(ingest_started_at),
                        report_period=client_period,
                    )
                    checkpointer.complete_step(
                        run_id, agency_id, client_id, "attribution_sql"
                    )

                # 3. Generate a draft, or reuse the exact artifact a human approved.
                recipient = _get_client_recipient(client_id)
                delivery_config_fingerprint = report_delivery_config_fingerprint(
                    recipient_email=recipient,
                    agency_config=agency,
                )
                approved_artifact = None
                if not dry_run and "generate_report" not in completed_steps:
                    current_data_version = fetch_report_data_fingerprint(
                        client_id,
                        attribution_model=selected_model,
                        report_month=client_period.month,
                    )
                    approved_artifact = fetch_latest_approved_report(
                        client_id=client_id,
                        agency_id=agency_id,
                        report_month=client_period.month,
                        attribution_model=selected_model,
                        data_version=current_data_version,
                        delivery_config_fingerprint=delivery_config_fingerprint,
                    )
                if approved_artifact:
                    report, report_id = _report_from_persisted_row(
                        approved_artifact,
                        agency_id=agency_id,
                        client_id=client_id,
                        attribution_model=selected_model,
                        report_month=client_period.month,
                    )
                    checkpointer.start_step(
                        run_id, agency_id, client_id, "generate_report"
                    )
                    checkpointer.complete_step(
                        run_id,
                        agency_id,
                        client_id,
                        "generate_report",
                        _report_checkpoint_payload(report, report_id),
                    )
                else:
                    report, report_id = _load_or_generate_report(
                        checkpointer=checkpointer,
                        completed_steps=completed_steps,
                        run_id=run_id,
                        agency_id=agency_id,
                        client_id=client_id,
                        attribution_model=selected_model,
                        report_month=client_period.month,
                    )
                report_record = {
                    **report.to_dict(),
                    "agency_id": agency_id,
                    "run_id": run_id,
                }
                delivery_checkpoint = (
                    checkpointer.get_step_result(run_id, client_id, "email_sent")
                    if "email_sent" in completed_steps
                    else {}
                )
                delivery_proven = _delivery_was_proven(delivery_checkpoint)
                persisted_report_id = write_insight_report(
                    {**report_record, "status": "generated"}
                )
                if persisted_report_id != report_id:
                    raise RuntimeError(
                        "Persisted report ID does not match the checkpointed artifact."
                    )

                # 3b. Governance review (critical findings block live delivery)
                governance_review = {
                    "decision": "NOT RUN",
                    "warnings": [],
                    "critical_issues": [],
                    "review_failed": False,
                }
                try:
                    from agents.intelligence.n8iv_agents import run_governance_review

                    governance_payload = report.to_dict()
                    governance_payload.pop("generated_at", None)
                    governance_review = run_governance_review(
                        client_id=client_id,
                        client_name=report.client_name,
                        report_narrative=report.narrative,
                        report_json=governance_payload,
                        agency_id=agency_id,
                        run_id=run_id,
                        data_version=report.data_version,
                    )
                    gov_findings = (
                        governance_review["warnings"]
                        + governance_review["critical_issues"]
                    )
                    if gov_findings:
                        logger.warning(
                            f"[Governance] {len(gov_findings)} item(s) for "
                            f"{client_id}: " + "; ".join(gov_findings[:3])
                        )
                        dispatch_alerts(
                            [
                                OperatorAlert(
                                    severity="warning",
                                    category="report_governance",
                                    title="Report governance warning",
                                    message="; ".join(gov_findings[:5]),
                                    client_id=client_id,
                                    agency_id=agency_id,
                                    run_id=run_id,
                                    action_required=(
                                        "Review the report before sending or "
                                        "sharing with the client."
                                    ),
                                    metadata={
                                        "warning_count": len(
                                            governance_review["warnings"]
                                        ),
                                        "critical_count": len(
                                            governance_review["critical_issues"]
                                        ),
                                    },
                                )
                            ]
                        )
                except Exception as gov_exc:
                    logger.warning(
                        f"[Governance] review failed (non-fatal): {gov_exc!r}"
                    )
                    governance_review = {
                        "decision": "REVIEW UNAVAILABLE",
                        "warnings": [],
                        "critical_issues": [
                            "Automated governance review was unavailable."
                        ],
                        "review_failed": True,
                    }

                # 4. Send white-labeled email
                email_sent = False
                delivery_idempotent_skip = False
                allow_partial_delivery = (
                    os.environ.get(
                        "ARIE_ALLOW_PARTIAL_REPORT_DELIVERY", "false"
                    ).lower()
                    == "true"
                )
                governance_blocks_delivery = _governance_blocks_live_delivery(
                    governance_review, dry_run=False
                )
                delivery_policy_clear = (
                    ingest_status != "partial" or allow_partial_delivery
                ) and not governance_blocks_delivery
                approval_status = None
                if delivery_policy_clear and not delivery_proven:
                    approval_status = report_delivery_approval_status(
                        report_id,
                        delivery_config_fingerprint=delivery_config_fingerprint,
                    )
                    if approval_status is None:
                        approval_id = ensure_report_delivery_approval(
                            report_id=report_id,
                            client_id=client_id,
                            agency_id=agency_id,
                            report_month=report.report_month,
                            attribution_model=selected_model,
                            recipient_email=recipient,
                            delivery_config_fingerprint=delivery_config_fingerprint,
                            description=(
                                f"Approve external delivery of "
                                f"{report.client_name} {report.report_month} report "
                                f"({selected_model}; attributed pipeline "
                                f"${report.total_pipeline:,.2f}) to {recipient}."
                            ),
                        )
                        approval_status = report_delivery_approval_status(
                            report_id,
                            delivery_config_fingerprint=delivery_config_fingerprint,
                        )
                        logger.info(
                            "[Agency] Report approval %s is %s",
                            approval_id,
                            approval_status,
                        )
                    if approval_status not in {"pending", "approved", "rejected"}:
                        raise RuntimeError(
                            "Unable to establish a durable report approval state"
                        )
                delivery_authorized = bool(
                    delivery_proven or approval_status == "approved"
                )
                if not delivery_policy_clear:
                    if governance_blocks_delivery:
                        governance_reasons = _governance_block_reasons(
                            governance_review
                        )
                        suppression_alert = OperatorAlert(
                            severity="critical",
                            category="report_governance_suppressed",
                            title="REPORT_GOVERNANCE_DELIVERY_SUPPRESSED",
                            message="; ".join(governance_reasons[:5]),
                            client_id=client_id,
                            agency_id=agency_id,
                            run_id=run_id,
                            action_required=(
                                "Review and approve a corrected report before delivery."
                            ),
                        )
                    else:
                        suppression_alert = _partial_suppression_alert(
                            client_id=client_id,
                            agency_id=agency_id,
                            run_id=run_id,
                            source_failures=source_failures,
                        )
                    logger.warning(
                        "%s | client=%s | failed_sources=%s",
                        suppression_alert.title,
                        client_id,
                        ",".join(source_failures),
                    )
                    dispatch_alerts([suppression_alert])
                elif not dry_run and not delivery_authorized:
                    logger.info(
                        "[Agency] External delivery awaits human approval | "
                        "client=%s report=%s",
                        client_id,
                        report_id,
                    )
                elif not dry_run and "email_sent" not in completed_steps:
                    delivery_key = report_delivery_key(
                        client_id,
                        report.report_month,
                        selected_model,
                        report_id=report_id,
                        delivery_config_fingerprint=delivery_config_fingerprint,
                    )
                    claim = claim_report_delivery(delivery_key)
                    checkpointer.start_step(run_id, agency_id, client_id, "email_sent")
                    if not claim.acquired:
                        if claim.state != "sent":
                            raise RuntimeError(
                                "A prior delivery attempt is unresolved; delivery "
                                "cannot be retried safely until an operator verifies it."
                            )
                        logger.info(
                            "[Agency] Previously sent report delivery confirmed | key=%s",
                            delivery_key,
                        )
                        checkpointer.complete_step(
                            run_id,
                            agency_id,
                            client_id,
                            "email_sent",
                            {
                                "delivered": True,
                                "reason": "existing_sent_claim",
                                "delivery_key": delivery_key,
                            },
                        )
                        delivery_proven = True
                        delivery_idempotent_skip = True
                    else:
                        try:
                            email_sent = send_agency_report(
                                report=report,
                                recipient_email=recipient,
                                agency_config=agency,
                                powerbi_url=agency.powerbi_workspace_url,
                                delivery_authorization=(
                                    _AGENCY_FLOW_DELIVERY_AUTHORIZATION
                                ),
                            )
                            if not email_sent:
                                raise DeliveryNotAcceptedError(
                                    "Report delivery provider returned an unsuccessful result."
                                )
                        except DeliveryNotAcceptedError:
                            claim.release()
                            raise
                        if email_sent:
                            delivery_proven = True
                            try:
                                claim.complete()
                            except Exception as claim_exc:
                                logger.warning(
                                    "[Agency] Email sent but claim finalization failed; "
                                    "the retained claim still prevents a duplicate | "
                                    "key=%s error=%r",
                                    delivery_key,
                                    claim_exc,
                                )
                            checkpointer.complete_step(
                                run_id,
                                agency_id,
                                client_id,
                                "email_sent",
                                {
                                    "delivered": True,
                                    "delivery_key": delivery_key,
                                },
                            )
                            logger.info(
                                "[Agency] Report sent to %s | key=%s",
                                recipient,
                                delivery_key,
                            )
                elif dry_run:
                    logger.info(f"[Agency] dry_run — skipping email for {client_id}")

                report_status = _report_outcome_status(
                    delivery_policy_clear=delivery_policy_clear,
                    delivery_proven=delivery_proven,
                )
                if approval_status == "rejected" and not delivery_proven:
                    report_status = "suppressed"
                update_insight_report_status(report_id, report_status)

                # Write pipeline summary to cross-run memory
                try:
                    from utils.memory_store import MemoryStore

                    MemoryStore().remember(
                        client_id=client_id,
                        memory_type="pipeline_summary",
                        content=(
                            f"Pipeline run {run_id[:8]}: "
                            f"top_channel={report.top_channel}, "
                            f"pipeline=${report.total_pipeline:,.0f}, "
                            f"model={selected_model}"
                        ),
                        source_run_id=run_id,
                        importance="medium",
                    )
                except Exception:
                    pass

                if email_sent:
                    delivery_status = "📧 Report sent"
                elif delivery_proven:
                    delivery_status = "📧 Delivery previously completed"
                elif not delivery_policy_clear:
                    delivery_status = "⛔ Delivery blocked — source or governance gate"
                elif dry_run:
                    delivery_status = "🔕 Dry run — email skipped"
                elif not delivery_authorized:
                    delivery_status = (
                        "⛔ Human approval rejected"
                        if approval_status == "rejected"
                        else "⏳ Awaiting human approval"
                    )
                elif delivery_idempotent_skip:
                    delivery_status = "Duplicate delivery suppressed"
                elif "email_sent" in completed_steps:
                    delivery_status = "⚠️ Prior delivery was not proven"
                else:
                    delivery_status = "⚠️ Report email was not sent"

                awaiting_approval = bool(
                    not dry_run
                    and delivery_policy_clear
                    and approval_status == "pending"
                    and not delivery_proven
                )
                pipeline_status = (
                    "awaiting_approval"
                    if awaiting_approval
                    else (
                        "partial"
                        if ingest_status == "partial"
                        or not delivery_policy_clear
                        or approval_status == "rejected"
                        else "success"
                    )
                )
                write_pipeline_run(
                    {
                        "run_id": run_id,
                        "agency_id": agency_id,
                        "client_id": client_id,
                        "run_mode": run_mode,
                        "attribution_model": selected_model,
                        "status": pipeline_status,
                        "dry_run": dry_run,
                        "meta_rows": ingest_result.get("meta_rows", 0),
                        "google_rows": ingest_result.get("google_rows", 0),
                        "linkedin_rows": ingest_result.get("linkedin_rows", 0),
                        "tiktok_rows": ingest_result.get("tiktok_rows", 0),
                        "hubspot_rows": ingest_result.get("hubspot_rows", 0),
                        "stripe_rows": ingest_result.get("stripe_rows", 0),
                        "normalized_ad_rows": ingest_result.get(
                            "normalized_ad_rows", 0
                        ),
                        "total_pipeline": report.total_pipeline,
                        "top_channel": report.top_channel,
                        "email_sent": delivery_proven,
                        "warnings": json.dumps(source_failures, default=str),
                        "started_at": started_at,
                        "finished_at": datetime.now(timezone.utc),
                        "output_schema": get_client(client_id).databricks_schema,
                    }
                )
                if not awaiting_approval:
                    checkpointer.complete_step(
                        run_id, agency_id, client_id, "pipeline_complete"
                    )
                _notify(
                    f"{'⏳' if awaiting_approval else '✅'} "
                    f"*{report.client_name}* "
                    f"{'awaiting approval' if awaiting_approval else 'complete'}\n"
                    f"Pipeline: `${report.total_pipeline:,.0f}` · Top: `{report.top_channel}`\n"
                    f"{delivery_status}"
                )
                results.append(
                    {
                        "client_id": client_id,
                        "meta_rows": ingest_result.get("meta_rows", 0),
                        "google_rows": ingest_result.get("google_rows", 0),
                        "linkedin_rows": ingest_result.get("linkedin_rows", 0),
                        "tiktok_rows": ingest_result.get("tiktok_rows", 0),
                        "hubspot_rows": ingest_result.get("hubspot_rows", 0),
                        "stripe_rows": ingest_result.get("stripe_rows", 0),
                        "normalized_ad_rows": ingest_result.get(
                            "normalized_ad_rows", 0
                        ),
                        "attribution_model": selected_model,
                        "top_channel": report.top_channel,
                        "total_pipeline": report.total_pipeline,
                        "email_sent": delivery_proven,
                        "status": (
                            "awaiting_approval"
                            if awaiting_approval
                            else (
                                "partial"
                                if ingest_status == "partial"
                                or not delivery_policy_clear
                                or approval_status == "rejected"
                                else "complete"
                            )
                        ),
                        "approval_status": approval_status,
                        "source_failures": source_failures,
                    }
                )

            except Exception as exc:
                logger.error(f"[Agency] Failed for client '{client_id}': {exc}")
                checkpointer.fail_step(
                    run_id, agency_id, client_id, "pipeline_complete", str(exc)
                )
                _notify(f"❌ *{client_id}* failed\n`{str(exc)[:200]}`")
                dispatch_alerts(
                    [
                        OperatorAlert(
                            severity="critical",
                            category="pipeline_failure",
                            title="Attribution pipeline failed",
                            message=str(exc)[:1200],
                            client_id=client_id,
                            agency_id=agency_id,
                            run_id=run_id,
                            action_required=(
                                "Open ARIE run history, inspect the failed step, "
                                "fix the source or configuration issue, then rerun."
                            ),
                        )
                    ]
                )
                errors.append({"client_id": client_id, "error": str(exc)})
                try:
                    write_pipeline_run(
                        {
                            "run_id": run_id,
                            "agency_id": agency_id,
                            "client_id": client_id,
                            "run_mode": run_mode,
                            "attribution_model": selected_model,
                            "status": "failed",
                            "dry_run": dry_run,
                            "error": str(exc),
                            "started_at": started_at,
                            "finished_at": datetime.now(timezone.utc),
                            "output_schema": get_client(client_id).databricks_schema,
                        }
                    )
                except Exception as write_exc:
                    logger.warning(f"[Ops] Failed to write run history: {write_exc}")

    # Run cross-client benchmark SQL (best-effort)
    if run_benchmarks and not dry_run and results:
        run_agency_benchmark_sql(agency)

    summary = {
        "agency_id": agency_id,
        "run_id": run_id,
        "attribution_model": selected_model,
        "report_month": requested_period.month,
        "clients_processed": len(results),
        "clients_failed": len(errors),
        "dry_run": dry_run,
        "results": results,
        "errors": errors,
    }
    logger.info(f"[Agency] Pipeline complete | {summary}")
    partial_count = sum(item.get("status") == "partial" for item in results)
    awaiting_count = sum(item.get("status") == "awaiting_approval" for item in results)
    success_count = len(results) - partial_count - awaiting_count
    _notify(
        f"{'✅' if not errors else '⚠️'} *Pipeline complete*\n"
        f"{success_count} succeeded · {awaiting_count} awaiting approval · "
        f"{partial_count} partial · {len(errors)} failed\n"
        f"Run ID: `{run_id[:8]}`"
    )
    return summary


def build_client_work_items(
    agency_id: str | None,
    client_filter: list[str] | None = None,
) -> list[tuple[str, str]]:
    """Build a deterministic Cloud Run task assignment list."""
    agency_ids = [agency_id] if agency_id else sorted(list_agencies())
    allowed_clients = set(client_filter or [])
    work_items: list[tuple[str, str]] = []

    for current_agency_id in agency_ids:
        agency = get_agency(current_agency_id)
        client_ids = sorted(_agency_client_ids(agency))
        if allowed_clients:
            client_ids = [cid for cid in client_ids if cid in allowed_clients]
        work_items.extend((current_agency_id, client_id) for client_id in client_ids)

    return work_items


def run_cloud_task(
    agency_id: str | None,
    dry_run: bool,
    client_filter: list[str] | None,
    attribution_model: str,
    report_month: str | None = None,
) -> dict | None:
    """Run this container's indexed client, or return None outside an array job."""
    task_index_value = os.environ.get("CLOUD_RUN_TASK_INDEX") or os.environ.get(
        "JOB_COMPLETION_INDEX"
    )
    if task_index_value is None:
        return None

    task_index = int(task_index_value)
    task_count = int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1"))
    manifest_uri = os.environ.get("ARIE_WORK_MANIFEST_URI", "").strip()
    manifest = None
    if manifest_uri:
        from utils.work_manifest import load_work_manifest

        manifest = load_work_manifest(manifest_uri)
        manifest_environment = {
            "ARIE_REPORT_MONTH": manifest["report_month"],
            "ARIE_REPORT_TIMEZONE": manifest["report_timezone"],
            "ARIE_PERIOD_START": manifest["period_start"],
            "ARIE_PERIOD_END": manifest["period_end"],
        }
        for name, expected in manifest_environment.items():
            supplied = os.environ.get(name, "").strip()
            if supplied and supplied != str(expected):
                raise RuntimeError(f"{name} conflicts with the immutable work manifest")
            os.environ[name] = str(expected)
        work_items = [
            (item["agency_id"], item["client_id"]) for item in manifest["work_items"]
        ]
        dry_run = bool(manifest.get("dry_run", dry_run))
        attribution_model = manifest.get("attribution_model", attribution_model)
        report_month = manifest.get("report_month", report_month)
    else:
        work_items = build_client_work_items(agency_id, client_filter)
    if task_index < 0 or task_count < 1:
        raise RuntimeError(
            "Cloud Run task index must be non-negative and task count must be positive"
        )
    if client_filter and not work_items:
        raise RuntimeError("No configured clients matched the requested client filter")
    if task_count < len(work_items):
        raise RuntimeError(
            f"Cloud Run has {task_count} tasks for {len(work_items)} clients"
        )
    if task_index >= len(work_items):
        return {"status": "no_work", "task_index": task_index}

    assigned_agency, assigned_client = work_items[task_index]
    execution_id = (
        (manifest.get("run_id") if manifest else None)
        or os.environ.get("CLOUD_RUN_EXECUTION")
        or str(uuid.uuid4())
    )
    logger.info(
        "[Job] Task %s/%s assigned to agency=%s client=%s",
        task_index,
        task_count,
        assigned_agency,
        assigned_client,
    )
    result = run_agency_pipeline(
        agency_id=assigned_agency,
        client_filter=[assigned_client],
        dry_run=dry_run,
        attribution_model=attribution_model,
        run_mode="client",
        execution_run_id=execution_id,
        run_benchmarks=False,
        report_month=report_month,
    )
    failed_count = int(result.get("clients_failed") or 0)
    partial_count = sum(
        item.get("status") == "partial" for item in result.get("results", [])
    )
    if failed_count or partial_count:
        raise RuntimeError(
            "Cloud Run task did not satisfy the client contract: "
            f"failed={failed_count} partial={partial_count}"
        )
    return result


def run_all_agencies(
    dry_run: bool = False,
    attribution_model: str | None = None,
    report_month: str | None = None,
) -> list[dict]:
    """Run run_agency_pipeline for every agency in AGENCY_REGISTRY."""
    return [
        run_agency_pipeline(
            agency_id,
            dry_run=dry_run,
            attribution_model=attribution_model,
            run_mode="agency",
            report_month=report_month,
        )
        for agency_id in list_agencies()
    ]


# ─── CLI / DATABRICKS JOB ENTRYPOINT ────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)

    # --- Parameter source: Databricks widgets ONLY on real Databricks compute.
    # Off-Databricks the SDK is still installed and DATABRICKS_HOST/TOKEN are set
    # (SQL-warehouse writes), so a bare try/except around dbutils could succeed
    # remotely and silently swallow CLI args — gate on the runtime marker instead.
    _agency = None
    _dry_run = False
    _attribution_model = "last_touch"
    _run_mode = "agency"
    _report_month = None
    _client_filter = None
    if os.environ.get("DATABRICKS_RUNTIME_VERSION"):
        from databricks.sdk.runtime import dbutils as _dbutils

        def _widget(name: str, default: str = "") -> str:
            try:
                return _dbutils.widgets.get(name) or default
            except Exception:
                return default

        _agency = _widget("agency") or None
        _dry_run = _widget("dry_run", "false").lower() == "true"
        _attribution_model = _widget("attribution_model", "last_touch") or "last_touch"
        _run_mode = _widget("run_mode", "agency") or "agency"
        _report_month = _widget("report_month") or None
        _client_filter_raw = _widget("client_filter")
        if _client_filter_raw:
            _client_filter = [
                value.strip()
                for value in _client_filter_raw.replace(",", " ").split()
                if value.strip()
            ]
        logger.info(
            f"[Job] Running with Databricks widget params: agency={_agency}, "
            f"clients={_client_filter}, dry_run={_dry_run}, "
            f"model={_attribution_model}, run_mode={_run_mode}"
        )
    else:
        parser = argparse.ArgumentParser(
            description="Run attribution pipeline for an agency"
        )
        parser.add_argument(
            "--agency",
            type=str,
            default=None,
            help="Agency ID (runs all agencies if omitted)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Generate reports but do not send emails",
        )
        parser.add_argument(
            "--client-filter",
            type=str,
            nargs="+",
            default=None,
            help="Run only specific client IDs within the agency",
        )
        parser.add_argument(
            "--attribution-model",
            type=str,
            default="last_touch",
            help="Attribution model to apply",
        )
        parser.add_argument(
            "--run-mode", type=str, default="agency", help="agency or business"
        )
        parser.add_argument(
            "--report-month",
            type=str,
            default=None,
            help="Completed report month in YYYY-MM format (defaults to previous month)",
        )
        args = parser.parse_args()
        _agency = args.agency
        _dry_run = args.dry_run
        _attribution_model = args.attribution_model
        _run_mode = args.run_mode
        _report_month = args.report_month
        _client_filter = args.client_filter

    cloud_task_result = run_cloud_task(
        agency_id=_agency,
        dry_run=_dry_run,
        client_filter=_client_filter,
        attribution_model=_attribution_model,
        report_month=_report_month,
    )
    if cloud_task_result is not None:
        print(json.dumps(cloud_task_result, indent=2, default=str))
    elif _agency:
        result = run_agency_pipeline(
            agency_id=_agency,
            client_filter=_client_filter,
            dry_run=_dry_run,
            attribution_model=_attribution_model,
            run_mode=_run_mode,
            report_month=_report_month,
        )
        print(json.dumps(result, indent=2, default=str))
    else:
        results = [
            run_agency_pipeline(
                agency_id=agency_id,
                client_filter=_client_filter,
                dry_run=_dry_run,
                attribution_model=_attribution_model,
                run_mode=_run_mode,
                report_month=_report_month,
            )
            for agency_id in list_agencies()
        ]
        print(json.dumps(results, indent=2, default=str))
