"""Business-calendar helpers.

Timestamps remain stored in UTC.  User-facing "today", daily entitlements and
calendar charts follow China Standard Time so midnight means the current local
calendar day rather than UTC midnight.
"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


BUSINESS_TIMEZONE_NAME = "Asia/Shanghai"
BUSINESS_TIMEZONE = ZoneInfo(BUSINESS_TIMEZONE_NAME)


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def business_day_start_utc(value: datetime | None = None) -> datetime:
    local = _utc(value).astimezone(BUSINESS_TIMEZONE)
    return datetime.combine(local.date(), time.min, BUSINESS_TIMEZONE).astimezone(timezone.utc)


def business_week_start_utc(value: datetime | None = None) -> datetime:
    local = _utc(value).astimezone(BUSINESS_TIMEZONE)
    monday = local.date() - timedelta(days=local.weekday())
    return datetime.combine(monday, time.min, BUSINESS_TIMEZONE).astimezone(timezone.utc)


def business_month_start_utc(value: datetime | None = None) -> datetime:
    local = _utc(value).astimezone(BUSINESS_TIMEZONE)
    first = local.date().replace(day=1)
    return datetime.combine(first, time.min, BUSINESS_TIMEZONE).astimezone(timezone.utc)


def business_date_keys(days: int, value: datetime | None = None) -> list[str]:
    count = max(1, int(days))
    end = _utc(value).astimezone(BUSINESS_TIMEZONE).date()
    start = end - timedelta(days=count - 1)
    return [(start + timedelta(days=index)).isoformat() for index in range(count)]
