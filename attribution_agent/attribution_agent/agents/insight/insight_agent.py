"""
agents/insight/insight_agent.py
--------------------------------
Reads channel_performance from Databricks and generates a natural language
attribution narrative using the Claude API.
=
Output: A structured InsightReport with narrative + key findings.

Run standalone:
    python agents/insight/insight_agent.py --client demo_client

Or import and call generate_insight_report(client_id) from a flow.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── path setup ────────────────────────────────────────────────
try:
    _root = str(Path(__file__).parent.parent.parent)
except NameError:
    import inspect as _inspect

    _root = str(Path(_inspect.getfile(_inspect.currentframe())).parent.parent.parent)
sys.path.insert(0, _root)

from config.client_config import get_client, ClientConfig
from attribution_models import (
    ATTRIBUTION_MODEL_DESCRIPTIONS,
    ATTRIBUTION_MODEL_LABELS,
    normalize_model,
)


# ─── REPORT DATACLASS ─────────────────────────────────────────


@dataclass
class InsightReport:
    client_id: str
    client_name: str
    report_month: str
    narrative: str  # Full Claude-generated narrative
    key_findings: list[str] = field(default_factory=list)
    top_channel: str = ""
    total_pipeline: float = 0.0
    total_spend: float = 0.0
    overall_roi: float = 0.0
    collected_revenue: float = 0.0  # Actual cash collected via Stripe
    refund_rate: float = 0.0  # Refunds / collected revenue
    true_roi: float = 0.0  # collected_revenue / total_spend
    attribution_model: str = "last_touch"
    generated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "client_id": self.client_id,
            "client_name": self.client_name,
            "report_month": self.report_month,
            "narrative": self.narrative,
            "key_findings": self.key_findings,
            "top_channel": self.top_channel,
            "total_pipeline": self.total_pipeline,
            "total_spend": self.total_spend,
            "overall_roi": self.overall_roi,
            "collected_revenue": self.collected_revenue,
            "refund_rate": self.refund_rate,
            "true_roi": self.true_roi,
            "attribution_model": self.attribution_model,
            "generated_at": self.generated_at,
        }


# ─── DATA FETCHER ─────────────────────────────────────────────


def _fetch_channel_performance(config: ClientConfig) -> list[dict]:
    """Pull channel_performance data from Databricks. Prefers v2 (payment metrics)."""
    from databricks import sql

    conn = sql.connect(
        server_hostname=(
            os.environ.get("DATABRICKS_SERVER_HOSTNAME")
            or os.environ.get("DATABRICKS_HOST", "").lstrip("https://").rstrip("/")
        ),
        http_path=os.environ["DATABRICKS_HTTP_PATH"],
        access_token=os.environ["DATABRICKS_TOKEN"],
    )
    cursor = conn.cursor()

    # Try v2 first (has Stripe payment metrics), fall back to v1
    table = "channel_performance_v2"
    v2_cols = ", collected_revenue, refund_rate, true_roi, ltv_90day"
    try:
        cursor.execute(
            f"DESCRIBE TABLE {config.databricks_schema}.channel_performance_v2"
        )
    except Exception:
        table = "channel_performance"
        v2_cols = ""

    query = f"""
        SELECT
            report_month,
            channel,
            deals_count,
            pipeline_value,
            avg_deal_value,
            avg_days_to_close,
            total_spend,
            roi,
            cost_per_deal
            {v2_cols}
        FROM {config.databricks_schema}.{table}
        ORDER BY pipeline_value DESC
    """

    cursor.execute(query)
    columns = [desc[0] for desc in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    cursor.close()
    conn.close()

    logger.info(
        f"[Insight] Fetched {len(rows)} channel rows for {config.client_id} "
        f"(source: {table})"
    )
    return rows


# ─── CLAUDE NARRATIVE GENERATOR ───────────────────────────────


def _build_prompt(
    config: ClientConfig, data: list[dict], attribution_model: str
) -> str:
    """Build the prompt for Claude."""

    data_str = json.dumps(data, indent=2, default=str)
    selected_model = normalize_model(attribution_model)
    model_label = ATTRIBUTION_MODEL_LABELS[selected_model]
    model_description = ATTRIBUTION_MODEL_DESCRIPTIONS[selected_model]

    total_pipeline = sum(r.get("pipeline_value") or 0 for r in data)
    total_spend = sum(r.get("total_spend") or 0 for r in data)
    total_deals = sum(r.get("deals_count") or 0 for r in data)
    collected_revenue = sum(r.get("collected_revenue") or 0 for r in data)
    top_channel = data[0]["channel"] if data else "Unknown"
    report_month = data[0]["report_month"] if data else "Unknown"
    has_payment_data = any(r.get("collected_revenue") is not None for r in data)

    high_refund_channels = [
        r["channel"] for r in data if (r.get("refund_rate") or 0) > 0.10
    ]
    refund_note = (
        f"- Channels with refund rate > 10%: {', '.join(high_refund_channels)}"
        if high_refund_channels
        else ""
    )

    payment_summary = (
        f"""
- Collected revenue (actual cash): ${collected_revenue:,.0f}
- Pipeline vs collected gap: ${total_pipeline - collected_revenue:,.0f}
{refund_note}
"""
        if has_payment_data
        else ""
    )

    return f"""You are a marketing analytics consultant writing a monthly attribution report for {config.client_name}.

Attribution model used: {model_label}
Model rationale: {model_description}

Here is their channel performance data for {report_month}:

{data_str}

Summary:
- Total pipeline value: ${total_pipeline:,.0f}
- Total ad spend: ${total_spend:,.0f}
- Total deals: {total_deals}
- Top performing channel: {top_channel}
{payment_summary}
Write a professional monthly attribution report with the following sections:

1. EXECUTIVE SUMMARY (2-3 sentences): High-level performance overview
2. CHANNEL BREAKDOWN: What each channel contributed, in plain English
3. KEY INSIGHTS (3-5 bullet points): The most important things to know
4. RECOMMENDATIONS (2-3 bullet points): What to do next month based on the data
5. WATCH LIST: Any anomalies or things to investigate

Guidelines:
- Write in plain, confident business language — not overly technical
- Use specific numbers from the data
- Be direct about what's working and what isn't
- If data shows "Unattributed" deals, note the importance of UTM tagging
- Briefly explain how the selected attribution model affects interpretation
- Keep the total report under 400 words
- If collected_revenue data is present, distinguish between pipeline value (deals created)
  and collected revenue (cash actually received) — these are different and both matter
- If any channel has a refund_rate above 10%, flag it as a watch item

Return your response as a JSON object with these exact keys:
{{
  "narrative": "full report text here",
  "key_findings": ["finding 1", "finding 2", "finding 3"],
  "top_channel": "channel name",
  "total_pipeline": 0.0,
  "total_spend": 0.0,
  "overall_roi": 0.0,
  "collected_revenue": 0.0,
  "refund_rate": 0.0,
  "true_roi": 0.0,
  "attribution_model": "{selected_model}"
}}

Return ONLY the JSON — no markdown, no backticks, no preamble."""


_SYSTEM_PROMPT = (
    "You are a marketing analytics consultant who writes monthly attribution reports "
    "for digital agencies. Your reports are concise, data-driven, and written in plain "
    "business language. You always return valid JSON — no markdown, no backticks, no preamble."
)


def _call_claude(
    prompt: str,
    client_id: str = "",
    agency_id: str = "",
    run_id: str = "",
) -> dict:
    """
    Single-stage fallback narrative generator.

    Routes through the ModelGateway (not the raw SDK) so this fallback path keeps
    the same governance as the primary two-stage path: PII masking + prompt-injection
    checks on input, cost-ledger write, budget enforcement, and prompt caching.
    """
    from utils.model_gateway import call as gw_call
    from agents.intelligence.n8iv_agents import EXEC_REPORT_SCHEMA

    resp = gw_call(
        agent_name="insight-fallback",
        system_prompt=_SYSTEM_PROMPT,
        user_message=prompt,
        max_tokens=1500,
        run_id=run_id,
        client_id=client_id,
        agency_id=agency_id,
        task_type="executive_reporting",  # routes to the sonnet quality model
        response_schema=EXEC_REPORT_SCHEMA,
    )

    content = resp.text.strip()

    # Strip any accidental markdown fences
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    content = content.strip()

    return json.loads(content)


# ─── TEMPLATE FALLBACK ────────────────────────────────────────


def _build_fallback_report(
    config: ClientConfig,
    data: list[dict],
    selected_model: str,
) -> dict:
    """
    Generates a structured but non-narrative report from raw channel_performance
    data when the Claude API is unavailable. No prose — metrics only.
    """
    total_pipeline = sum(r.get("pipeline_value") or 0 for r in data)
    total_spend = sum(r.get("total_spend") or 0 for r in data)
    total_deals = sum(r.get("deals_count") or 0 for r in data)
    collected_revenue = sum(r.get("collected_revenue") or 0 for r in data)
    overall_roi = round(total_pipeline / total_spend, 2) if total_spend else 0.0
    true_roi = round(collected_revenue / total_spend, 2) if total_spend else 0.0
    top_channel = data[0]["channel"] if data else "Unknown"
    report_month = data[0].get("report_month", "Unknown") if data else "Unknown"

    channel_lines = [
        f"• {r['channel']}: {int(r.get('deals_count') or 0)} deals, "
        f"${(r.get('pipeline_value') or 0):,.0f} pipeline, "
        f"${(r.get('total_spend') or 0):,.0f} spend"
        for r in data
    ]

    narrative = (
        f"[AUTO-GENERATED — AI narrative unavailable]\n\n"
        f"Period: {report_month} | Model: {selected_model}\n\n"
        f"Total pipeline: ${total_pipeline:,.0f} across {total_deals} deals. "
        f"Total ad spend: ${total_spend:,.0f}. Overall ROI: {overall_roi}x. "
        f"Top channel: {top_channel}.\n\n"
        "Channel breakdown:\n" + "\n".join(channel_lines)
    )

    key_findings = [
        f"Top channel: {top_channel}",
        f"Total pipeline value: ${total_pipeline:,.0f}",
        f"Total ad spend: ${total_spend:,.0f} — ROI: {overall_roi}x",
    ]
    if collected_revenue:
        key_findings.append(
            f"Collected revenue: ${collected_revenue:,.0f} — True ROI: {true_roi}x"
        )

    return {
        "narrative": narrative,
        "key_findings": key_findings,
        "top_channel": top_channel,
        "total_pipeline": total_pipeline,
        "total_spend": total_spend,
        "overall_roi": overall_roi,
        "collected_revenue": collected_revenue,
        "refund_rate": 0.0,
        "true_roi": true_roi,
        "attribution_model": selected_model,
    }


# ─── MAIN FUNCTION ────────────────────────────────────────────


def generate_insight_report(
    client_id: str,
    attribution_model: str | None = None,
) -> InsightReport:
    """
    Full pipeline:
    1. Fetch channel_performance from Databricks
    2. Build prompt with data
    3. Call Claude API (falls back to template report on failure)
    4. Return structured InsightReport
    """
    config = get_client(client_id)
    selected_model = normalize_model(attribution_model or config.attribution_model)
    logger.info(f"[Insight] Generating report for {config.client_name}")

    # 1. Fetch data
    data = _fetch_channel_performance(config)
    if not data:
        logger.warning("[Insight] No channel performance data found")
        return InsightReport(
            client_id=client_id,
            client_name=config.client_name,
            report_month="N/A",
            narrative="No attribution data available for this period.",
            attribution_model=selected_model,
            generated_at=datetime.utcnow().isoformat(),
        )

    # 2. Build prompt and call Claude (two-stage via N8iV agents, fallback to single-stage)
    logger.info(
        "[Insight] Generating report via N8iV revenue-analyst + executive-reporting agents..."
    )
    try:
        from agents.intelligence.n8iv_agents import (
            run_revenue_analyst_agent,
            run_executive_reporting_agent,
        )

        analyst_output = run_revenue_analyst_agent(
            client_id=client_id,
            client_name=config.client_name,
            channel_data=data,
            attribution_model=selected_model,
        )
        claude_response = run_executive_reporting_agent(
            client_id=client_id,
            client_name=config.client_name,
            analyst_output=analyst_output,
            channel_data=data,
            attribution_model=selected_model,
        )
    except Exception as exc:
        logger.warning(
            f"[Insight] N8iV two-stage agents failed ({exc!r}) — "
            "falling back to single-stage prompt"
        )
        prompt = _build_prompt(config, data, selected_model)
        try:
            claude_response = _call_claude(
                prompt,
                client_id=client_id,
                agency_id=getattr(config, "agency_id", ""),
            )
        except Exception as exc2:
            logger.warning(
                f"[Insight] Claude API unavailable ({exc2!r}) — "
                "falling back to template report"
            )
            claude_response = _build_fallback_report(config, data, selected_model)

    # 4. Build report
    report_month = data[0].get("report_month", "")
    report = InsightReport(
        client_id=client_id,
        client_name=config.client_name,
        report_month=report_month,
        narrative=claude_response.get("narrative", ""),
        key_findings=claude_response.get("key_findings", []),
        top_channel=claude_response.get("top_channel", ""),
        total_pipeline=claude_response.get("total_pipeline", 0.0),
        total_spend=claude_response.get("total_spend", 0.0),
        overall_roi=claude_response.get("overall_roi", 0.0),
        collected_revenue=claude_response.get("collected_revenue", 0.0),
        refund_rate=claude_response.get("refund_rate", 0.0),
        true_roi=claude_response.get("true_roi", 0.0),
        attribution_model=selected_model,
        generated_at=datetime.utcnow().isoformat(),
    )

    logger.info(f"[Insight] Report generated | top channel: {report.top_channel}")
    return report


# ─── CLI ENTRYPOINT ───────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser()
    parser.add_argument("--client", type=str, default="demo_client")
    parser.add_argument("--attribution-model", type=str, default=None)
    args = parser.parse_args()

    report = generate_insight_report(
        client_id=args.client,
        attribution_model=args.attribution_model,
    )

    print("\n" + "=" * 60)
    print(f"ATTRIBUTION REPORT | {report.client_name} | {report.report_month}")
    print("=" * 60)
    print(report.narrative)
    print("\nKEY FINDINGS:")
    for f in report.key_findings:
        print(f"  • {f}")
    print(f"\nTop Channel:     {report.top_channel}")
    print(f"Total Pipeline:  ${report.total_pipeline:,.0f}")
    print(f"Total Spend:     ${report.total_spend:,.0f}")
    print(f"Overall ROI:     {report.overall_roi}x")
    print("=" * 60)
