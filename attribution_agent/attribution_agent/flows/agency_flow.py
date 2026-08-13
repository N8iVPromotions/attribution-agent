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
from config.client_config import get_client, list_clients
from flows.ingest_flow import ingest_flow
from agents.insight.insight_agent import generate_insight_report
from agents.comms.comms_agent import send_agency_report
from attribution_models import normalize_model
from utils.databricks_writer import _run_sql, write_pipeline_run
from utils.idempotency import claim_report_delivery, report_delivery_key
from utils.operator_alerts import OperatorAlert, dispatch_alerts

try:
    from agents.control import arie_bot as _arie
except Exception:
    _arie = None


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


def run_client_attribution_sql(client_id: str, attribution_model: str) -> None:
    """Refresh model-specific closed-revenue attribution tables for a client."""
    config = get_client(client_id)
    sql_path = Path(_root) / "transforms" / "closed_revenue_attribution.sql"
    sql = sql_path.read_text()
    for stmt in sql.format(
        schema=config.databricks_schema,
        attribution_model=normalize_model(attribution_model),
    ).split(";"):
        stmt = stmt.strip()
        if stmt:
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
                # 1. Ingest
                if "ingest" not in completed_steps:
                    checkpointer.start_step(run_id, agency_id, client_id, "ingest")
                    ingest_result = ingest_flow(
                        client_id,
                        run_id=run_id,
                        attribution_model=selected_model,
                    )
                    checkpointer.complete_step(
                        run_id, agency_id, client_id, "ingest", ingest_result
                    )
                else:
                    logger.info(f"[Agency] Resuming — skipping ingest for {client_id}")
                    ingest_result = checkpointer.get_step_result(
                        run_id, client_id, "ingest"
                    )

                ingest_status = ingest_result.get("status", "complete")
                source_failures = ingest_result.get("source_failures", {})

                # 2. Refresh attribution outputs for the selected model
                if "attribution_sql" not in completed_steps:
                    checkpointer.start_step(
                        run_id, agency_id, client_id, "attribution_sql"
                    )
                    run_client_attribution_sql(client_id, selected_model)
                    checkpointer.complete_step(
                        run_id, agency_id, client_id, "attribution_sql"
                    )

                # 3. Generate report from refreshed attributed revenue
                if "generate_report" not in completed_steps:
                    checkpointer.start_step(
                        run_id, agency_id, client_id, "generate_report"
                    )
                    report = generate_insight_report(
                        client_id,
                        attribution_model=selected_model,
                        run_id=run_id,
                    )
                    checkpointer.complete_step(
                        run_id, agency_id, client_id, "generate_report"
                    )
                else:
                    report = generate_insight_report(
                        client_id,
                        attribution_model=selected_model,
                        run_id=run_id,
                    )

                # 3b. Governance review (advisory — never blocks)
                try:
                    from agents.intelligence.n8iv_agents import run_governance_review

                    governance_payload = report.to_dict()
                    governance_payload.pop("generated_at", None)
                    gov_warnings = run_governance_review(
                        client_id=client_id,
                        client_name=get_client(client_id).client_name,
                        report_narrative=report.narrative,
                        report_json=governance_payload,
                        agency_id=agency_id,
                        run_id=run_id,
                        data_version=report.data_version,
                    )
                    if gov_warnings:
                        logger.warning(
                            f"[Governance] {len(gov_warnings)} advisory item(s) for "
                            f"{client_id}: " + "; ".join(gov_warnings[:3])
                        )
                        dispatch_alerts(
                            [
                                OperatorAlert(
                                    severity="warning",
                                    category="report_governance",
                                    title="Report governance warning",
                                    message="; ".join(gov_warnings[:5]),
                                    client_id=client_id,
                                    agency_id=agency_id,
                                    run_id=run_id,
                                    action_required=(
                                        "Review the report before sending or "
                                        "sharing with the client."
                                    ),
                                    metadata={"warning_count": len(gov_warnings)},
                                )
                            ]
                        )
                except Exception as gov_exc:
                    logger.warning(
                        f"[Governance] review failed (non-fatal): {gov_exc!r}"
                    )

                # 4. Send white-labeled email
                email_sent = False
                delivery_idempotent_skip = False
                allow_partial_delivery = (
                    os.environ.get(
                        "ARIE_ALLOW_PARTIAL_REPORT_DELIVERY", "false"
                    ).lower()
                    == "true"
                )
                can_deliver = ingest_status != "partial" or allow_partial_delivery
                if not can_deliver:
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
                elif not dry_run and "email_sent" not in completed_steps:
                    recipient = _get_client_recipient(client_id)
                    delivery_key = report_delivery_key(
                        client_id, report.report_month, selected_model
                    )
                    claim = claim_report_delivery(delivery_key)
                    if not claim.acquired:
                        logger.info(
                            "[Agency] Duplicate report delivery suppressed | key=%s",
                            delivery_key,
                        )
                        checkpointer.complete_step(
                            run_id, agency_id, client_id, "email_sent"
                        )
                        delivery_idempotent_skip = True
                    else:
                        checkpointer.start_step(
                            run_id, agency_id, client_id, "email_sent"
                        )
                        try:
                            email_sent = send_agency_report(
                                report=report,
                                recipient_email=recipient,
                                agency_config=agency,
                                powerbi_url=agency.powerbi_workspace_url,
                            )
                            if not email_sent:
                                claim.release()
                        except Exception:
                            claim.release()
                            raise
                        if email_sent:
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
                                run_id, agency_id, client_id, "email_sent"
                            )
                            logger.info(
                                "[Agency] Report sent to %s | key=%s",
                                recipient,
                                delivery_key,
                            )
                elif dry_run:
                    logger.info(f"[Agency] dry_run — skipping email for {client_id}")

                checkpointer.complete_step(
                    run_id, agency_id, client_id, "pipeline_complete"
                )

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
                elif dry_run:
                    delivery_status = "🔕 Dry run — email skipped"
                elif not can_deliver:
                    delivery_status = "⛔ Delivery blocked — partial ingest"
                elif delivery_idempotent_skip:
                    delivery_status = "Duplicate delivery suppressed"
                elif "email_sent" in completed_steps:
                    delivery_status = "📧 Delivery previously completed"
                else:
                    delivery_status = "⚠️ Report email was not sent"

                _notify(
                    f"✅ *{get_client(client_id).client_name}* complete\n"
                    f"Pipeline: `${report.total_pipeline:,.0f}` · Top: `{report.top_channel}`\n"
                    f"{delivery_status}"
                )
                results.append(
                    {
                        "client_id": client_id,
                        "meta_rows": ingest_result.get("meta_rows", 0),
                        "google_rows": ingest_result.get("google_rows", 0),
                        "linkedin_rows": ingest_result.get("linkedin_rows", 0),
                        "hubspot_rows": ingest_result.get("hubspot_rows", 0),
                        "stripe_rows": ingest_result.get("stripe_rows", 0),
                        "normalized_ad_rows": ingest_result.get(
                            "normalized_ad_rows", 0
                        ),
                        "attribution_model": selected_model,
                        "top_channel": report.top_channel,
                        "total_pipeline": report.total_pipeline,
                        "email_sent": email_sent,
                        "status": ingest_status,
                        "source_failures": source_failures,
                    }
                )
                pipeline_status = "partial" if ingest_status == "partial" else "success"
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
                        "hubspot_rows": ingest_result.get("hubspot_rows", 0),
                        "stripe_rows": ingest_result.get("stripe_rows", 0),
                        "normalized_ad_rows": ingest_result.get(
                            "normalized_ad_rows", 0
                        ),
                        "total_pipeline": report.total_pipeline,
                        "top_channel": report.top_channel,
                        "email_sent": email_sent,
                        "warnings": json.dumps(source_failures, default=str),
                        "started_at": started_at,
                        "finished_at": datetime.now(timezone.utc),
                        "output_schema": get_client(client_id).databricks_schema,
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
        "clients_processed": len(results),
        "clients_failed": len(errors),
        "dry_run": dry_run,
        "results": results,
        "errors": errors,
    }
    logger.info(f"[Agency] Pipeline complete | {summary}")
    partial_count = sum(item.get("status") == "partial" for item in results)
    success_count = len(results) - partial_count
    _notify(
        f"{'✅' if not errors else '⚠️'} *Pipeline complete*\n"
        f"{success_count} succeeded · {partial_count} partial · {len(errors)} failed\n"
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
        work_items = [
            (item["agency_id"], item["client_id"]) for item in manifest["work_items"]
        ]
        dry_run = bool(manifest.get("dry_run", dry_run))
        attribution_model = manifest.get("attribution_model", attribution_model)
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
    return run_agency_pipeline(
        agency_id=assigned_agency,
        client_filter=[assigned_client],
        dry_run=dry_run,
        attribution_model=attribution_model,
        run_mode="client",
        execution_run_id=execution_id,
        run_benchmarks=False,
    )


def run_all_agencies(
    dry_run: bool = False,
    attribution_model: str | None = None,
) -> list[dict]:
    """Run run_agency_pipeline for every agency in AGENCY_REGISTRY."""
    return [
        run_agency_pipeline(
            agency_id,
            dry_run=dry_run,
            attribution_model=attribution_model,
            run_mode="agency",
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
        args = parser.parse_args()
        _agency = args.agency
        _dry_run = args.dry_run
        _attribution_model = args.attribution_model
        _run_mode = args.run_mode
        _client_filter = args.client_filter

    cloud_task_result = run_cloud_task(
        agency_id=_agency,
        dry_run=_dry_run,
        client_filter=_client_filter,
        attribution_model=_attribution_model,
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
            )
            for agency_id in list_agencies()
        ]
        print(json.dumps(results, indent=2, default=str))
