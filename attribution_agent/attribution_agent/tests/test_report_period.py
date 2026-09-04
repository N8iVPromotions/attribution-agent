from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from utils import report_period as report_period_module
from utils.report_period import extraction_lookback_days, resolve_report_period


def test_default_period_on_september_first_is_completed_august():
    period = resolve_report_period(
        None,
        now=datetime(2026, 9, 1, 4, 0, tzinfo=timezone.utc),
        timezone_name="America/New_York",
    )

    assert period.month == "2026-08"
    assert period.start.isoformat() == "2026-08-01T04:00:00+00:00"
    assert period.end.isoformat() == "2026-09-01T04:00:00+00:00"


def test_timezone_boundary_uses_new_york_calendar_date():
    period = resolve_report_period(
        None,
        now=datetime(2026, 9, 1, 3, 59, tzinfo=timezone.utc),
        timezone_name="America/New_York",
    )

    assert period.month == "2026-07"


def test_default_period_rolls_across_calendar_year():
    period = resolve_report_period(
        None,
        now=datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc),
        timezone_name="America/New_York",
    )

    assert period.month == "2025-12"
    assert period.start.isoformat() == "2025-12-01T05:00:00+00:00"
    assert period.end.isoformat() == "2026-01-01T05:00:00+00:00"


def test_explicit_leap_month_has_exclusive_calendar_bound():
    period = resolve_report_period(
        "2024-02",
        now=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    assert period.start.isoformat() == "2024-02-01T05:00:00+00:00"
    assert period.end.isoformat() == "2024-03-01T05:00:00+00:00"


def test_backfill_extraction_window_reaches_pre_period_evidence():
    period = resolve_report_period(
        "2024-02",
        now=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    days = extraction_lookback_days(period, 30, today=date(2026, 9, 1))

    assert date(2026, 9, 1) - timedelta(days=days - 1) == date(2024, 1, 2)


def test_extraction_window_includes_period_lookback_and_end_boundary():
    period = resolve_report_period(
        "2026-08",
        now=datetime(2026, 9, 1, 4, 0, tzinfo=timezone.utc),
    )

    assert extraction_lookback_days(period, 30, today=date(2026, 9, 1)) == 62


def test_default_extraction_date_uses_business_timezone_at_month_boundary(
    monkeypatch,
):
    period = resolve_report_period(
        "2026-07",
        now=datetime(2026, 9, 1, 3, 30, tzinfo=timezone.utc),
    )

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = cls(2026, 9, 1, 3, 30, tzinfo=timezone.utc)
            return instant if tz is None else instant.astimezone(tz)

    monkeypatch.setattr(report_period_module, "datetime", FrozenDateTime)

    assert extraction_lookback_days(period, 30) == 92


@pytest.mark.parametrize("value", ["2026-9", "09-2026", "2026-13", ""])
def test_invalid_report_month_is_rejected(value):
    with pytest.raises(ValueError, match="YYYY-MM"):
        resolve_report_period(
            value,
            now=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )


def test_current_or_future_month_is_rejected():
    with pytest.raises(ValueError, match="completed calendar month"):
        resolve_report_period(
            "2026-09",
            now=datetime(2026, 9, 15, tzinfo=timezone.utc),
        )
