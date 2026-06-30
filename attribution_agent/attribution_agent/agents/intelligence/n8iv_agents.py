"""
agents/intelligence/n8iv_agents.py
------------------------------------
Python runner for N8iV specialized Claude agents.

Loads agent system prompts from .claude/agents/ and calls the Anthropic SDK.
Each public function corresponds to one injection point in the pipeline:

  run_data_quality_agent(client_id, validation_reports)
      → called post-ingest in ingest_flow.py; returns findings dict

  run_revenue_analyst_agent(client_id, channel_data, attribution_model)
      → first stage of two-stage insight generation in insight_agent.py

  run_executive_reporting_agent(client_id, analyst_output, channel_data)
      → second stage; returns the JSON expected by InsightReport

  run_governance_review(client_id, report_narrative, report_json)
      → called pre-send in agency_flow.py; returns advisory warnings list
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Path to .claude/agents/ — set N8IV_AGENTS_DIR env var to override (required in Databricks Jobs).
# Databricks Jobs: set N8IV_AGENTS_DIR to the absolute path where the bundle deploys agent files.
# Default walks up from this file to the repo root (works locally and in Databricks Apps).
def _find_agents_dir() -> Path:
    if env_path := os.environ.get("N8IV_AGENTS_DIR"):
        return Path(env_path)
    # Walk upward from this file looking for .claude/agents/
    candidate = Path(__file__).resolve()
    for _ in range(8):
        candidate = candidate.parent
        agents_dir = candidate / ".claude" / "agents"
        if agents_dir.is_dir():
            return agents_dir
    # Last resort: relative to the bundle deploy path
    return Path("/Workspace/Users/zajen@n8ivpromotions.com/.bundle/attribution-agent/production") / ".claude" / "agents"

_AGENTS_DIR = _find_agents_dir()

_MAX_TOKENS = 2000

_TASK_TYPE_MAP: dict[str, str] = {
    "data-quality": "data_quality",
    "revenue-analyst": "revenue_analyst",
    "executive-reporting": "executive_reporting",
    "governance-reviewer": "governance_review",
}

# ─── STRUCTURED-OUTPUT SCHEMAS ────────────────────────────────
# Passed to the gateway as forced tool-use schemas so the JSON-contract agents
# return guaranteed-valid JSON (no markdown-fence / json.loads fragility).

_str_array = {"type": "array", "items": {"type": "string"}}

DATA_QUALITY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "issues": _str_array,
        "warnings": _str_array,
        "escalations": _str_array,
        "passed": {"type": "boolean"},
    },
    "required": ["issues", "warnings", "escalations", "passed"],
}

EXEC_REPORT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "narrative": {"type": "string"},
        "key_findings": _str_array,
        "top_channel": {"type": "string"},
        "total_pipeline": {"type": "number"},
        "total_spend": {"type": "number"},
        "overall_roi": {"type": "number"},
        "collected_revenue": {"type": "number"},
        "refund_rate": {"type": "number"},
        "true_roi": {"type": "number"},
        "attribution_model": {"type": "string"},
    },
    "required": [
        "narrative", "key_findings", "top_channel", "total_pipeline",
        "total_spend", "overall_roi", "attribution_model",
    ],
}

GOVERNANCE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["READY FOR HUMAN REVIEW", "REVISE BEFORE HUMAN REVIEW"],
        },
        "warnings": _str_array,
        "critical_issues": _str_array,
    },
    "required": ["decision", "warnings", "critical_issues"],
}


def _load_system_prompt(agent_name: str) -> str:
    """Read the agent .md file and strip the YAML frontmatter, returning only the body."""
    path = _AGENTS_DIR / f"{agent_name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Agent definition not found: {path}")
    content = path.read_text()
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return content.strip()


def _call_agent(
    agent_name: str,
    user_message: str,
    max_tokens: int = _MAX_TOKENS,
    run_id: str = "",
    client_id: str = "",
    agency_id: str = "",
    response_schema: dict | None = None,
) -> str:
    """Route agent call through ModelGateway. Returns raw text response.

    When response_schema is given, the gateway forces tool-use so the returned
    text is guaranteed schema-valid JSON.
    """
    from utils.model_gateway import call as gw_call

    system_prompt = _load_system_prompt(agent_name)
    task_type = _TASK_TYPE_MAP.get(agent_name, agent_name.replace("-", "_"))

    resp = gw_call(
        agent_name=agent_name,
        system_prompt=system_prompt,
        user_message=user_message,
        max_tokens=max_tokens,
        run_id=run_id,
        client_id=client_id,
        agency_id=agency_id,
        task_type=task_type,
        response_schema=response_schema,
    )
    logger.info(
        f"[N8iV/{agent_name}] tokens — in: {resp.input_tokens}, "
        f"out: {resp.output_tokens}, cache_read: {resp.cache_read_tokens}"
    )
    return resp.text


def _parse_json_response(text: str) -> dict:
    """Strip accidental markdown fences and parse JSON."""
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


# ─── DATA QUALITY AGENT ───────────────────────────────────────

def run_data_quality_agent(
    client_id: str,
    validation_reports: list,
    ingest_summary: dict,
) -> dict:
    """
    Post-ingest hook. Summarizes validation findings through the data-quality agent lens.

    Returns a findings dict with keys: issues, warnings, escalations, passed.
    On any error, returns a safe fallback with passed=True to keep the pipeline running.
    """
    try:
        issues_text = []
        for report in validation_reports:
            if report is None:
                continue
            if hasattr(report, "errors") and report.errors:
                issues_text.extend(report.errors)
            if hasattr(report, "warnings") and report.warnings:
                issues_text.extend(report.warnings)

        if not issues_text:
            logger.info(f"[DataQuality/{client_id}] No issues — skipping agent call")
            return {"passed": True, "issues": [], "warnings": [], "escalations": []}

        message = (
            f"Client: {client_id}\n\n"
            f"Ingest summary: {json.dumps(ingest_summary, default=str)}\n\n"
            f"Validation findings from this ingestion run:\n"
            + "\n".join(f"- {i}" for i in issues_text)
            + "\n\nReturn a JSON object with keys: "
            '"issues" (list of strings), "warnings" (list of strings), '
            '"escalations" (list of strings), "passed" (bool). '
            "No markdown, no backticks."
        )

        raw = _call_agent(
            "data-quality", message, max_tokens=800,
            client_id=client_id, response_schema=DATA_QUALITY_SCHEMA,
        )
        result = _parse_json_response(raw)
        logger.info(
            f"[DataQuality/{client_id}] findings — "
            f"issues: {len(result.get('issues', []))}, "
            f"escalations: {len(result.get('escalations', []))}"
        )
        return result

    except Exception as exc:
        logger.warning(f"[DataQuality/{client_id}] agent error (non-fatal): {exc!r}")
        return {"passed": True, "issues": [], "warnings": [], "escalations": []}


# ─── REVENUE ANALYST AGENT ────────────────────────────────────

def run_revenue_analyst_agent(
    client_id: str,
    client_name: str,
    channel_data: list[dict],
    attribution_model: str,
) -> str:
    """
    Stage 1 of two-stage insight generation.
    Analyzes channel performance data and returns a structured analysis string
    to be consumed by the executive-reporting agent.
    """
    data_str = json.dumps(channel_data, indent=2, default=str)

    total_pipeline = sum(r.get("pipeline_value") or 0 for r in channel_data)
    total_spend    = sum(r.get("total_spend") or 0 for r in channel_data)
    total_deals    = sum(r.get("deals_count") or 0 for r in channel_data)
    report_month   = channel_data[0].get("report_month", "Unknown") if channel_data else "Unknown"

    # Prepend any cross-run client memories as context
    memory_context = ""
    try:
        from utils.memory_store import MemoryStore
        memory_context = MemoryStore().recall_as_context(client_id, limit=5)
    except Exception:
        pass

    context_prefix = f"{memory_context}\n\n" if memory_context else ""

    message = (
        f"{context_prefix}"
        f"Client: {client_name} ({client_id})\n"
        f"Period: {report_month}\n"
        f"Attribution model: {attribution_model}\n\n"
        f"Channel performance data:\n{data_str}\n\n"
        f"Summary: {total_deals} deals, ${total_pipeline:,.0f} pipeline, "
        f"${total_spend:,.0f} spend\n\n"
        "Analyze this data. Identify the 3-5 most decision-relevant findings "
        "about pipeline creation, channel efficiency, anomalies, and budget implications. "
        "Be specific with numbers. Note data-quality limitations where applicable. "
        "Return structured analysis text (not JSON) that a reporting agent will use "
        "to write the final client report."
    )

    return _call_agent("revenue-analyst", message, max_tokens=1000, client_id=client_id)


# ─── EXECUTIVE REPORTING AGENT ────────────────────────────────

def run_executive_reporting_agent(
    client_id: str,
    client_name: str,
    analyst_output: str,
    channel_data: list[dict],
    attribution_model: str,
) -> dict:
    """
    Stage 2 of two-stage insight generation.
    Takes revenue analyst output and produces the final JSON InsightReport payload.
    """
    total_pipeline    = sum(r.get("pipeline_value") or 0 for r in channel_data)
    total_spend       = sum(r.get("total_spend") or 0 for r in channel_data)
    collected_revenue = sum(r.get("collected_revenue") or 0 for r in channel_data)
    top_channel       = channel_data[0]["channel"] if channel_data else "Unknown"
    overall_roi       = round(total_pipeline / total_spend, 2) if total_spend else 0.0
    true_roi          = round(collected_revenue / total_spend, 2) if total_spend else 0.0
    report_month      = channel_data[0].get("report_month", "Unknown") if channel_data else "Unknown"

    message = (
        f"Client: {client_name} ({client_id})\n"
        f"Period: {report_month}\n"
        f"Attribution model: {attribution_model}\n\n"
        f"Revenue Analyst findings:\n{analyst_output}\n\n"
        f"Pre-calculated summary metrics:\n"
        f"- top_channel: {top_channel}\n"
        f"- total_pipeline: {total_pipeline}\n"
        f"- total_spend: {total_spend}\n"
        f"- overall_roi: {overall_roi}\n"
        f"- collected_revenue: {collected_revenue}\n"
        f"- true_roi: {true_roi}\n\n"
        "Write a professional monthly attribution report for this client. "
        "Keep it under 350 words, plain business language, specific numbers. "
        "Return ONLY a JSON object with these exact keys:\n"
        '{"narrative": "...", "key_findings": ["..."], "top_channel": "...", '
        '"total_pipeline": 0.0, "total_spend": 0.0, "overall_roi": 0.0, '
        '"collected_revenue": 0.0, "refund_rate": 0.0, "true_roi": 0.0, '
        f'"attribution_model": "{attribution_model}"}}\n\n'
        "No markdown, no backticks, no preamble."
    )

    raw = _call_agent(
        "executive-reporting", message, max_tokens=1500,
        client_id=client_id, response_schema=EXEC_REPORT_SCHEMA,
    )
    return _parse_json_response(raw)


# ─── GOVERNANCE REVIEWER AGENT ────────────────────────────────

def run_governance_review(
    client_id: str,
    client_name: str,
    report_narrative: str,
    report_json: dict,
) -> list[str]:
    """
    Pre-send governance check. Returns a list of advisory warning strings.
    An empty list means no issues. Never blocks the pipeline — advisory only.
    """
    try:
        message = (
            f"Client: {client_name} ({client_id})\n\n"
            f"Draft report narrative:\n{report_narrative}\n\n"
            f"Report metrics: {json.dumps(report_json, default=str)}\n\n"
            "Review this draft attribution report for: evidence quality, "
            "unsupported certainty, attribution-vs-causality confusion, "
            "privacy concerns, and tone. "
            "Return ONLY a JSON object: "
            '{"decision": "READY FOR HUMAN REVIEW|REVISE BEFORE HUMAN REVIEW", '
            '"warnings": ["...", "..."], "critical_issues": ["..."]}. '
            "No markdown, no backticks."
        )

        raw = _call_agent(
            "governance-reviewer", message, max_tokens=600,
            client_id=client_id, response_schema=GOVERNANCE_SCHEMA,
        )
        result = _parse_json_response(raw)
        warnings = result.get("warnings", []) + result.get("critical_issues", [])
        if warnings:
            logger.warning(
                f"[Governance/{client_id}] {len(warnings)} advisory item(s): "
                + "; ".join(warnings[:3])
            )
        return warnings

    except Exception as exc:
        logger.warning(f"[Governance/{client_id}] review error (non-fatal): {exc!r}")
        return []
