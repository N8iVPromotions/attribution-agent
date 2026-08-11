"""Atomic GCS-backed reservations for parallel model spend enforcement."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


class BudgetReservationExceeded(RuntimeError):
    pass


class BudgetReservationLost(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _active_reservations(state: dict, now: datetime) -> dict:
    active = {}
    for reservation_id, reservation in state.get("reservations", {}).items():
        try:
            expires_at = datetime.fromisoformat(reservation["expires_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if expires_at > now:
            active[reservation_id] = reservation
    state["reservations"] = active
    return active


def reserve_state(
    state: dict,
    *,
    reservation_id: str,
    estimated_usd: float,
    daily_limit: float,
    monthly_limit: float,
    day: str,
    run_id: str,
    expires_at: datetime,
    now: datetime,
) -> None:
    active = _active_reservations(state, now)
    reserved_month = sum(float(item["estimated_usd"]) for item in active.values())
    reserved_day = sum(
        float(item["estimated_usd"])
        for item in active.values()
        if item.get("day") == day
    )
    spent_month = float(state.get("spent_usd", 0))
    spent_day = float(state.get("daily_spent_usd", {}).get(day, 0))
    if spent_day + reserved_day + estimated_usd > daily_limit:
        raise BudgetReservationExceeded("Daily AI spend limit exceeded")
    if spent_month + reserved_month + estimated_usd > monthly_limit:
        raise BudgetReservationExceeded("Monthly AI spend limit exceeded")
    active[reservation_id] = {
        "estimated_usd": estimated_usd,
        "day": day,
        "run_id": run_id,
        "expires_at": expires_at.isoformat(),
    }


def settle_state(
    state: dict,
    *,
    reservation_id: str,
    actual_usd: float,
    now: datetime,
) -> None:
    active = _active_reservations(state, now)
    reservation = active.pop(reservation_id, None)
    if reservation is None:
        raise BudgetReservationLost(f"Reservation {reservation_id} is unavailable")
    day = reservation["day"]
    state["spent_usd"] = round(float(state.get("spent_usd", 0)) + actual_usd, 8)
    daily = state.setdefault("daily_spent_usd", {})
    daily[day] = round(float(daily.get(day, 0)) + actual_usd, 8)


def cancel_state(state: dict, reservation_id: str, now: datetime) -> None:
    _active_reservations(state, now).pop(reservation_id, None)


class GCSBudgetStore:
    def __init__(self, bucket_name: str, agency_id: str, month: str) -> None:
        from google.cloud import storage

        safe_agency = "".join(
            character if character.isalnum() or character in "-_" else "_"
            for character in agency_id
        )
        self.blob = (
            storage.Client()
            .bucket(bucket_name)
            .blob(f"ai-budget/agency={safe_agency}/month={month}.json")
        )

    def update(self, mutate) -> None:
        from google.api_core.exceptions import NotFound, PreconditionFailed

        max_attempts = int(os.environ.get("ARIE_AI_BUDGET_CAS_RETRIES", "8"))
        for attempt in range(max_attempts):
            try:
                self.blob.reload()
                generation = int(self.blob.generation)
                state = json.loads(
                    self.blob.download_as_text(if_generation_match=generation)
                )
            except NotFound:
                generation = 0
                state = {
                    "version": 1,
                    "spent_usd": 0.0,
                    "daily_spent_usd": {},
                    "reservations": {},
                }
            except PreconditionFailed:
                continue

            mutate(state)
            try:
                self.blob.upload_from_string(
                    json.dumps(state, sort_keys=True, separators=(",", ":")),
                    content_type="application/json",
                    if_generation_match=generation,
                )
                return
            except PreconditionFailed:
                if attempt + 1 == max_attempts:
                    break
                time.sleep(min(0.05 * (2**attempt), 1.0))
        raise RuntimeError("AI budget reservation CAS retries exhausted")


@dataclass
class BudgetReservation:
    store: GCSBudgetStore
    reservation_id: str

    def settle(self, actual_usd: float) -> None:
        now = _utcnow()
        self.store.update(
            lambda state: settle_state(
                state,
                reservation_id=self.reservation_id,
                actual_usd=actual_usd,
                now=now,
            )
        )

    def cancel(self) -> None:
        now = _utcnow()
        self.store.update(lambda state: cancel_state(state, self.reservation_id, now))


def reserve_budget(
    *,
    agency_id: str,
    run_id: str,
    estimated_usd: float,
    daily_limit: float,
    monthly_limit: float,
) -> BudgetReservation | None:
    bucket_name = os.environ.get("ARIE_AI_BUDGET_BUCKET", "").strip()
    if not bucket_name:
        return None

    now = _utcnow()
    reservation_id = uuid.uuid4().hex
    ttl_minutes = int(os.environ.get("ARIE_AI_BUDGET_RESERVATION_TTL_MINUTES", "1440"))
    expires_at = now + timedelta(minutes=max(ttl_minutes, 60))
    store = GCSBudgetStore(bucket_name, agency_id, now.strftime("%Y-%m"))
    store.update(
        lambda state: reserve_state(
            state,
            reservation_id=reservation_id,
            estimated_usd=estimated_usd,
            daily_limit=daily_limit,
            monthly_limit=monthly_limit,
            day=now.date().isoformat(),
            run_id=run_id,
            expires_at=expires_at,
            now=now,
        )
    )
    return BudgetReservation(store=store, reservation_id=reservation_id)
