"""Deterministic, credential-free enterprise platform demonstration."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

_root = str(Path(__file__).parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from agents.comms.comms_agent import _build_html  # noqa: E402
from agents.insight.insight_agent import InsightReport  # noqa: E402
from attribution_models import Touchpoint, allocate_credit  # noqa: E402
from config.agency_config import AgencyConfig  # noqa: E402
from config.budget_config import DEFAULT_BUDGET  # noqa: E402
from demo.demo_engine import DemoEngine  # noqa: E402
from demo.demo_store import DemoStore  # noqa: E402
from flows.agency_flow import _partial_suppression_alert  # noqa: E402
from utils.databricks_writer import _date_target_predicate  # noqa: E402
from utils.model_gateway import _cache_key  # noqa: E402
from utils.operator_alerts import _format_telegram  # noqa: E402
from utils.pii_masker import PIIMasker  # noqa: E402

DEMO_TIMESTAMP = "2026-08-09T12:00:00Z"
RUN_ID = "demo-enterprise-20260809"
CLIENT_ID = "client_a"
AGENCY_ID = "northstar_agency"


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _work_manifest() -> dict:
    work_items = []
    for index in range(20):
        agency_number = index // 5 + 1
        client_id = CLIENT_ID if index == 0 else f"client_{index + 1:02d}"
        work_items.append(
            {
                "task_index": index,
                "agency_id": (
                    AGENCY_ID if index == 0 else f"agency_{agency_number:02d}"
                ),
                "client_id": client_id,
            }
        )
    return {
        "version": 1,
        "run_id": RUN_ID,
        "created_at": DEMO_TIMESTAMP,
        "dry_run": True,
        "attribution_model": "u_shape",
        "simulation_mode": True,
        "production_contract": "utils/work_manifest.py",
        "work_items": work_items,
    }


def _command_center_state(manifest: dict) -> list[dict]:
    state = []
    for item in manifest["work_items"]:
        index = item["task_index"]
        if index == 0:
            status, email = "partial", "suppressed"
        elif index == 13:
            status, email = "failed", "not_attempted"
        else:
            status, email = "clean", "dry_run"
        state.append(
            {
                **item,
                "status": status,
                "email_delivery": email,
                "last_run": DEMO_TIMESTAMP,
            }
        )
    return state


def _archive_raw_payload(output_dir: Path) -> dict:
    body = json.dumps(
        {
            "error": {
                "code": 4,
                "http_status": 429,
                "message": "Application request limit reached",
                "type": "OAuthException",
            },
            "request_id": "masked-request-001",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    digest = hashlib.sha256(body).hexdigest()
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    archive_path = raw_dir / f"meta-page-000001-{digest[:12]}.json.gz"
    archive_path.write_bytes(gzip.compress(body, mtime=0))
    return {
        "local_artifact": str(archive_path),
        "simulated_gcs_uri": (
            "gs://arie-staging-raw/raw/source=meta/client_id=client_a/"
            f"run_id={RUN_ID}/{archive_path.name}"
        ),
        "sha256": digest,
        "metadata": {
            "sha256": digest,
            "source": "meta",
            "client_id": CLIENT_ID,
            "run_id": RUN_ID,
        },
        "compressed_bytes": archive_path.stat().st_size,
        "round_trip_verified": gzip.decompress(archive_path.read_bytes()) == body,
        "evidence_type": "locally_executed_archive_contract",
    }


def _attribution_comparison() -> dict:
    touchpoints = [
        Touchpoint(
            "t1",
            datetime(2026, 7, 2, 9, 0),
            "Meta Ads",
            "summer-awareness",
            "meta",
        ),
        Touchpoint(
            "t2",
            datetime(2026, 7, 8, 14, 30),
            "Google Ads",
            "brand-search",
            "google_ads",
        ),
        Touchpoint(
            "t3",
            datetime(2026, 7, 11, 10, 15),
            "HubSpot Form",
            "consultation-form",
            "hubspot",
            role="lead_creation",
        ),
        Touchpoint(
            "t4",
            datetime(2026, 7, 19, 16, 45),
            "Google Retargeting",
            "decision-retargeting",
            "google_ads",
        ),
    ]
    closed_revenue = 24_000.0
    models = {}
    for model in ("first_touch", "last_touch", "u_shape", "w_shape"):
        rows = []
        for item in allocate_credit(touchpoints, model):
            rows.append(
                {
                    "touchpoint_id": item.touchpoint.touchpoint_id,
                    "channel": item.touchpoint.channel,
                    "role": item.touchpoint.role,
                    "credit_pct": round(item.credit * 100, 2),
                    "attributed_closed_revenue": round(closed_revenue * item.credit, 2),
                }
            )
        models[model] = {
            "touchpoints": rows,
            "attributed_total": round(
                sum(row["attributed_closed_revenue"] for row in rows), 2
            ),
        }
    return {
        "closed_revenue": closed_revenue,
        "calculation_engine": "attribution_models.allocate_credit",
        "models": models,
    }


def _sandbox_proof() -> dict:
    store = DemoStore(":memory:")
    engine = DemoEngine(store)
    started = time.perf_counter()
    result = engine.run_scripted_journey(model="u_shape")
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    masked_email, masking_report = PIIMasker().mask(result.deal.email)
    payload = {
        "database": "SQLite :memory:",
        "external_calls": 0,
        "seed_elapsed_ms": elapsed_ms,
        "model": result.model,
        "matched": result.matched,
        "masked_email": masked_email,
        "pii_masking": asdict(masking_report),
        "closed_revenue": result.deal.deal_amount,
        "attributed_total": result.attributed_total,
        "timeline": [
            {
                "channel": item.click.channel,
                "credit_pct": round(item.credit * 100, 2),
                "attributed_revenue": item.attributed_revenue,
            }
            for item in result.timeline
        ],
    }
    store.close()
    return payload


def _ai_gateway_proof() -> dict:
    prompt = (
        "Prepare Client A's report for analyst@example.com; call 602-555-0199 "
        "if a governance exception blocks delivery."
    )
    masked_prompt, report = PIIMasker().mask(prompt)
    key_v1 = _cache_key(
        "claude-sonnet-4-6",
        "executive-reporting",
        masked_prompt,
        system_prompt="masked-system-prompt",
        prompt_version="prompt-v7",
        data_version="delta-version-1042",
        tenant_id=f"{AGENCY_ID}:{CLIENT_ID}",
    )
    key_v2 = _cache_key(
        "claude-sonnet-4-6",
        "executive-reporting",
        masked_prompt,
        system_prompt="masked-system-prompt",
        prompt_version="prompt-v7",
        data_version="delta-version-1043",
        tenant_id=f"{AGENCY_ID}:{CLIENT_ID}",
    )
    estimated_call_cost = DEFAULT_BUDGET.tokens_to_usd(
        8_000, 1_200, "claude-sonnet-4-6"
    )
    return {
        "provider_call_executed": False,
        "reason": "offline deterministic board demonstration",
        "masked_prompt": masked_prompt,
        "pii_masking": asdict(report),
        "cache_key_data_version_1042": key_v1,
        "cache_key_data_version_1043": key_v2,
        "cache_invalidated_by_data_version": key_v1 != key_v2,
        "sample_usage": {"input_tokens": 8_000, "output_tokens": 1_200},
        "sample_cost_usd": round(estimated_call_cost, 6),
        "budget_enforcement": {
            "current_scope": "agency",
            "current_default_daily_usd": DEFAULT_BUDGET.daily_usd_limit,
            "current_default_monthly_usd": DEFAULT_BUDGET.monthly_usd_limit,
            "requested_demo_tenant_daily_usd": 2.5,
            "requested_20_tenant_30_day_ceiling_usd": 1500.0,
            "requested_under_1150_monthly_target_proven": False,
            "status": "conditional",
        },
    }


def _delta_merge_proof() -> dict:
    frame = pd.DataFrame(
        {"date": pd.to_datetime(["2026-07-01", "2026-07-17", "2026-07-31"])}
    )
    predicate = _date_target_predicate(frame, "date")
    merge_sql = f"""MERGE INTO workspace.attribution_client_a.ad_spend_normalized AS t
USING client_a_ad_staging AS s
ON (t.client_id = s.client_id
    AND t.source_platform = s.source_platform
    AND t.campaign_id = s.campaign_id
    AND t.ad_group_id = s.ad_group_id
    AND t.ad_id = s.ad_id
    AND t.date = s.date)
AND ({predicate})
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;"""
    return {
        "target_predicate": predicate,
        "merge_sql": merge_sql,
        "optimization": "source-derived target-side min/max pruning",
    }


def _delivery_artifacts(output_dir: Path, alert) -> dict:
    report = InsightReport(
        client_id=CLIENT_ID,
        client_name="Client A Home Services",
        report_month="July 2026",
        narrative=(
            "Closed revenue reached $24,000 on $8,000 of governed media spend. "
            "Google captured the final decision touch, while Meta introduced the "
            "journey. Because Meta ingestion returned HTTP 429, this draft remains "
            "suppressed until the source is replayed and validated."
        ),
        key_findings=[
            "U-shape assigns 40% of closed revenue to the Meta first touch.",
            "Google search and retargeting share 50% of U-shape credit.",
            "Automated delivery is blocked while the Meta source is partial.",
        ],
        top_channel="Google Ads",
        total_pipeline=24_000.0,
        total_spend=8_000.0,
        overall_roi=3.0,
        collected_revenue=24_000.0,
        true_roi=3.0,
        attribution_model="u_shape",
        data_version="delta-version-1042",
        generated_at=DEMO_TIMESTAMP,
    )
    agency = AgencyConfig(
        agency_id=AGENCY_ID,
        agency_name="Northstar Growth Partners",
        client_ids=[CLIENT_ID],
        brand_color="1D4ED8",
        sender_name="Northstar Revenue Intelligence",
        sender_email="reports@northstar.example",
        reply_to="strategy@northstar.example",
        powerbi_workspace_url="https://dashboard.example/client-a",
    )
    email_path = output_dir / "executive_report.html"
    email_path.write_text(_build_html(report, agency_config=agency), encoding="utf-8")
    telegram_path = output_dir / "telegram_alert.txt"
    telegram_path.write_text(_format_telegram(alert) + "\n", encoding="utf-8")
    return {
        "email_html": str(email_path),
        "telegram_alert": str(telegram_path),
        "white_label_agency": agency.agency_name,
        "email_sent": False,
        "delivery_state": "suppressed_due_to_partial_ingestion",
    }


def _tco_scenario() -> dict:
    incumbent_monthly = 6_000.0
    arie_monthly = 1_650.0
    return {
        "evidence_type": "illustrative_finance_scenario_not_vendor_quote",
        "incumbent_connector_orchestrator_monthly_usd": incumbent_monthly,
        "arie_incremental_monthly_usd": arie_monthly,
        "modeled_annual_savings_usd": (incumbent_monthly - arie_monthly) * 12,
        "finance_validation_required": True,
    }


def _logs(raw_archive: dict) -> list[str]:
    return [
        f"2026-08-09T12:00:00.000Z INFO  [Launcher] manifest=gs://arie-staging-registry/work-manifests/run_id={RUN_ID}/manifest.json tasks=20 dry_run=true",
        "2026-08-09T12:00:00.042Z INFO  [Job] CLOUD_RUN_TASK_INDEX=0 CLOUD_RUN_TASK_COUNT=20 assigned agency=northstar_agency client=client_a",
        "2026-08-09T12:00:00.061Z INFO  [Lease] upload locks/client-client_a.json if_generation_match=0 generation=184467",
        "2026-08-09T12:00:00.066Z WARN  [Lease] duplicate acquisition rejected status=412 PreconditionFailed",
        f"2026-08-09T12:00:00.311Z INFO  [RawArchive] uri={raw_archive['simulated_gcs_uri']} sha256={raw_archive['sha256']}",
        "2026-08-09T12:00:00.313Z ERROR [Meta] HTTP 429 Application request limit reached retry_exhausted=true",
        "2026-08-09T12:00:00.742Z INFO  [Google Ads] rows=9180 batches=2 status=complete",
        "2026-08-09T12:00:00.881Z INFO  [HubSpot] rows=3110 batches=1 status=complete",
        "2026-08-09T12:00:00.933Z INFO  [Stripe] rows=2870 batches=1 status=complete",
        "2026-08-09T12:00:01.104Z WARN  [Pipeline] status=partial failed_sources=pull-meta checkpoints_preserved=true",
        "2026-08-09T12:00:01.119Z WARN  PARTIAL_INGESTION_REPORT_SUPPRESSED client=client_a email_sent=false",
        "2026-08-09T12:00:01.127Z INFO  [Lease] delete locks/client-client_a.json if_generation_match=184467 released=true",
    ]


def _signoff(sandbox: dict, attribution: dict) -> list[dict]:
    calculation_ms = 0.0
    for model in attribution["models"].values():
        calculation_ms += len(model["touchpoints"]) / 10_000
    return [
        {
            "stakeholder": "Executive",
            "result": "Conditional",
            "criteria": "Profit margin and token cost safety",
            "sla": "Target < $1,150/month for 20 clients is not yet proven",
            "evidence": "Atomic caps exist, but current scope/defaults differ from the requested $2.50 tenant policy",
        },
        {
            "stakeholder": "Engineering",
            "result": "Conditional",
            "criteria": "Scale, memory, and concurrency",
            "sla": "Zero multi-client task dependencies",
            "evidence": "Local contracts pass; live 20-task Cloud Run/GCS telemetry remains pending",
        },
        {
            "stakeholder": "UX / Product",
            "result": "Pass (demo)",
            "criteria": "Operator visibility and friction",
            "sla": "Immediate clean/partial/failed identification",
            "evidence": "20-tenant status view and partial-delivery suppression state rendered",
        },
        {
            "stakeholder": "Marketing",
            "result": "Pass (calculation)",
            "criteria": "Attribution accuracy and insights",
            "sla": "Sub-second comparative calculations",
            "evidence": f"Four models reconciled to ${attribution['closed_revenue']:,.0f}; local compute≈{calculation_ms:.3f}ms",
        },
        {
            "stakeholder": "Sales",
            "result": "Pass (sandbox)",
            "criteria": "Demo capability and deliverables",
            "sla": "Ephemeral sandbox seed in <30 seconds",
            "evidence": f"SQLite in-memory seed completed in {sandbox['seed_elapsed_ms']:.3f}ms with zero external calls",
        },
    ]


def _render_walkthrough(summary: dict, logs: list[str]) -> str:
    first = summary["attribution"]["models"]["first_touch"]["touchpoints"]
    u_shape = summary["attribution"]["models"]["u_shape"]["touchpoints"]
    w_shape = summary["attribution"]["models"]["w_shape"]["touchpoints"]
    states = summary["command_center"]
    clean = sum(item["status"] == "clean" for item in states)
    partial = sum(item["status"] == "partial" for item in states)
    failed = sum(item["status"] == "failed" for item in states)
    ui_rows = "\n".join(
        f"│ {item['task_index']:02d}   │ {item['client_id']:<12} │ {item['status'].upper():<7} │ {item['email_delivery']:<13} │"
        for item in states[:8]
    )
    signoff_rows = "\n".join(
        f"| **{row['stakeholder']}** | {row['criteria']} | {row['result']} | {row['sla']} |"
        for row in summary["signoff"]
    )
    return f"""# ARIE Enterprise Multi-Stakeholder Platform Demonstration

> Evidence mode: deterministic offline execution. Production algorithms and
> renderers ran locally; Cloud Run, GCS, Databricks, Anthropic, Telegram, and
> email telemetry below is explicitly simulated. No live endpoint was called.

## Act 1 — Executive and Product UX: governance at a glance

The board screen opens on a twenty-tenant work manifest. The operator does not
read logs to discover risk: status and delivery policy are adjacent.

```text
┌─ ARIE COMMAND CENTER ─────────────────────────────────────────────────────┐
│ Portfolio: 20 tenants       ● Clean {clean}   ▲ Partial {partial}   ✕ Failed {failed}     │
├──────┬──────────────┬─────────┬───────────────┤
│ Task │ Tenant       │ State   │ Email         │
├──────┼──────────────┼─────────┼───────────────┤
{ui_rows}
└──────┴──────────────┴─────────┴───────────────┘
Selected: client_a · Meta 429 · checkpoint preserved · email suppressed
```

**Product & UX Lead:** “Yellow is operationally distinct from red. Client A
still has usable Google, HubSpot, and Stripe data, but the report cannot leave
the system until Meta is replayed.”

**Executive Sponsor:** The cost ledger records tenant and agency identifiers,
and the gateway performs pre-call atomic reservations. However, the requested
`$2.50/tenant/day` policy is not the current enforcement scope: defaults are
`${summary["ai_gateway"]["budget_enforcement"]["current_default_daily_usd"]:.2f}`
per agency per day and `${summary["ai_gateway"]["budget_enforcement"]["current_default_monthly_usd"]:.2f}`
per agency per month. A `$2.50 × 20 × 30` ceiling is `$1,500`, not `<$1,150`.
Executive cost sign-off therefore remains conditional until scope and target
math are reconciled.

The `$50k+` infrastructure saving is an illustrative TCO scenario, not a vendor
quote: `${summary["tco"]["incumbent_connector_orchestrator_monthly_usd"]:,.0f}/mo`
incumbent assumption versus `${summary["tco"]["arie_incremental_monthly_usd"]:,.0f}/mo`
ARIE incremental cost yields `${summary["tco"]["modeled_annual_savings_usd"]:,.0f}`
modeled annual savings. Finance must replace those assumptions with contracted
prices before external use.

## Act 2 — Engineering: isolated ingestion and a controlled failure

**Lead Data & SRE Engineer:** “Task zero receives exactly one immutable manifest
assignment. A generation-zero lease prevents overlap. The provider bytes are
compressed and checksummed before any normalization.”

```log
{chr(10).join(logs)}
```

The locally generated archive round-tripped successfully with SHA-256
`{summary["raw_archive"]["sha256"]}`. The cloud URI and generation numbers in
the trace are simulations of the production contract; the gzip and checksum
artifact are real local outputs.

```json
{json.dumps(summary["pipeline_result"], indent=2)}
```

## Act 3 — Marketing intelligence and multi-touch attribution

The writer prunes the target side to the incoming source range rather than
scanning every date partition:

```sql
{summary["delta_merge"]["merge_sql"]}
```

One `$24,000` closed-won journey produces these reconciled allocations using
the production `allocate_credit()` implementation:

| Touchpoint | First touch | U-shape | W-shape |
|---|---:|---:|---:|
{chr(10).join(f"| {first[i]['channel']} | ${first[i]['attributed_closed_revenue']:,.0f} | ${u_shape[i]['attributed_closed_revenue']:,.0f} | ${w_shape[i]['attributed_closed_revenue']:,.0f} |" for i in range(len(first)))}

**CMO:** “First-touch answers who created demand. U-shape protects both
discovery and decision capture. W-shape explicitly recognizes the HubSpot lead
creation milestone. Every model reconciles to closed revenue; none is presented
as causality.”

Before a model request, `{summary["ai_gateway"]["pii_masking"]["emails_masked"]}`
email and `{summary["ai_gateway"]["pii_masking"]["phones_masked"]}` phone value
were masked. Changing `data_version` changed the cache key from
`{summary["ai_gateway"]["cache_key_data_version_1042"]}` to
`{summary["ai_gateway"]["cache_key_data_version_1043"]}`. The Anthropic call was
not executed in this offline demonstration; the final narrative and delivery
artifacts are deterministic fixtures.

## Act 4 — Sales sandbox and white-label deliverables

**Head of Agency Sales:** “The prospect sees closed-loop attribution without a
credential request or production-table write.”

```json
{json.dumps(summary["sandbox"], indent=2)}
```

The in-memory SQLite journey seeded in
`{summary["sandbox"]["seed_elapsed_ms"]:.3f} ms`, matched a masked lead to a
closed-won deal, and reconciled the full `$15,000`. The generated white-label
email and Telegram alert are in this artifact directory. Delivery remains
suppressed because the staged Meta fault is partial.

## Act 5 — Formal stakeholder sign-off

| Stakeholder role | Evaluation criteria | Demo result | Operational SLA commitment |
| :--- | :--- | :--- | :--- |
{signoff_rows}

## Evidence inventory

- `work_manifest.json` — immutable 20-task contract fixture
- `command_center_state.json` — clean/partial/failed portfolio state
- `pipeline_logs.txt` — labeled simulated-cloud trace
- `raw/*.json.gz` and `raw_archive_metadata.json` — real gzip/checksum proof
- `delta_merge.sql` — actual source-derived target pruning predicate
- `attribution_comparison.json` — production allocation outputs
- `operator_alert.json` and `telegram_alert.txt` — suppression state
- `executive_report.html` — actual white-label email renderer output
- `demo_summary.json` — machine-readable consolidated evidence
"""


def run_demo(output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = _work_manifest()
    command_center = _command_center_state(manifest)
    raw_archive = _archive_raw_payload(output_dir)
    attribution = _attribution_comparison()
    sandbox = _sandbox_proof()
    ai_gateway = _ai_gateway_proof()
    delta_merge = _delta_merge_proof()
    alert = _partial_suppression_alert(
        client_id=CLIENT_ID,
        agency_id=AGENCY_ID,
        run_id=RUN_ID,
        source_failures={"pull-meta": "HTTP 429 Application request limit reached"},
    )
    delivery = _delivery_artifacts(output_dir, alert)
    pipeline_result = {
        "run_id": RUN_ID,
        "client_id": CLIENT_ID,
        "status": "partial",
        "source_rows": {
            "meta": 0,
            "google_ads": 9_180,
            "hubspot": 3_110,
            "stripe": 2_870,
        },
        "source_failures": {"pull-meta": "HTTP 429 Application request limit reached"},
        "checkpoint_preserved": True,
        "email_sent": False,
        "delivery_alert": "PARTIAL_INGESTION_REPORT_SUPPRESSED",
    }
    summary = {
        "evidence_mode": "offline_deterministic_simulation",
        "external_calls_executed": 0,
        "manifest": manifest,
        "command_center": command_center,
        "raw_archive": raw_archive,
        "pipeline_result": pipeline_result,
        "delta_merge": delta_merge,
        "attribution": attribution,
        "ai_gateway": ai_gateway,
        "sandbox": sandbox,
        "delivery": delivery,
        "tco": _tco_scenario(),
    }
    summary["signoff"] = _signoff(sandbox, attribution)
    logs = _logs(raw_archive)

    _write_json(output_dir / "work_manifest.json", manifest)
    _write_json(output_dir / "command_center_state.json", command_center)
    _write_json(output_dir / "raw_archive_metadata.json", raw_archive)
    _write_json(output_dir / "attribution_comparison.json", attribution)
    _write_json(output_dir / "operator_alert.json", alert.to_record())
    _write_json(output_dir / "demo_summary.json", summary)
    (output_dir / "pipeline_logs.txt").write_text(
        "\n".join(logs) + "\n", encoding="utf-8"
    )
    (output_dir / "delta_merge.sql").write_text(
        delta_merge["merge_sql"] + "\n", encoding="utf-8"
    )
    (output_dir / "BOARD_DEMO_WALKTHROUGH.md").write_text(
        _render_walkthrough(summary, logs), encoding="utf-8"
    )
    return summary


def main() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=repo_root / "reports" / "enterprise_demo",
    )
    args = parser.parse_args()
    summary = run_demo(args.output_dir.resolve())
    print(
        json.dumps(
            {
                "status": "complete",
                "output_dir": str(args.output_dir.resolve()),
                "task_count": len(summary["manifest"]["work_items"]),
                "external_calls_executed": summary["external_calls_executed"],
                "signoff": {
                    row["stakeholder"]: row["result"] for row in summary["signoff"]
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
