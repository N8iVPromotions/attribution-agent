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


def run_agency_benchmark_sql(agency: AgencyConfig) -> None:
    """Run the agency_benchmark and agency_dashboard SQL transforms."""
    try:
        union_all_clause = _build_union_all(agency)
        sql_dir = Path(__file__).parent.parent / "transforms"

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
        logger.warning(f"[Agency] Benchmark SQL failed (non-fatal): {exc}")


def run_client_attribution_sql(client_id: str, attribution_model: str) -> None:
    """Refresh model-specific closed-revenue attribution tables for a client."""
    config = get_client(client_id)
    sql_path = Path(__file__).parent.parent / "transforms" / "closed_revenue_attribution.sql"
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
) -> dict:
    """
    Run the full pipeline for every client in the agency:
    1. ingest_flow (pulls Meta, HubSpot, Stripe → Databricks)
    2. generate_insight_report (Claude narrative)
    3. send white-labeled email (skipped if dry_run=True)

    Runs clients sequentially to avoid API rate limits.
    """
    agency = get_agency(agency_id)
    client_ids = client_filter or _agency_client_ids(agency)
    selected_model = normalize_model(attribution_model or "last_touch")
    run_id = str(uuid.uuid4())

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
        try:
            # 1. Ingest
            ingest_result = ingest_flow(client_id)

            # 2. Refresh attribution outputs for the selected model
            run_client_attribution_sql(client_id, selected_model)

            # 3. Generate report from refreshed attributed revenue
            report = generate_insight_report(client_id, attribution_model=selected_model)

            # 4. Send white-labeled email
            email_sent = False
            if not dry_run:
                recipient = _get_client_recipient(client_id)
                email_sent = send_agency_report(
                    report=report,
                    recipient_email=recipient,
                    agency_config=agency,
                    powerbi_url=agency.powerbi_workspace_url,
                )
                logger.info(f"[Agency] Report sent to {recipient}")
            else:
                logger.info(f"[Agency] dry_run — skipping email for {client_id}")

            _notify(
                f"✅ *{get_client(client_id).client_name}* complete\n"
                f"Pipeline: `${report.total_pipeline:,.0f}` · Top: `{report.top_channel}`\n"
                f"{'📧 Report sent' if email_sent else '🔕 Dry run — email skipped'}"
            )
            results.append({
                "client_id":     client_id,
                "meta_rows":     ingest_result.get("meta_rows", 0),
                "google_rows":   ingest_result.get("google_rows", 0),
                "linkedin_rows": ingest_result.get("linkedin_rows", 0),
                "hubspot_rows":  ingest_result.get("hubspot_rows", 0),
                "stripe_rows":   ingest_result.get("stripe_rows", 0),
                "normalized_ad_rows": ingest_result.get("normalized_ad_rows", 0),
                "attribution_model": selected_model,
                "top_channel":   report.top_channel,
                "total_pipeline": report.total_pipeline,
                "email_sent":    email_sent,
                "status":        "ok",
            })
            write_pipeline_run({
                "run_id": run_id,
                "agency_id": agency_id,
                "client_id": client_id,
                "run_mode": run_mode,
                "attribution_model": selected_model,
                "status": "success",
                "dry_run": dry_run,
                "meta_rows": ingest_result.get("meta_rows", 0),
                "google_rows": ingest_result.get("google_rows", 0),
                "linkedin_rows": ingest_result.get("linkedin_rows", 0),
                "hubspot_rows": ingest_result.get("hubspot_rows", 0),
                "stripe_rows": ingest_result.get("stripe_rows", 0),
                "normalized_ad_rows": ingest_result.get("normalized_ad_rows", 0),
                "total_pipeline": report.total_pipeline,
                "top_channel": report.top_channel,
                "email_sent": email_sent,
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc),
                "output_schema": get_client(client_id).databricks_schema,
            })

        except Exception as exc:
            logger.error(f"[Agency] Failed for client '{client_id}': {exc}")
            _notify(f"❌ *{client_id}* failed\n`{str(exc)[:200]}`")
            errors.append({"client_id": client_id, "error": str(exc)})
            try:
                write_pipeline_run({
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
                })
            except Exception as write_exc:
                logger.warning(f"[Ops] Failed to write run history: {write_exc}")

    # Run cross-client benchmark SQL (best-effort)
    if not dry_run and results:
        run_agency_benchmark_sql(agency)

    summary = {
        "agency_id":         agency_id,
        "run_id":            run_id,
        "attribution_model": selected_model,
        "clients_processed": len(results),
        "clients_failed":    len(errors),
        "dry_run":           dry_run,
        "results":           results,
        "errors":            errors,
    }
    logger.info(f"[Agency] Pipeline complete | {summary}")
    _notify(
        f"{'✅' if not errors else '⚠️'} *Pipeline complete*\n"
        f"{len(results)} succeeded · {len(errors)} failed\n"
        f"Run ID: `{run_id[:8]}`"
    )
    return summary


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


# ─── CLI ENTRYPOINT ───────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="Run attribution pipeline for an agency")
    parser.add_argument("--agency", type=str, default=None,
                        help="Agency ID (runs all agencies if omitted)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate reports but do not send emails")
    parser.add_argument("--client-filter", type=str, nargs="+", default=None,
                        help="Run only specific client IDs within the agency")
    parser.add_argument("--attribution-model", type=str, default="last_touch",
                        help="Attribution model to apply")
    parser.add_argument("--run-mode", type=str, default="agency",
                        help="agency or business")
    args = parser.parse_args()

    if args.agency:
        result = run_agency_pipeline(
            agency_id=args.agency,
            dry_run=args.dry_run,
            client_filter=args.client_filter,
            attribution_model=args.attribution_model,
            run_mode=args.run_mode,
        )
        print(json.dumps(result, indent=2, default=str))
    else:
        results = [
            run_agency_pipeline(
                agency_id=agency_id,
                dry_run=args.dry_run,
                attribution_model=args.attribution_model,
                run_mode=args.run_mode,
            )
            for agency_id in list_agencies()
        ]
        print(json.dumps(results, indent=2, default=str))
