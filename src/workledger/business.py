import calendar
import csv
import html
import io
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Response
from pydantic import AwareDatetime, Field, field_validator
from sqlalchemy import select

from .domain import digest
from .models import (
    CalendarDay,
    Camera,
    Employee,
    EventReview,
    PayAdjustment,
    PayrollRun,
    SourceEvent,
    now,
)
from .payroll import calendar_map
from .schemas import EmployeeInput, Strict
from .security import require_role


class EmployeeEdit(EmployeeInput):
    active: bool = True
    left_on: str | None = None
    monthly_salary: Decimal | None = Field(default=None, ge=0, le=10000000, decimal_places=2)
    overtime_rate: Decimal | None = Field(default=None, ge=0, le=100000, decimal_places=2)

    @field_validator("joined_on", "left_on")
    @classmethod
    def real_date(cls, value):
        if value:
            date.fromisoformat(value)
        return value or None


class CalendarInput(Strict):
    site_id: UUID
    day: date
    name: str = Field(min_length=2, max_length=120)
    kind: Literal["paid_holiday", "unpaid_closure", "working_day"]
    note: str = Field(default="", max_length=1000)


class PayrollPolicy(Strict):
    overtime_multiplier: Decimal = Field(default=1, ge=0, le=10, decimal_places=2)
    holiday_multiplier: Decimal = Field(default=1, ge=0, le=10, decimal_places=2)
    holiday_hourly_rate: Decimal = Field(default=0, ge=0, le=100000, decimal_places=2)


class AdjustmentInput(Strict):
    employee_id: UUID
    month: str = Field(pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$")
    kind: Literal["allowance", "deduction"]
    amount: Decimal = Field(gt=0, le=10000000, decimal_places=2)
    reason: str = Field(min_length=5, max_length=1000)

    @field_validator("month")
    @classmethod
    def valid_month(cls, value):
        date.fromisoformat(value + "-01")
        return value


class Reason(Strict):
    reason: str = Field(min_length=5, max_length=1000)


class ManualEvent(Reason):
    employee_id: UUID
    kind: Literal["entry", "exit"]
    occurred_at: AwareDatetime


def register(
    app,
    factory,
    current_user,
    site_for,
    employee_for,
    audit,
    employee_json,
    event_query,
    payroll_preview,
    day_rows,
):
    def database():
        with factory() as db:
            yield db

    def run_for(db, user, identifier):
        run = db.scalar(
            select(PayrollRun).where(
                PayrollRun.id == identifier, PayrollRun.company_id == user.company_id
            )
        )
        if not run:
            raise HTTPException(404, "Payroll run not found")
        return run

    @app.put("/api/v1/employees/{identifier}")
    def edit_employee(
        identifier: str, body: EmployeeEdit, user=Depends(current_user), db=Depends(database)
    ):
        require_role(user, "admin", "hr")
        employee = employee_for(db, user, identifier)
        if str(body.site_id) != employee.site_id:
            raise HTTPException(422, "Site transfers require a separate employment assignment")
        if body.joined_on and body.left_on and body.left_on < body.joined_on:
            raise HTTPException(422, "Last employment date cannot precede joining date")
        if not body.active and not body.left_on:
            raise HTTPException(
                422,
                "Set the last employment date when deactivating an employee; historical attendance and payroll remain available",
            )
        if user.role == "hr" and (
            body.monthly_salary is not None
            and body.monthly_salary != employee.monthly_salary
            or body.overtime_rate is not None
            and body.overtime_rate != employee.overtime_rate
        ):
            raise HTTPException(403, "Salary changes require an administrator or payroll role")
        employee.salary_version += int(
            body.monthly_salary is not None
            and employee.monthly_salary != body.monthly_salary
            or body.overtime_rate is not None
            and employee.overtime_rate != body.overtime_rate
        )
        for key, value in body.model_dump(mode="json", exclude={"site_id"}).items():
            if key in {"monthly_salary", "overtime_rate"} and value is None:
                continue
            setattr(employee, key, value)
        employee.sample = False
        audit(db, user, "employee.updated", employee.id, {"active": body.active})
        db.commit()
        if app.state.vision:
            app.state.vision.templates_cache.clear()
            if not employee.active:
                app.state.vision.gallery.remove(employee.site_id, employee.id)
            with app.state.vision.lock:
                for tracker in app.state.vision.trackers.values():
                    tracker.tracks.clear()
        return employee_json(employee, user)

    @app.get("/api/v1/calendar")
    def get_calendar(site_id: str, month: str, user=Depends(current_user), db=Depends(database)):
        site_for(db, user, site_id)
        try:
            date.fromisoformat(month + "-01")
        except ValueError:
            raise HTTPException(422, "Use YYYY-MM") from None
        return [
            {
                "id": row.id,
                "day": row.day,
                "name": row.name,
                "kind": row.kind,
                "note": row.note,
                "version": row.version,
            }
            for row in calendar_map(db, user.company_id, site_id, month).values()
        ]

    @app.post("/api/v1/calendar")
    def save_calendar(body: CalendarInput, user=Depends(current_user), db=Depends(database)):
        require_role(user, "admin", "hr", "payroll")
        site_for(db, user, body.site_id)
        row = db.scalar(
            select(CalendarDay).where(
                CalendarDay.company_id == user.company_id,
                CalendarDay.site_id == str(body.site_id),
                CalendarDay.day == body.day.isoformat(),
            )
        )
        if row:
            row.version += 1
        else:
            row = CalendarDay(
                company_id=user.company_id, site_id=str(body.site_id), day=body.day.isoformat()
            )
            db.add(row)
        row.name, row.kind, row.note = body.name, body.kind, body.note
        audit(
            db,
            user,
            "calendar.updated",
            str(body.site_id),
            {"day": str(body.day), "kind": body.kind, "name": body.name},
        )
        db.commit()
        return {"ok": True}

    @app.delete("/api/v1/calendar/{identifier}")
    def delete_calendar(identifier: str, user=Depends(current_user), db=Depends(database)):
        require_role(user, "admin", "hr", "payroll")
        row = db.scalar(
            select(CalendarDay).where(
                CalendarDay.id == identifier, CalendarDay.company_id == user.company_id
            )
        )
        if not row:
            raise HTTPException(404, "Calendar entry not found")
        audit(db, user, "calendar.removed", identifier, {"day": row.day})
        db.delete(row)
        db.commit()
        return {"ok": True}

    @app.put("/api/v1/sites/{identifier}/payroll-policy")
    def policy(
        identifier: str, body: PayrollPolicy, user=Depends(current_user), db=Depends(database)
    ):
        require_role(user, "admin", "hr", "payroll")
        site = site_for(db, user, identifier)
        site.payroll_policy = body.model_dump(mode="json")
        audit(db, user, "payroll.policy_updated", identifier, site.payroll_policy)
        db.commit()
        return {"ok": True, "policy": site.payroll_policy}

    @app.get("/api/v1/payroll/adjustments")
    def adjustments(site_id: str, month: str, user=Depends(current_user), db=Depends(database)):
        require_role(user, "admin", "payroll")
        site_for(db, user, site_id)
        rows = db.execute(
            select(PayAdjustment, Employee.name)
            .join(Employee, Employee.id == PayAdjustment.employee_id)
            .where(
                PayAdjustment.company_id == user.company_id,
                Employee.site_id == site_id,
                PayAdjustment.month == month,
            )
        ).all()
        return [
            {
                "id": r.id,
                "employee_id": r.employee_id,
                "name": name,
                "kind": r.kind,
                "amount": str(r.amount),
                "reason": r.reason,
            }
            for r, name in rows
        ]

    @app.post("/api/v1/payroll/adjustments", status_code=201)
    def adjustment(body: AdjustmentInput, user=Depends(current_user), db=Depends(database)):
        require_role(user, "admin", "payroll")
        employee = employee_for(db, user, body.employee_id)
        date.fromisoformat(body.month + "-01")
        row = PayAdjustment(
            company_id=user.company_id,
            employee_id=employee.id,
            month=body.month,
            kind=body.kind,
            amount=body.amount,
            reason=body.reason,
            created_by=user.id,
        )
        db.add(row)
        db.flush()
        audit(
            db,
            user,
            "payroll.adjustment_added",
            row.id,
            {"kind": body.kind, "amount": str(body.amount), "reason": body.reason},
        )
        db.commit()
        return {"id": row.id}

    @app.delete("/api/v1/payroll/adjustments/{identifier}")
    def remove_adjustment(identifier: str, user=Depends(current_user), db=Depends(database)):
        require_role(user, "admin", "payroll")
        row = db.scalar(
            select(PayAdjustment).where(
                PayAdjustment.id == identifier, PayAdjustment.company_id == user.company_id
            )
        )
        if not row:
            raise HTTPException(404, "Adjustment not found")
        audit(db, user, "payroll.adjustment_removed", identifier, {"reason": row.reason})
        db.delete(row)
        db.commit()
        return {"ok": True}

    @app.get("/api/v1/payroll/runs/{identifier}")
    def payroll_run(identifier: str, user=Depends(current_user), db=Depends(database)):
        require_role(user, "admin", "payroll")
        run = run_for(db, user, identifier)
        site = site_for(db, user, run.site_id)
        current = payroll_preview(db, user, site, run.month)
        return {
            "id": run.id,
            "status": run.status,
            "snapshot": run.snapshot,
            "stale": run.input_revision != current["input_revision"],
            "finalized_at": run.finalized_at,
        }

    @app.post("/api/v1/payroll/runs/{identifier}/finalize")
    def finalize(identifier: str, body: Reason, user=Depends(current_user), db=Depends(database)):
        require_role(user, "admin", "payroll")
        run = run_for(db, user, identifier)
        if run.status != "draft":
            raise HTTPException(
                409, "Only a draft can be finalized; finalized snapshots are immutable"
            )
        site = site_for(db, user, run.site_id)
        if db.scalar(
            select(PayrollRun.id).where(
                PayrollRun.site_id == site.id,
                PayrollRun.month == run.month,
                PayrollRun.status == "finalized",
            )
        ):
            raise HTTPException(409, "This site/month already has finalized payroll")
        current = payroll_preview(db, user, site, run.month)
        if not current["can_finalize"]:
            raise HTTPException(
                409, "Resolve all attendance, calendar and deduction blockers first"
            )
        if run.input_revision != current["input_revision"]:
            raise HTTPException(409, "Inputs changed. Save and review a new draft snapshot")
        year, month = map(int, run.month.split("-"))
        end = date(year, month, calendar.monthrange(year, month)[1])
        if end >= now().astimezone(ZoneInfo(site.timezone)).date():
            raise HTTPException(409, "The payroll month must be completed before finalization")
        run.status, run.finalized_by, run.finalized_at = "finalized", user.id, now()
        audit(
            db,
            user,
            "payroll.finalized",
            run.id,
            {"reason": body.reason, "input_revision": run.input_revision},
        )
        db.commit()
        return {"ok": True}

    @app.get("/api/v1/payroll/runs/{identifier}/export.csv")
    def export_payroll(identifier: str, user=Depends(current_user), db=Depends(database)):
        require_role(user, "admin", "payroll")
        run = run_for(db, user, identifier)
        stream = io.StringIO()
        writer = csv.writer(stream)
        keys = [
            "code",
            "name",
            "base_salary",
            "paid_days",
            "paid_holidays",
            "earned_salary",
            "overtime",
            "holiday_pay",
            "allowances",
            "deductions",
            "gross",
            "net",
        ]
        writer.writerow(keys)
        for item in run.snapshot["items"]:
            writer.writerow(
                [
                    ("'" + str(item.get(key, "")))
                    if str(item.get(key, "")).startswith(("=", "+", "-", "@", "\t", "\r"))
                    else item.get(key, "")
                    for key in keys
                ]
            )
        audit(db, user, "payroll.exported", identifier)
        db.commit()
        return Response(
            stream.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="payroll-{run.month}.csv"'},
        )

    @app.get("/api/v1/payroll/runs/{identifier}/payslip/{employee_id}")
    def payslip(
        identifier: str, employee_id: str, user=Depends(current_user), db=Depends(database)
    ):
        require_role(user, "admin", "payroll")
        run = run_for(db, user, identifier)
        item = next(
            (row for row in run.snapshot["items"] if row["employee_id"] == employee_id), None
        )
        if not item:
            raise HTTPException(404, "Employee is not in this payroll snapshot")

        def esc(value):
            return html.escape(str(value))

        rows = "".join(
            f"<tr><th>{esc(label)}</th><td>BDT {esc(item.get(key, '0.00'))}</td></tr>"
            for label, key in [
                ("Monthly base", "base_salary"),
                ("Earned base", "earned_salary"),
                ("Overtime", "overtime"),
                ("Holiday work", "holiday_pay"),
                ("Allowances", "allowances"),
                ("Deductions", "deductions"),
                ("Net pay", "net"),
            ]
        )
        audit(db, user, "payslip.viewed", identifier, {"employee_id": employee_id})
        db.commit()
        document = f'<!doctype html><html><head><meta charset="utf-8"><title>Payslip {esc(run.month)}</title><style>body{{font:16px system-ui;margin:50px auto;max-width:750px;color:#243b2b}}h1{{font-size:30px}}table{{width:100%;border-collapse:collapse;margin:30px 0}}th,td{{padding:15px;text-align:left;border-bottom:1px solid #ddd}}td{{text-align:right}}small{{color:#68776a}}@media print{{body{{margin:20px}}}}</style></head><body><small>WORKLEDGER · {esc(run.status.upper())} SNAPSHOT</small><h1>Payslip · {esc(run.month)}</h1><p>{esc(item["name"])} · {esc(item["code"])}</p><p>{esc(item.get("designation", ""))}</p><table>{rows}</table><p>Paid units: {esc(item["paid_days"])} / {esc(item["scheduled_days"])} · Unresolved days: {esc(item.get("unresolved_days", 0))}</p><small>Currency BDT. Print or save as PDF using your browser. {"Draft — not approved for payment." if run.status != "finalized" else "Approved immutable payroll snapshot."}</small></body></html>'
        return Response(
            document,
            media_type="text/html",
            headers={
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'"
            },
        )

    @app.post("/api/v1/events/manual", status_code=201)
    def manual_event(body: ManualEvent, user=Depends(current_user), db=Depends(database)):
        require_role(user, "admin", "hr")
        employee = employee_for(db, user, body.employee_id)
        if body.occurred_at > now():
            raise HTTPException(422, "Future movements cannot be entered")
        camera = db.scalar(
            select(Camera).where(
                Camera.company_id == user.company_id,
                Camera.site_id == employee.site_id,
                Camera.system.is_(True),
                Camera.role == body.kind,
            )
        )
        if not camera:
            camera = Camera(
                company_id=user.company_id,
                site_id=employee.site_id,
                name="HR correction: " + body.kind,
                role=body.kind,
                target_fps=1,
                system=True,
            )
            db.add(camera)
            db.flush()
        identifier = str(uuid4())
        source = SourceEvent(
            id=identifier,
            company_id=user.company_id,
            site_id=employee.site_id,
            agent_id=None,
            camera_id=camera.id,
            employee_id=employee.id,
            kind=body.kind,
            identity_status="confirmed",
            occurred_at=body.occurred_at,
            boot_id="manual",
            sequence=0,
            fingerprint=digest({"id": identifier, "by": user.id, "reason": body.reason}),
            simulated=False,
        )
        db.add(source)
        audit(
            db,
            user,
            "movement.corrected",
            identifier,
            {
                "employee_id": employee.id,
                "reason": body.reason,
                "kind": body.kind,
                "occurred_at": body.occurred_at.isoformat(),
            },
        )
        db.commit()
        return {"id": identifier}

    @app.post("/api/v1/events/{identifier}/void")
    def void_event(identifier: str, body: Reason, user=Depends(current_user), db=Depends(database)):
        require_role(user, "admin", "hr")
        source = db.scalar(
            select(SourceEvent).where(
                SourceEvent.id == identifier, SourceEvent.company_id == user.company_id
            )
        )
        if not source:
            raise HTTPException(404, "Movement not found")
        review = db.get(EventReview, identifier)
        if not review:
            review = EventReview(event_id=identifier, company_id=user.company_id)
            db.add(review)
        (
            review.employee_id,
            review.outcome,
            review.reason,
            review.approved_by,
            review.approved_at,
        ) = None, "voided", body.reason, user.id, now()
        audit(db, user, "movement.voided", identifier, {"reason": body.reason})
        db.commit()
        return {"ok": True}
