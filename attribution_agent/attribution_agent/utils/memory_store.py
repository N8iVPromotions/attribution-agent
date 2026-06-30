"""
utils/memory_store.py
---------------------
Delta-backed cross-run memory for agents.

PII is masked before write. recall_as_context() returns a formatted
block ready to prepend to any Claude prompt.
"""

from __future__ import annotations
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

logger = logging.getLogger(__name__)

_OPS_SCHEMA = os.environ.get("ATTRIBUTION_OPS_SCHEMA", "workspace.attribution_ops")

MemoryType = Literal[
    "pipeline_summary",
    "channel_insight",
    "governance_flag",
    "client_preference",
    "anomaly_note",
]


class MemoryStore:
    def remember(
        self,
        client_id: str,
        memory_type: MemoryType,
        content: str,
        source_run_id: str = "",
        importance: Literal["high", "medium", "low"] = "medium",
        tags: list[str] | None = None,
        ttl_days: int = 90,
    ) -> None:
        """PII-mask content, then persist to agent_memory Delta table."""
        try:
            from utils.pii_masker import PIIMasker

            masked_content, _ = PIIMasker().mask(content)

            import json
            import pandas as pd
            from utils.databricks_writer import _upsert_dataframe

            now = datetime.now(timezone.utc)
            df = pd.DataFrame(
                [
                    {
                        "memory_id": uuid.uuid4().hex,
                        "client_id": client_id,
                        "memory_type": memory_type,
                        "content": masked_content,
                        "source_run_id": source_run_id,
                        "created_at": now,
                        "valid_until": now + timedelta(days=ttl_days),
                        "importance": importance,
                        "tags": json.dumps(tags or []),
                    }
                ]
            )
            _upsert_dataframe(df, _OPS_SCHEMA, "agent_memory", ["memory_id"])
        except Exception as exc:
            logger.debug(f"[MemoryStore] remember failed for {client_id}: {exc}")

    def recall_as_context(self, client_id: str, limit: int = 5) -> str:
        """Return the most recent memories formatted as a context block for prompts."""
        memories = self._fetch(client_id, limit)
        if not memories:
            return ""
        lines = ["--- Prior context for this client ---"]
        for m in memories:
            date = str(m.get("created_at", ""))[:10]
            lines.append(
                f"[{date}] ({m.get('memory_type', '')}) {m.get('content', '')}"
            )
        lines.append("--- End prior context ---")
        return "\n".join(lines)

    def _fetch(self, client_id: str, limit: int) -> list[dict]:
        try:
            from utils.databricks_writer import (
                _get_connection,
                _is_databricks,
                _get_spark,
            )

            now = datetime.now(timezone.utc).isoformat()
            query = (
                f"SELECT memory_id, client_id, memory_type, content, created_at, importance "
                f"FROM {_OPS_SCHEMA}.agent_memory "
                f"WHERE client_id = '{client_id}' "
                f"AND valid_until > CAST('{now}' AS TIMESTAMP) "
                f"ORDER BY importance DESC, created_at DESC "
                f"LIMIT {int(limit)}"
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
            logger.debug(f"[MemoryStore] recall failed for {client_id}: {exc}")
            return []
