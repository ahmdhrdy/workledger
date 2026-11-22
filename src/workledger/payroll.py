"""Configurable, explainable payroll arithmetic; no assumed statutory tax rates."""

from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select

from .domain import attendance, business_date, digest, money, month_workdays
from .models import CalendarDay, Decision, Employee, PayAdjustment


def calendar_map(db, company_id, site_id, month):
    return {
        row.day: row
        for row in db.scalars(
            select(CalendarDay).where(
                CalendarDay.company_id == company_id,
                CalendarDay.site_id == site_id,
                CalendarDay.day >= month + "-01",
                CalendarDay.day <= month + "-31",
            )
        ).all()
    }


def day_kind(day, site, calendar):
    entry = calendar.get(day.isoformat())
    if entry:
        return entry.kind
    return "working_day" if day.weekday() in site.weekdays else "weekly_off"


def employee_eligible(employee, day):
    return (not employee.joined_on or day.isoformat() >= employee.joined_on) and (
        not employee.left_on or day.isoformat() <= employee.left_on
    )


def preview(db, user, site, month, event_query):
    date.fromisoformat(month + "-01")
    calendar = calendar_map(db, user.company_id, site.id, month)
    nominal = set(month_workdays(month, site.weekdays))
    nominal.update(
        date.fromisoformat(key) for key, entry in calendar.items() if entry.kind == "working_day"
    )
    days = sorted(nominal)
    if not days:
        raise HTTPException(422, "Configure at least one scheduled weekday or working-day override")
    employees = db.scalars(
        select(Employee)
        .where(
            Employee.company_id == user.company_id,
            Employee.site_id == site.id,
            Employee.sample.is_(False),
        )
        .order_by(Employee.code)
    ).all()
    employees = [
        employee
        for employee in employees
        if any(employee_eligible(employee, day) for day in days)
        and (employee.active or employee.left_on)
    ]
    events = [e for e in event_query(db, user.company_id, site.id) if e.kind in {"entry", "exit"}]
    decisions = {
        (d.employee_id, d.business_date): d
        for d in db.scalars(
            select(Decision).where(
                Decision.company_id == user.company_id,
                Decision.site_id == site.id,
                Decision.business_date >= month + "-01",
                Decision.business_date <= month + "-31",
            )
        ).all()
    }
    adjustments = db.scalars(
        select(PayAdjustment).where(
            PayAdjustment.company_id == user.company_id, PayAdjustment.month == month
        )
    ).all()
    policy = site.payroll_policy or {}
    overtime_multiplier = Decimal(str(policy.get("overtime_multiplier", "1")))
    holiday_multiplier = Decimal(str(policy.get("holiday_multiplier", "1")))
    holiday_rate = Decimal(str(policy.get("holiday_hourly_rate", "0")))
    items, blockers, inputs = [], [], []
    unknown = [
        e
        for e in events
        if e.employee_id is None
        and business_date(e.occurred_at, site.timezone, site.shift_start).strftime("%Y-%m") == month
    ]
    if unknown:
        blockers.append(
            {
                "employee_id": None,
                "name": "Unidentified portal events",
                "days": len(unknown),
                "reason": "Local identity review required",
            }
        )
    inputs.append(("unknown", sorted((e.id, e.fingerprint) for e in unknown)))
    for employee in employees:
        paid = overtime = holiday_minutes = unresolved = paid_holidays = unpaid_closures = 0
        employee_events = [e for e in events if e.employee_id == employee.id]
        extra_holiday_days = {
            date.fromisoformat(d)
            for (identifier, d), decision in decisions.items()
            if identifier == employee.id
            and date.fromisoformat(d) not in nominal
            and decision.outcome == "present"
        }
        extra_holiday_days.update(
            business_date(e.occurred_at, site.timezone, site.shift_start)
            for e in employee_events
            if business_date(e.occurred_at, site.timezone, site.shift_start).strftime("%Y-%m")
            == month
        )
        for day in sorted(nominal | extra_holiday_days):
            if not employee_eligible(employee, day):
                continue
            kind = day_kind(day, site, calendar)
            state = attendance(employee_events, day, site)
            decision = decisions.get((employee.id, day.isoformat()))
            valid = decision and decision.source_revision == state["revision"]
            inputs.append(
                (
                    employee.id,
                    str(day),
                    kind,
                    state["revision"],
                    None
                    if not decision
                    else (
                        decision.outcome,
                        decision.paid_leave,
                        decision.overtime_minutes,
                        decision.holiday_minutes,
                        decision.reason,
                        decision.source_revision,
                    ),
                )
            )
            if kind == "paid_holiday":
                paid += int(day in nominal)
                paid_holidays += int(day in nominal)
            elif kind == "unpaid_closure":
                unpaid_closures += int(day in nominal)
            elif kind == "working_day":
                if not valid:
                    unresolved += 1
                else:
                    paid += int(
                        decision.outcome == "present"
                        or decision.outcome == "leave"
                        and decision.paid_leave
                    )
            if state["event_count"] and kind != "working_day" and not valid:
                unresolved += 1
            if valid and decision.outcome == "present":
                if kind == "working_day":
                    if decision.holiday_minutes:
                        unresolved += 1
                    overtime += decision.overtime_minutes
                else:
                    if decision.overtime_minutes:
                        unresolved += 1
                    holiday_minutes += decision.holiday_minutes
        if unresolved:
            blockers.append(
                {
                    "employee_id": employee.id,
                    "name": employee.name,
                    "days": unresolved,
                    "reason": "Unreviewed, changed or calendar-conflicting attendance",
                }
            )
        allowance_rows = [
            r for r in adjustments if r.employee_id == employee.id and r.kind == "allowance"
        ]
        deduction_rows = [
            r for r in adjustments if r.employee_id == employee.id and r.kind == "deduction"
        ]
        allowances = money(sum((r.amount for r in allowance_rows), Decimal(0)))
        deductions = money(sum((r.amount for r in deduction_rows), Decimal(0)))
        base = money(employee.monthly_salary)
        earned = money(base * paid / len(days))
        ot_rate = money(employee.overtime_rate * overtime_multiplier)
        h_rate = money((holiday_rate or employee.overtime_rate) * holiday_multiplier)
        if overtime and ot_rate <= 0 or holiday_minutes and h_rate <= 0:
            blockers.append(
                {
                    "employee_id": employee.id,
                    "name": employee.name,
                    "days": 0,
                    "reason": "Approved extra work requires a positive configured hourly pay rate",
                }
            )
        overtime_amount = money(ot_rate * overtime / 60)
        holiday_amount = money(h_rate * holiday_minutes / 60)
        gross = money(earned + overtime_amount + holiday_amount + allowances)
        net = money(gross - deductions)
        if net < 0:
            blockers.append(
                {
                    "employee_id": employee.id,
                    "name": employee.name,
                    "days": 0,
                    "reason": "Deductions exceed earned gross pay",
                }
            )
        inputs.append(
            (
                employee.id,
                str(employee.monthly_salary),
                str(employee.overtime_rate),
                employee.salary_version,
                employee.joined_on,
                employee.left_on,
                [
                    (r.id, r.kind, str(r.amount), r.reason)
                    for r in adjustments
                    if r.employee_id == employee.id
                ],
            )
        )
        items.append(
            {
                "employee_id": employee.id,
                "name": employee.name,
                "nickname": employee.nickname,
                "code": employee.code,
                "designation": employee.designation,
                "scheduled_days": len(days),
                "eligible_days": sum(employee_eligible(employee, d) for d in days),
                "paid_days": paid,
                "paid_holidays": paid_holidays,
                "unpaid_closures": unpaid_closures,
                "unresolved_days": unresolved,
                "overtime_minutes": overtime,
                "holiday_minutes": holiday_minutes,
                "base_salary": str(base),
                "earned_salary": str(earned),
                "unpaid_adjustment": str(money(base - earned)),
                "overtime": str(overtime_amount),
                "overtime_hourly_rate": str(ot_rate),
                "holiday_pay": str(holiday_amount),
                "holiday_hourly_rate": str(h_rate),
                "allowances": str(allowances),
                "deductions": str(deductions),
                "gross": str(gross),
                "net": str(net),
                "adjustments": [
                    {"kind": r.kind, "amount": str(r.amount), "reason": r.reason}
                    for r in allowance_rows + deduction_rows
                ],
            }
        )
    inputs.append(
        (
            "calendar",
            [(key, c.kind, c.version, c.name, c.note) for key, c in sorted(calendar.items())],
        )
    )
    return {
        "site_id": site.id,
        "month": month,
        "currency": "BDT",
        "items": items,
        "blockers": blockers,
        "can_finalize": bool(items) and not blockers,
        "total": str(money(sum((Decimal(item["net"]) for item in items), Decimal(0)))),
        "input_revision": digest(
            {"inputs": inputs, "policy": site.policy_version, "payroll_policy": policy}
        ),
        "calculation_policy": "Paid holidays require no attendance and receive paid credit; approved work/leave + approved overtime/holiday work + explicit allowances − deductions. No automatic productivity penalties or assumed tax rates.",
    }
