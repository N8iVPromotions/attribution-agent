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
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

try:
    _root = str(Path(__file__).parent.parent)
except NameError:
    _root = "/Workspace/Users/zajen@n8ivpromotions.com/attributionAgent/attribution_agent/attribution_agent"
sys.path.insert(0, _root)

from config.agency_config import AgencyConfig, get_agency, list_agencies
from config.client_config import get_client
from flows.ingest_flow import ingest_flow
from agents.insight.insight_agent import generate_insight_report
from agents.comms.comms_agent import send_agency_report
from utils.databricks_writer import _run_sql


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
    for client_id in agency.client_ids:
        config = get_client(client_id)
        client_name = config.client_display_name or config.client_name
        schema = config.databricks_schema
        clauses.append(
            f"SELECT '{client_id}' AS client_id, '{client_name}' AS client_name, * "
            f"FROM {schema}.channel_performance"
        )
    return "\nUNION ALL\n".join(clauses)


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


def run_agency_pipeline(
    agency_id: str,
    dry_run: bool = False,
    client_filter: list[str] | None = None,
) -> dict:
    """
    Run the full pipeline for every client in the agency:
    1. ingest_flow (pulls Meta, HubSpot, Stripe → Databricks)
    2. generate_insight_report (Claude narrative)
    3. send white-labeled email (skipped if dry_run=True)

    Runs clients sequentially to avoid API rate limits.
    """
    agency = get_agency(agency_id)
    client_ids = client_filter or agency.client_ids

    logger.info(
        f"[Agency] Starting pipeline | agency={agency_id} | "
        f"clients={client_ids} | dry_run={dry_run}"
    )

    results = []
    errors = []

    for client_id in client_ids:
        logger.info(f"[Agency] Processing client: {client_id}")
        try:
            # 1. Ingest
            ingest_result = ingest_flow(client_id)

            # 2. Generate report
            report = generate_insight_report(client_id)

            # 3. Send white-labeled email
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

            results.append({
                "client_id":     client_id,
                "meta_rows":     ingest_result.get("meta_rows", 0),
                "hubspot_rows":  ingest_result.get("hubspot_rows", 0),
                "stripe_rows":   ingest_result.get("stripe_rows", 0),
                "top_channel":   report.top_channel,
                "total_pipeline": report.total_pipeline,
                "email_sent":    email_sent,
                "status":        "ok",
            })

        except Exception as exc:
            logger.error(f"[Agency] Failed for client '{client_id}': {exc}")
            errors.append({"client_id": client_id, "error": str(exc)})

    # Run cross-client benchmark SQL (best-effort)
    if not dry_run and results:
        run_agency_benchmark_sql(agency)

    summary = {
        "agency_id":         agency_id,
        "clients_processed": len(results),
        "clients_failed":    len(errors),
        "dry_run":           dry_run,
        "results":           results,
        "errors":            errors,
    }
    logger.info(f"[Agency] Pipeline complete | {summary}")
    return summary


def run_all_agencies(dry_run: bool = False) -> list[dict]:
    """Run run_agency_pipeline for every agency in AGENCY_REGISTRY."""
    return [
        run_agency_pipeline(agency_id, dry_run=dry_run)
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
    args = parser.parse_args()

    if args.agency:
        result = run_agency_pipeline(
            agency_id=args.agency,
            dry_run=args.dry_run,
            client_filter=args.client_filter,
        )
        print(json.dumps(result, indent=2, default=str))
    else:
        results = run_all_agencies(dry_run=args.dry_run)
        print(json.dumps(results, indent=2, default=str))
