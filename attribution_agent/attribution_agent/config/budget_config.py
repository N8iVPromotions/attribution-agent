"""
config/budget_config.py
-----------------------
Token-spend budgets per agency/run. Triggers Telegram alert at 80% usage.
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass
class BudgetConfig:
    monthly_token_limit: int = 5_000_000
    per_run_token_limit: int = 200_000
    daily_usd_limit: float = 25.0
    monthly_usd_limit: float = 300.0
    alert_threshold_pct: float = 0.80

    def tokens_to_usd(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str,
        *,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        """Estimate standard API cost, including five-minute prompt caching."""
        if model.startswith("claude-haiku-4-5"):
            in_price, out_price = 1.00, 5.00
        elif model.startswith("claude-sonnet-4-6"):
            in_price, out_price = 3.00, 15.00
        elif model.startswith("claude-opus-4"):
            in_price, out_price = 15.00, 75.00
        else:
            raise ValueError(f"No configured pricing for model '{model}'")
        cache_write_price = in_price * 1.25
        cache_read_price = in_price * 0.10
        return (
            input_tokens / 1_000_000 * in_price
            + output_tokens / 1_000_000 * out_price
            + cache_read_tokens / 1_000_000 * cache_read_price
            + cache_write_tokens / 1_000_000 * cache_write_price
        )


# Default budget applied to all agencies unless overridden below.
DEFAULT_BUDGET = BudgetConfig()

# Per-agency overrides: agency_id → BudgetConfig
BUDGET_REGISTRY: dict[str, BudgetConfig] = {}


def get_budget(agency_id: str) -> BudgetConfig:
    return BUDGET_REGISTRY.get(agency_id, DEFAULT_BUDGET)


def send_budget_alert(
    agency_id: str, pct_used: float, tokens_used: int, limit: int
) -> None:
    """Fire a Telegram message when budget threshold is crossed. Best-effort."""
    try:
        from agents.control.arie_bot import notify

        notify(
            f"⚠️ *Budget alert* — `{agency_id}`\n"
            f"Token usage at *{pct_used:.0%}* "
            f"({tokens_used:,} / {limit:,} tokens this month)"
        )
    except Exception:
        pass
