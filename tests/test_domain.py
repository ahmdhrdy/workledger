from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from workledger.domain import attendance, business_date, calculate_pay


def site(start="09:00"):
    return SimpleNamespace(
        timezone="Asia/Dhaka", shift_start=start, grace_minutes=10, policy_version=1
    )


def event(kind, hour, minute=0, status="confirmed", day=14):
    return SimpleNamespace(
        id=f"{day}-{hour}-{minute}",
        fingerprint=f"{kind}-{hour}-{minute}",
        occurred_at=datetime(2026, 10, day, hour, minute, tzinfo=UTC),
        kind=kind,
        identity_status=status,
    )


def test_intervals_do_not_count_lunch_or_after_final_exit():
    result = attendance(
        [event("entry", 3), event("exit", 6, 30), event("entry", 7, 10), event("exit", 12)],
        date(2026, 10, 14),
        site(),
    )
    assert result["complete"]
    assert result["inside_minutes"] == 500
    assert result["away_minutes"] == 40
    assert result["late_minutes"] == 0


def test_open_and_unseen_days_never_implicitly_become_absence():
    result = attendance([event("entry", 3, 25)], date(2026, 10, 14), site())
    assert not result["complete"] and result["currently_inside"]
    assert result["inside_minutes"] == 0
    assert result["late_minutes"] == 15
    unseen = attendance([], date(2026, 10, 14), site())
    assert not unseen["complete"]
    assert "review leave or absence" in unseen["issues"][0]


def test_ambiguous_identity_cannot_close_a_confirmed_interval():
    result = attendance(
        [event("entry", 3), event("exit", 12, status="ambiguous")], date(2026, 10, 14), site()
    )
    assert not result["complete"]
    assert result["last_out"] is None
    assert "Uncertain identification" in result["issues"]


def test_repeated_entry_is_an_exception_and_revision_changes_with_policy():
    events = [event("entry", 3), event("entry", 4), event("exit", 12)]
    policy = site()
    state = attendance(events, date(2026, 10, 14), policy)
    assert not state["complete"]
    policy.policy_version += 1
    assert state["revision"] != attendance(events, date(2026, 10, 14), policy)["revision"]


def test_overnight_shift_attributes_morning_exit_to_prior_date():
    # 22:00 local to 06:00 local on the next calendar day.
    events = [event("entry", 16), event("exit", 0, day=15)]
    result = attendance(events, date(2026, 10, 14), site("22:00"))
    assert result["complete"] and result["inside_minutes"] == 480
    assert business_date(events[1].occurred_at, "Asia/Dhaka", "22:00") == date(2026, 10, 14)


def test_money_is_decimal_and_rounded_once_per_component():
    result = calculate_pay("10000.00", 22, 21, 35, "123.45")
    assert result == {
        "base_salary": "10000.00",
        "earned_salary": "9545.45",
        "unpaid_adjustment": "454.55",
        "overtime": "72.01",
        "net": "9617.46",
    }
    with pytest.raises(ValueError):
        calculate_pay("100", 0, 0, 0, "0")
