"""Completed calendar-month reporting periods."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_REPORT_TIMEZONE = "America/New_York"
_REPORT_MONTH_PATTERN = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


@dataclass(frozen=True)
class ReportPeriod:
    month: str
    start: datetime
    end: datetime
    timezone_name: str = DEFAULT_REPORT_TIMEZONE


def _report_timezone(timezone_name: str | None) -> ZoneInfo:
    name = (
        timezone_name
        or os.environ.get("ARIE_REPORT_TIMEZONE", "").strip()
        or DEFAULT_REPORT_TIMEZONE
    )
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown report timezone: {name}") from exc


def _local_now(now: datetime | None, report_timezone: ZoneInfo) -> datetime:
    if now is None:
        return datetime.now(timezone.utc).astimezone(report_timezone)
    if now.tzinfo is None:
        return now.replace(tzinfo=report_timezone)
    return now.astimezone(report_timezone)


def _month_bounds(month: str) -> tuple[date, date]:
    if not isinstance(month, str) or not _REPORT_MONTH_PATTERN.fullmatch(month):
        raise ValueError("report_month must use YYYY-MM")
    start = datetime.strptime(month, "%Y-%m").date()
    end = (
        date(start.year + 1, 1, 1)
        if start.month == 12
        else date(start.year, start.month + 1, 1)
    )
    return start, end


def resolve_report_period(
    report_month: str | None,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> ReportPeriod:
    """Resolve a completed report month in the configured business timezone."""
    report_timezone = _report_timezone(timezone_name)
    current_month_start = _local_now(now, report_timezone).date().replace(day=1)
    if report_month is None:
        report_end = current_month_start
        report_start = (report_end - timedelta(days=1)).replace(day=1)
        report_month = report_start.strftime("%Y-%m")
    else:
        report_start, report_end = _month_bounds(report_month)
        if report_end > current_month_start:
            raise ValueError("report_month must be a completed calendar month")
    return ReportPeriod(
        month=report_month,
        start=datetime.combine(report_start, time.min, report_timezone).astimezone(
            timezone.utc
        ),
        end=datetime.combine(report_end, time.min, report_timezone).astimezone(
            timezone.utc
        ),
        timezone_name=report_timezone.key,
    )


def extraction_lookback_days(
    period: ReportPeriod,
    configured_lookback: int,
    today: date | datetime | None = None,
) -> int:
    """Return a trailing window that reaches the period's earliest evidence date."""
    if configured_lookback < 0:
        raise ValueError("configured_lookback cannot be negative")
    report_timezone = ZoneInfo(period.timezone_name)
    if today is None:
        extraction_end = datetime.now(timezone.utc).astimezone(report_timezone).date()
    elif isinstance(today, datetime):
        extraction_end = today.date()
    else:
        extraction_end = today
    period_end_date = period.end.astimezone(report_timezone).date()
    if extraction_end < period_end_date:
        raise ValueError("today cannot precede the completed report period")
    period_start_date = period.start.astimezone(report_timezone).date()
    extraction_start = period_start_date - timedelta(days=configured_lookback)
    return (extraction_end - extraction_start).days + 1
