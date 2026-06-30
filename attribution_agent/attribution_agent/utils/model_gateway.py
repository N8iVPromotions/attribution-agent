"""
utils/model_gateway.py
----------------------
Single entry point for all Claude API calls.

Responsibilities:
- Model routing (haiku for cheap classification, sonnet for quality tasks)
- Cost ledger writes to cost_ledger Delta table
- Budget enforcement with Telegram alert at 80% usage
- Semantic caching for eligible tasks (data_quality, governance_review)
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import anthropic

logger = logging.getLogger(__name__)

_OPS_SCHEMA = os.environ.get("ATTRIBUTION_OPS_SCHEMA", "workspace.attribution_ops")

# Model routing by task type
_MODEL_ROUTING: dict[str, str] = {
    "data_quality": "claude-haiku-4-5-20251001",
    "governance_review": "claude-haiku-4-5-20251001",
    "revenue_analyst": "claude-sonnet-4-6",
    "executive_reporting": "claude-sonnet-4-6",
    "outreach": "claude-sonnet-4-6",
}
_DEFAULT_MODEL = "claude-sonnet-4-6"

# Task types eligible for semantic caching (deterministic classification tasks)
_CACHEABLE_TASKS = {"data_quality", "governance_review"}


@dataclass
class GatewayResponse:
    text: str
    model_id: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    from_cache: bool = False


def select_model(task_type: str) -> str:
    return _MODEL_ROUTING.get(task_type, _DEFAULT_MODEL)


def _cache_key(model: str, agent_name: str, message: str) -> str:
    raw = f"{model}:{agent_name}:{message[:200]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _check_cache(cache_key: str) -> str | None:
    try:
        from utils.databricks_writer import _get_connection, _is_databricks, _get_spark
        now = datetime.now(timezone.utc).isoformat()
        query = (
            f"SELECT response_text FROM {_OPS_SCHEMA}.semantic_cache "
            f"WHERE cache_key = '{cache_key}' "
            f"AND expires_at > CAST('{now}' AS TIMESTAMP) LIMIT 1"
        )
        if _is_databricks():
            rows = _get_spark().sql(query).collect()
            if rows:
                return rows[0]["response_text"]
        else:
            conn = _get_connection()
            cursor = conn.cursor()
            cursor.execute(query)
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            if row:
                return row[0]
    except Exception as exc:
        logger.debug(f"[ModelGateway] cache check failed: {exc}")
    return None


def _write_cache(cache_key: str, agent_name: str, model_id: str, response_text: str) -> None:
    try:
        import pandas as pd
        from datetime import timedelta
        from utils.databricks_writer import _upsert_dataframe
        now = datetime.now(timezone.utc)
        df = pd.DataFrame([{
            "cache_key": cache_key,
            "created_at": now,
            "expires_at": now + timedelta(hours=24),
            "input_hash": cache_key,
            "agent_name": agent_name,
            "model_id": model_id,
            "response_text": response_text,
            "input_tokens": 0,
            "output_tokens": 0,
            "hit_count": 1,
        }])
        _upsert_dataframe(df, _OPS_SCHEMA, "semantic_cache", ["cache_key"])
    except Exception as exc:
        logger.debug(f"[ModelGateway] cache write failed: {exc}")


def _write_cost_ledger(
    run_id: str,
    client_id: str,
    agency_id: str,
    agent_name: str,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    cost_usd: float,
    task_type: str,
) -> None:
    try:
        import pandas as pd
        from utils.databricks_writer import _upsert_dataframe
        df = pd.DataFrame([{
            "ledger_id": uuid.uuid4().hex,
            "event_time": datetime.now(timezone.utc),
            "run_id": run_id,
            "client_id": client_id,
            "agency_id": agency_id,
            "agent_name": agent_name,
            "model_id": model_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "cost_usd_estimate": cost_usd,
            "prompt_version": os.environ.get("GIT_PROMPT_TAG", "dev"),
            "task_type": task_type,
        }])
        _upsert_dataframe(df, _OPS_SCHEMA, "cost_ledger", ["ledger_id"])
    except Exception as exc:
        logger.debug(f"[ModelGateway] cost_ledger write failed: {exc}")


def _check_budget(agency_id: str, new_tokens: int) -> None:
    """Sends a Telegram alert if monthly token usage crosses 80% after this call."""
    try:
        from config.budget_config import get_budget, send_budget_alert
        from utils.databricks_writer import _get_connection, _is_databricks, _get_spark
        budget = get_budget(agency_id)

        first_of_month = datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        query = (
            f"SELECT COALESCE(SUM(input_tokens + output_tokens), 0) "
            f"FROM {_OPS_SCHEMA}.cost_ledger "
            f"WHERE agency_id = '{agency_id}' "
            f"AND event_time >= CAST('{first_of_month}' AS TIMESTAMP)"
        )
        if _is_databricks():
            rows = _get_spark().sql(query).collect()
            used = int(rows[0][0]) if rows else 0
        else:
            conn = _get_connection()
            cursor = conn.cursor()
            cursor.execute(query)
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            used = int(row[0]) if row and row[0] else 0

        total = used + new_tokens
        pct = total / budget.monthly_token_limit
        if pct >= budget.alert_threshold_pct and (total - new_tokens) / budget.monthly_token_limit < budget.alert_threshold_pct:
            send_budget_alert(agency_id, pct, total, budget.monthly_token_limit)

        if total > budget.monthly_token_limit:
            raise RuntimeError(
                f"[ModelGateway] Monthly token budget exceeded for {agency_id}: "
                f"{total:,} > {budget.monthly_token_limit:,}"
            )
    except RuntimeError:
        raise
    except Exception as exc:
        logger.debug(f"[ModelGateway] budget check failed: {exc}")


def call(
    agent_name: str,
    system_prompt: str,
    user_message: str,
    max_tokens: int = 2048,
    run_id: str = "",
    client_id: str = "",
    agency_id: str = "",
    task_type: str = "",
    response_schema: dict | None = None,
) -> GatewayResponse:
    """
    Route an agent call through the model gateway.

    Handles: model selection, semantic cache check, API call,
    cost ledger write, budget alert.

    When `response_schema` is provided, the call uses forced tool-use so the
    model MUST return JSON matching that schema (no markdown fences, no parse
    gamble). The returned `.text` is the schema-valid JSON serialized as a
    string, so existing json.loads() callers keep working unchanged.
    """
    model_id = select_model(task_type or agent_name)

    # Semantic cache check for eligible tasks
    use_cache = task_type in _CACHEABLE_TASKS
    ck = _cache_key(model_id, agent_name, user_message) if use_cache else None
    if ck:
        cached = _check_cache(ck)
        if cached:
            logger.info(f"[ModelGateway] Cache HIT for {agent_name}")
            return GatewayResponse(
                text=cached,
                model_id=model_id,
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
                cost_usd=0.0,
                from_cache=True,
            )

    from config.budget_config import get_budget, DEFAULT_BUDGET
    budget = get_budget(agency_id) if agency_id else DEFAULT_BUDGET

    # Guardrails: input check
    from utils.guardrails import check_input, check_output
    input_check = check_input(user_message, agent_name)
    if input_check.blocked:
        raise RuntimeError(f"[Guardrails] Input blocked for {agent_name}: {input_check.block_reason}")
    sanitized_message = input_check.sanitized_text

    _check_budget(agency_id or "global", max_tokens)

    create_kwargs: dict = dict(
        model=model_id,
        max_tokens=max_tokens,
        # Block form with cache_control so the (stable) agent system prompt is
        # served from Anthropic's prompt cache on repeat calls within the 5-min
        # TTL — e.g. the same agent run across multiple clients in one pipeline.
        # No-op (gracefully ignored) when the prompt is below the model's cache
        # minimum; pays off once system prompts exceed it.
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": sanitized_message}],
    )

    # Forced structured output: the model must call this tool, guaranteeing the
    # response is JSON that matches the schema. Removes the markdown-fence /
    # json.loads fragility for the client-facing report agents.
    if response_schema is not None:
        create_kwargs["tools"] = [
            {
                "name": "emit_structured_report",
                "description": "Return the result strictly as JSON matching the schema.",
                "input_schema": response_schema,
            }
        ]
        create_kwargs["tool_choice"] = {"type": "tool", "name": "emit_structured_report"}

    client = anthropic.Anthropic()
    resp = client.messages.create(**create_kwargs)

    if response_schema is not None:
        # Pull the forced tool_use block and serialize its (schema-valid) input.
        tool_block = next(
            (b for b in resp.content if getattr(b, "type", None) == "tool_use"), None
        )
        text = json.dumps(tool_block.input) if tool_block is not None else ""
    else:
        text = next(
            (b.text for b in resp.content if getattr(b, "type", None) == "text"), ""
        ) if resp.content else ""

    # Guardrails: output check
    output_check = check_output(text, agent_name)
    if output_check.requires_approval:
        logger.warning(f"[ModelGateway] {agent_name} output requires human approval")
    in_tok = resp.usage.input_tokens
    out_tok = resp.usage.output_tokens
    cache_read = getattr(resp.usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(resp.usage, "cache_creation_input_tokens", 0) or 0

    cost = budget.tokens_to_usd(in_tok, out_tok, model_id)

    logger.info(
        f"[ModelGateway] {agent_name} ({model_id}) "
        f"in={in_tok} out={out_tok} cache_read={cache_read} cost=${cost:.4f}"
    )

    _write_cost_ledger(
        run_id=run_id, client_id=client_id, agency_id=agency_id,
        agent_name=agent_name, model_id=model_id,
        input_tokens=in_tok, output_tokens=out_tok,
        cache_read_tokens=cache_read, cache_write_tokens=cache_write,
        cost_usd=cost, task_type=task_type,
    )

    if ck:
        _write_cache(ck, agent_name, model_id, text)

    return GatewayResponse(
        text=text,
        model_id=model_id,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        cost_usd=cost,
    )
