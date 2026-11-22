"""Pure attendance and money rules. No inference or automatic absence claims."""

import calendar
import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def business_date(moment: datetime, timezone: str, shift_start: str) -> date:
    local = utc(moment).astimezone(ZoneInfo(timezone))
    boundary = datetime.combine(local.date(), time.fromisoformat(shift_start), local.tzinfo)
    if local < boundary - timedelta(hours=4):
        return local.date() - timedelta(days=1)
    return local.date()


def attendance(events, day: date, site) -> dict:
    events = sorted(
        (
            e
            for e in events
            if e.kind in {"entry", "exit"}
            and business_date(e.occurred_at, site.timezone, site.shift_start) == day
        ),
        key=lambda e: (utc(e.occurred_at), e.id),
    )
    revision = digest(
        {"events": [(e.id, e.fingerprint) for e in events], "policy": site.policy_version}
    )
    confirmed = [e for e in events if e.identity_status == "confirmed"]
    issues = []
    if len(events) != len(confirmed):
        issues.append("Uncertain identification")
    intervals, away = [], []
    inside_since = None
    away_since = None
    first_in = last_out = None
    for event in confirmed:
        at = utc(event.occurred_at)
        if event.kind == "entry":
            if inside_since is not None:
                issues.append("Entry without a preceding exit")
                continue
            first_in = first_in or at
            inside_since = at
            if away_since is not None:
                away.append((away_since, at))
                away_since = None
        elif event.kind == "exit":
            if inside_since is None:
                issues.append("Exit without a matching entry")
                continue
            intervals.append((inside_since, at))
            inside_since = None
            last_out = at
            away_since = at
    if inside_since is not None:
        issues.append("Missing final exit / day still open")
    if not confirmed:
        issues.append("No confirmed attendance; review leave or absence")
    minutes = int(sum((end - start).total_seconds() for start, end in intervals) // 60)
    away_minutes = int(sum((end - start).total_seconds() for start, end in away) // 60)
    expected = datetime.combine(day, time.fromisoformat(site.shift_start), ZoneInfo(site.timezone))
    late = (
        max(0, int((first_in - expected).total_seconds() // 60) - site.grace_minutes)
        if first_in
        else 0
    )
    return {
        "business_date": day.isoformat(),
        "first_in": first_in,
        "last_out": last_out,
        "inside_minutes": minutes,
        "away_minutes": away_minutes,
        "late_minutes": late,
        "complete": bool(first_in and last_out and inside_since is None and not issues),
        "currently_inside": inside_since is not None,
        "issues": sorted(set(issues)),
        "revision": revision,
        "event_count": len(events),
    }


def month_workdays(month: str, weekdays: list[int], today: date | None = None):
    start = date.fromisoformat(month + "-01")
    end = calendar.monthrange(start.year, start.month)[1]
    days = [start.replace(day=d) for d in range(1, end + 1)]
    return [d for d in days if d.weekday() in weekdays]


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_pay(monthly_salary, scheduled_days: int, paid_days: int, overtime_minutes: int, rate):
    if scheduled_days <= 0 or not 0 <= paid_days <= scheduled_days:
        raise ValueError("Invalid scheduled or paid day count")
    salary = money(monthly_salary)
    earned = money(salary * Decimal(paid_days) / Decimal(scheduled_days))
    overtime = money(Decimal(overtime_minutes) * Decimal(str(rate)) / Decimal(60))
    return {
        "base_salary": str(salary),
        "earned_salary": str(earned),
        "unpaid_adjustment": str(money(salary - earned)),
        "overtime": str(overtime),
        "net": str(money(earned + overtime)),
    }
