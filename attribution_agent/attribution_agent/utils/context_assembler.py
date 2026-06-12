"""
utils/context_assembler.py
--------------------------
Single source of truth for all context passed to agent prompts.

Gathers client config, agency config, recent memories, last 3 run
summaries, and MoM trends from channel_performance.
"""
from __future__ import annotations
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_OPS_SCHEMA = os.environ.get("ATTRIBUTION_OPS_SCHEMA", "workspace.attribution_ops")


@dataclass
class ClientContext:
    client_id: str
    client_name: str
    agency_id: str
    agency_name: str
    attribution_model: str
    lookback_days: int
    prior_memories: str = ""
    prior_run_summaries: list[dict] = field(default_factory=list)
    mom_trend: dict = field(default_factory=dict)

    def as_prompt_block(self) -> str:
        parts = [
            f"Client: {self.client_name} ({self.client_id})",
            f"Agency: {self.agency_name} ({self.agency_id})",
            f"Attribution model: {self.attribution_model}",
            f"Lookback: {self.lookback_days} days",
        ]
        if self.prior_memories:
            parts.append(self.prior_memories)
        if self.prior_run_summaries:
            parts.append("\n--- Prior run summaries (most recent first) ---")
            for run in self.prior_run_summaries[:3]:
                parts.append(
                    f"  {str(run.get('started_at', ''))[:10]}: "
                    f"pipeline=${run.get('total_pipeline', 0):,.0f}, "
                    f"top={run.get('top_channel', 'n/a')}, "
                    f"status={run.get('status', 'n/a')}"
                )
            parts.append("--- End prior runs ---")
        if self.mom_trend:
            parts.append(
                f"\nMonth-over-month spend change: "
                f"{self.mom_trend.get('spend_change_pct', 0):.1f}%, "
                f"pipeline change: {self.mom_trend.get('pipeline_change_pct', 0):.1f}%"
            )
        return "\n".join(parts)


class ClientContextAssembler:
    def assemble(self, client_id: str, run_id: str = "") -> ClientContext:
        from config.client_config import get_client
        from config.agency_config import AGENCY_REGISTRY

        cfg = get_client(client_id)
        agency_cfg = AGENCY_REGISTRY.get(cfg.agency_id)
        agency_name = agency_cfg.agency_name if agency_cfg else cfg.agency_id

        prior_memories = ""
        try:
            from utils.memory_store import MemoryStore
            prior_memories = MemoryStore().recall_as_context(client_id, limit=5)
        except Exception as exc:
            logger.debug(f"[ContextAssembler] memory recall failed: {exc}")

        prior_runs = self._fetch_prior_runs(client_id)
        mom_trend = self._compute_mom_trend(cfg.databricks_schema)

        return ClientContext(
            client_id=client_id,
            client_name=cfg.client_name,
            agency_id=cfg.agency_id,
            agency_name=agency_name,
            attribution_model=cfg.attribution_model,
            lookback_days=cfg.lookback_days,
            prior_memories=prior_memories,
            prior_run_summaries=prior_runs,
            mom_trend=mom_trend,
        )

    def _fetch_prior_runs(self, client_id: str) -> list[dict]:
        try:
            from utils.databricks_writer import _get_connection, _is_databricks, _get_spark
            query = (
                f"SELECT started_at, total_pipeline, top_channel, status "
                f"FROM {_OPS_SCHEMA}.pipeline_runs "
                f"WHERE client_id = '{client_id}' AND status = 'success' "
                f"ORDER BY started_at DESC LIMIT 3"
            )
            if _is_databricks():
                rows = _get_spark().sql(query).collect()
                return [r.asDict() for r in rows]
            conn = _get_connection()
            cursor = conn.cursor()
            cursor.execute(query)
            cols = [d[0] for d in cursor.description]
            result = [dict(zip(cols, r)) for r in cursor.fetchall()]
            cursor.close()
            conn.close()
            return result
        except Exception as exc:
            logger.debug(f"[ContextAssembler] prior runs fetch failed: {exc}")
            return []

    def _compute_mom_trend(self, schema: str) -> dict:
        try:
            from utils.databricks_writer import _get_connection, _is_databricks, _get_spark
            query = (
                f"SELECT report_month, SUM(total_spend) AS spend, SUM(pipeline_value) AS pipeline "
                f"FROM {schema}.channel_performance "
                f"GROUP BY report_month ORDER BY report_month DESC LIMIT 2"
            )
            if _is_databricks():
                rows = _get_spark().sql(query).collect()
                data = [r.asDict() for r in rows]
            else:
                conn = _get_connection()
                cursor = conn.cursor()
                cursor.execute(query)
                cols = [d[0] for d in cursor.description]
                data = [dict(zip(cols, r)) for r in cursor.fetchall()]
                cursor.close()
                conn.close()

            if len(data) < 2:
                return {}
            curr, prev = data[0], data[1]

            def pct_change(curr_val, prev_val):
                if not prev_val:
                    return 0.0
                return round((curr_val - prev_val) / prev_val * 100, 1)

            return {
                "spend_change_pct": pct_change(curr.get("spend", 0), prev.get("spend", 0)),
                "pipeline_change_pct": pct_change(curr.get("pipeline", 0), prev.get("pipeline", 0)),
            }
        except Exception as exc:
            logger.debug(f"[ContextAssembler] MoM trend failed: {exc}")
            return {}
