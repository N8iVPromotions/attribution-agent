"""
agents/a2a/agent_card.py
------------------------
Agent cards per Google A2A draft spec (infrastructure stub).

Each card documents an agent's interface machine-readably so a future
dispatcher can route tasks across agents or external systems.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class AgentCard:
    agent_id: str
    name: str
    description: str
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)
    model: str = ""
    version: str = "1.0.0"

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "capabilities": self.capabilities,
            "model": self.model,
            "version": self.version,
        }


AGENT_CARDS: dict[str, AgentCard] = {
    "data-quality": AgentCard(
        agent_id="data-quality",
        name="Data Quality Triage Agent",
        description="Post-ingest triage — classifies validation findings into issues, warnings, and escalations.",
        input_schema={
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
                "ingest_summary": {"type": "object"},
                "validation_findings": {"type": "array", "items": {"type": "string"}},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "issues": {"type": "array"},
                "warnings": {"type": "array"},
                "escalations": {"type": "array"},
                "passed": {"type": "boolean"},
            },
        },
        capabilities=["data_validation", "json_output"],
        model="claude-haiku-4-5-20251001",
    ),
    "revenue-analyst": AgentCard(
        agent_id="revenue-analyst",
        name="Revenue Analyst Agent",
        description="Stage 1: structured channel performance analysis for the executive reporting agent.",
        input_schema={
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
                "client_name": {"type": "string"},
                "channel_data": {"type": "array"},
                "attribution_model": {"type": "string"},
            },
        },
        output_schema={"type": "string", "description": "Structured analyst briefing text"},
        capabilities=["channel_analysis", "text_output"],
        model="claude-sonnet-4-6",
    ),
    "executive-reporting": AgentCard(
        agent_id="executive-reporting",
        name="Executive Reporting Agent",
        description="Stage 2: converts analyst findings into a client-facing JSON attribution report.",
        input_schema={
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
                "analyst_output": {"type": "string"},
                "channel_data": {"type": "array"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "narrative": {"type": "string"},
                "key_findings": {"type": "array"},
                "top_channel": {"type": "string"},
                "total_pipeline": {"type": "number"},
                "overall_roi": {"type": "number"},
            },
        },
        capabilities=["report_generation", "json_output"],
        model="claude-sonnet-4-6",
    ),
    "governance-reviewer": AgentCard(
        agent_id="governance-reviewer",
        name="Governance Reviewer Agent",
        description="Pre-send advisory check for evidence quality, privacy, and tone.",
        input_schema={
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
                "report_narrative": {"type": "string"},
                "report_json": {"type": "object"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "decision": {"type": "string"},
                "warnings": {"type": "array"},
                "critical_issues": {"type": "array"},
            },
        },
        capabilities=["compliance_review", "json_output"],
        model="claude-haiku-4-5-20251001",
    ),
}
