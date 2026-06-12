"""
agents/a2a/dispatcher.py
------------------------
Local dispatcher for agent calls (Phase 6 stub).

Currently routes to local Python functions. In a future phase this
will support network dispatch via the A2A protocol.
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


class AgentDispatcher:
    def dispatch(self, agent_id: str, input_data: dict) -> dict | str:
        """
        Dispatch a task to a named agent. Returns the agent's output.

        Phase 6: local function routing only.
        Future: network routing via A2A protocol.
        """
        dispatch_map = {
            "data-quality": self._data_quality,
            "revenue-analyst": self._revenue_analyst,
            "executive-reporting": self._executive_reporting,
            "governance-reviewer": self._governance_reviewer,
        }

        handler = dispatch_map.get(agent_id)
        if not handler:
            raise ValueError(f"Unknown agent_id: {agent_id}. "
                             f"Available: {list(dispatch_map.keys())}")

        logger.info(f"[Dispatcher] Dispatching to {agent_id}")
        return handler(input_data)

    def _data_quality(self, inp: dict) -> dict:
        from agents.intelligence.n8iv_agents import run_data_quality_agent
        return run_data_quality_agent(
            client_id=inp.get("client_id", ""),
            validation_reports=inp.get("validation_reports", []),
            ingest_summary=inp.get("ingest_summary", {}),
        )

    def _revenue_analyst(self, inp: dict) -> str:
        from agents.intelligence.n8iv_agents import run_revenue_analyst_agent
        return run_revenue_analyst_agent(
            client_id=inp.get("client_id", ""),
            client_name=inp.get("client_name", ""),
            channel_data=inp.get("channel_data", []),
            attribution_model=inp.get("attribution_model", "last_touch"),
        )

    def _executive_reporting(self, inp: dict) -> dict:
        from agents.intelligence.n8iv_agents import run_executive_reporting_agent
        return run_executive_reporting_agent(
            client_id=inp.get("client_id", ""),
            client_name=inp.get("client_name", ""),
            analyst_output=inp.get("analyst_output", ""),
            channel_data=inp.get("channel_data", []),
            attribution_model=inp.get("attribution_model", "last_touch"),
        )

    def _governance_reviewer(self, inp: dict) -> list[str]:
        from agents.intelligence.n8iv_agents import run_governance_review
        return run_governance_review(
            client_id=inp.get("client_id", ""),
            client_name=inp.get("client_name", ""),
            report_narrative=inp.get("report_narrative", ""),
            report_json=inp.get("report_json", {}),
        )
