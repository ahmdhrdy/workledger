"""Explicitly opted-in synthetic workspace. Never represents live camera inference."""

from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .domain import digest
from .models import Camera, Decision, Employee, Site, SourceEvent, User, uid


def seed_demo(db: Session, site: Site, user: User):
    names = [
        "Ayesha Rahman",
        "Tanvir Hasan",
        "Nusrat Jahan",
        "Arif Ahmed",
        "Farhana Islam",
        "Rafiul Karim",
        "Mehedi Rahman",
        "Sadika Noor",
        "Imran Hossain",
        "Sadia Akter",
        "Fahim Chowdhury",
        "Maliha Khan",
        "Rakib Alam",
        "Nabila Sultana",
        "Samiul Haque",
    ]
    cameras = [
        Camera(company_id=site.company_id, site_id=site.id, name=name, role=role, target_fps=fps)
        for name, role, fps in [("Main entrance", "entry", 8), ("Office exit", "exit", 8)]
        + [(f"Workspace {n:02}", "interior", 2) for n in range(1, 11)]
    ]
    db.add_all(cameras)
    db.flush()
    day = datetime.now(ZoneInfo(site.timezone)).date() - timedelta(days=1)
    while day.weekday() not in site.weekdays:
        day -= timedelta(days=1)
    tz = ZoneInfo(site.timezone)
    for i, name in enumerate(names):
        employee = Employee(
            company_id=site.company_id,
            site_id=site.id,
            code=f"EMP-{i + 1:03}",
            name=name,
            department=["Operations", "Engineering", "Administration"][i % 3],
            designation=["Associate", "Specialist", "Coordinator"][i % 3],
            monthly_salary=Decimal(25000 + i * 1500),
            overtime_rate=Decimal("120"),
        )
        db.add(employee)
        db.flush()
        if i in (12, 13):
            db.add(
                Decision(
                    company_id=site.company_id,
                    site_id=site.id,
                    employee_id=employee.id,
                    business_date=day.isoformat(),
                    outcome="leave",
                    paid_leave=True,
                    reason="Synthetic example: approved leave",
                    source_revision="demo-pending",
                    approved_by=user.id,
                )
            )
            continue
        if i == 14:
            continue
        moments = [("entry", time(9, i * 2), cameras[0])]
        if i % 3 == 0:
            moments += [("exit", time(12, 30), cameras[1]), ("entry", time(13, 10), cameras[0])]
        if i != 10:
            moments += [("exit", time(18, 5), cameras[1])]
        for seq, (kind, clock, camera) in enumerate(moments):
            event_id = uid()
            db.add(
                SourceEvent(
                    id=event_id,
                    company_id=site.company_id,
                    site_id=site.id,
                    camera_id=camera.id,
                    employee_id=employee.id,
                    kind=kind,
                    identity_status="confirmed",
                    occurred_at=datetime.combine(day, clock, tz),
                    boot_id=f"demo-{employee.id}",
                    sequence=seq,
                    fingerprint=digest(event_id),
                    simulated=True,
                )
            )
    return day
