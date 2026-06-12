"""
config/budget_config.py
-----------------------
Token-spend budgets per agency/run. Triggers Telegram alert at 80% usage.
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field


@dataclass
class BudgetConfig:
    monthly_token_limit: int = 5_000_000
    per_run_token_limit: int = 200_000
    alert_threshold_pct: float = 0.80

    def tokens_to_usd(self, input_tokens: int, output_tokens: int, model: str) -> float:
        """Rough cost estimate. Update pricing as Anthropic publishes changes."""
        pricing = {
            "claude-haiku-4-5": (0.80, 4.00),     # per 1M tokens (in, out)
            "claude-sonnet-4-6": (3.00, 15.00),
            "claude-opus-4-8": (15.00, 75.00),
        }
        in_price, out_price = pricing.get(model, (3.00, 15.00))
        return (input_tokens / 1_000_000 * in_price) + (output_tokens / 1_000_000 * out_price)


# Default budget applied to all agencies unless overridden below.
DEFAULT_BUDGET = BudgetConfig()

# Per-agency overrides: agency_id → BudgetConfig
BUDGET_REGISTRY: dict[str, BudgetConfig] = {}


def get_budget(agency_id: str) -> BudgetConfig:
    return BUDGET_REGISTRY.get(agency_id, DEFAULT_BUDGET)


def send_budget_alert(agency_id: str, pct_used: float, tokens_used: int, limit: int) -> None:
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
