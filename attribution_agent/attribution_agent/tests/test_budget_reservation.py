from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from utils.budget_reservation import (
    BudgetReservationExceeded,
    cancel_state,
    reserve_state,
    settle_state,
)


def _state() -> dict:
    return {
        "version": 1,
        "spent_usd": 0.0,
        "daily_spent_usd": {},
        "reservations": {},
    }


def _reserve(state: dict, reservation_id: str, amount: float, now: datetime) -> None:
    reserve_state(
        state,
        reservation_id=reservation_id,
        estimated_usd=amount,
        daily_limit=1.0,
        monthly_limit=5.0,
        day=now.date().isoformat(),
        run_id="run-1",
        expires_at=now + timedelta(hours=1),
        now=now,
    )


def test_parallel_reservations_include_inflight_spend():
    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    state = _state()
    _reserve(state, "r1", 0.6, now)

    with pytest.raises(BudgetReservationExceeded, match="Daily"):
        _reserve(state, "r2", 0.5, now)


def test_settlement_reconciles_estimate_to_actual():
    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    state = _state()
    _reserve(state, "r1", 0.6, now)

    settle_state(state, reservation_id="r1", actual_usd=0.4, now=now)

    assert state["reservations"] == {}
    assert state["spent_usd"] == pytest.approx(0.4)
    assert state["daily_spent_usd"]["2026-08-09"] == pytest.approx(0.4)


def test_cancel_releases_reserved_capacity():
    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    state = _state()
    _reserve(state, "r1", 0.8, now)

    cancel_state(state, "r1", now)
    _reserve(state, "r2", 0.8, now)

    assert set(state["reservations"]) == {"r2"}
