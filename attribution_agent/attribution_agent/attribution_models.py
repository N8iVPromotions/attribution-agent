"""
Attribution model utilities.

This module is intentionally pure Python so it can be unit tested without
Databricks or external API credentials.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import exp
from typing import Literal


AttributionModel = Literal[
    "last_touch",
    "first_touch",
    "linear",
    "time_decay",
    "u_shape",
    "w_shape",
]

ATTRIBUTION_MODEL_LABELS: dict[str, str] = {
    "last_touch": "Last Touch",
    "first_touch": "First Touch",
    "linear": "Linear",
    "time_decay": "Time Decay",
    "u_shape": "U-Shape",
    "w_shape": "W-Shape",
}

ATTRIBUTION_MODEL_DESCRIPTIONS: dict[str, str] = {
    "last_touch": (
        "Credits the final known touchpoint before conversion. Useful when the "
        "business wants to understand closing channels and near-term demand capture."
    ),
    "first_touch": (
        "Credits the first known touchpoint. Useful when the business is focused "
        "on awareness, lead creation, and top-of-funnel source quality."
    ),
    "linear": (
        "Splits credit evenly across all known touchpoints. Useful when the sales "
        "cycle has multiple meaningful interactions and no single touch should dominate."
    ),
    "time_decay": (
        "Weights later touchpoints more heavily. Useful for longer B2B cycles where "
        "recent touches are more likely to influence closed revenue."
    ),
    "u_shape": (
        "Gives 40% credit to first touch and 40% to last touch, with the remaining "
        "20% shared by middle touches. Useful when both source creation and close matter."
    ),
    "w_shape": (
        "Gives 30% credit to first touch, lead creation, and last touch, with the "
        "remaining 10% shared by other touches. Useful for sales-led B2B journeys."
    ),
}

SUPPORTED_ATTRIBUTION_MODELS = tuple(ATTRIBUTION_MODEL_LABELS.keys())


@dataclass(frozen=True)
class Touchpoint:
    touchpoint_id: str
    occurred_at: datetime
    channel: str
    campaign: str = ""
    source_platform: str = ""
    role: str = "touch"


@dataclass(frozen=True)
class AttributedTouchpoint:
    touchpoint: Touchpoint
    credit: float


def normalize_model(model: str | None) -> AttributionModel:
    model_key = (model or "last_touch").strip().lower()
    if model_key not in SUPPORTED_ATTRIBUTION_MODELS:
        raise ValueError(
            f"Unsupported attribution model '{model}'. "
            f"Supported models: {', '.join(SUPPORTED_ATTRIBUTION_MODELS)}"
        )
    return model_key  # type: ignore[return-value]


def allocate_credit(
    touchpoints: list[Touchpoint],
    model: str | None,
    *,
    half_life_days: float = 7.0,
) -> list[AttributedTouchpoint]:
    """
    Allocate 100% conversion credit across a journey's touchpoints.

    The returned credits sum to 1.0, except when no touchpoints are supplied.
    Touchpoints are sorted by occurred_at before allocation.
    """
    selected_model = normalize_model(model)
    ordered = sorted(touchpoints, key=lambda item: item.occurred_at)
    count = len(ordered)
    if count == 0:
        return []
    if count == 1:
        return [AttributedTouchpoint(ordered[0], 1.0)]

    if selected_model == "first_touch":
        weights = [1.0] + [0.0] * (count - 1)
    elif selected_model == "last_touch":
        weights = [0.0] * (count - 1) + [1.0]
    elif selected_model == "linear":
        weights = [1.0 / count] * count
    elif selected_model == "time_decay":
        max_ts = ordered[-1].occurred_at
        raw_weights = [
            exp(-((max_ts - tp.occurred_at).total_seconds() / 86400.0) / half_life_days)
            for tp in ordered
        ]
        total = sum(raw_weights)
        weights = [w / total for w in raw_weights]
    elif selected_model == "u_shape":
        if count == 2:
            weights = [0.5, 0.5]
        else:
            middle_credit = 0.20 / (count - 2)
            weights = [0.40] + [middle_credit] * (count - 2) + [0.40]
    elif selected_model == "w_shape":
        lead_idx = _lead_creation_index(ordered)
        key_indices = sorted({0, lead_idx, count - 1})
        if len(key_indices) == count:
            weights = [1.0 / count] * count
        else:
            weights = [0.0] * count
            for idx in key_indices:
                weights[idx] = 0.30
            remaining_indices = [idx for idx in range(count) if idx not in key_indices]
            remaining_credit = 1.0 - sum(weights)
            for idx in remaining_indices:
                weights[idx] = remaining_credit / len(remaining_indices)
    else:  # pragma: no cover - normalize_model prevents this
        raise ValueError(f"Unsupported attribution model: {selected_model}")

    return [
        AttributedTouchpoint(touchpoint=touchpoint, credit=round(weight, 10))
        for touchpoint, weight in zip(ordered, weights)
    ]


def _lead_creation_index(touchpoints: list[Touchpoint]) -> int:
    for idx, touchpoint in enumerate(touchpoints):
        if touchpoint.role == "lead_creation":
            return idx
    return len(touchpoints) // 2

