import csv
import io
import secrets
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import date, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from .config import Settings, ensure_sqlite_parent
from .domain import attendance, business_date, digest, utc
from .models import (
    Agent,
    Audit,
    Base,
    Camera,
    Company,
    Decision,
    Employee,
    EventReview,
    PayrollRun,
    Site,
    SourceEvent,
    User,
    now,
)
from .models import Session as LoginSession
from .schemas import (
    AgentInput,
    CameraInput,
    DecisionInput,
    EmployeeInput,
    EventBatch,
    LoginInput,
    PayrollInput,
    PolicyInput,
    SetupInput,
)
from .security import (
    COOKIE,
    DUMMY,
    hasher,
    principal,
    require_role,
    sign_in,
    token_hash,
    verify_password,
)
from .seed import seed_demo


def create_app(settings: Settings | None = None):
    settings = settings or Settings()
    settings.validate_production()
    ensure_sqlite_parent(settings.database_url)
    opts = (
        {"connect_args": {"check_same_thread": False}}
        if settings.database_url.startswith("sqlite")
        else {}
    )
    if ":memory:" in settings.database_url:
        opts["poolclass"] = StaticPool
    engine = create_engine(settings.database_url, **opts)
    if settings.database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")

    factory = sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(_):
        if settings.mode == "test":
            Base.metadata.create_all(engine)
        elif settings.mode != "production":
            from .migration import upgrade_local

            upgrade_local(engine, settings)
        if settings.local_ai and settings.mode != "production":
            from .vision.runtime import VisionRuntime

            app.state.vision = VisionRuntime(settings, factory)
            if settings.mode != "test":
                from .samples import archive_generated_lists

                archive_generated_lists(factory, app.state.vision.store)
            if settings.mode != "test":
                app.state.vision.start()
        yield
        if app.state.vision and settings.mode != "test":
            app.state.vision.shutdown()
        engine.dispose()

    app = FastAPI(title="WorkLedger", version="0.3.0", lifespan=lifespan)
    app.state.engine, app.state.session_factory = engine, factory
    app.state.settings = settings
    app.state.vision = None
    setup_lock = threading.Lock()
    rate_lock = threading.Lock()
    login_attempts = defaultdict(deque)

    @app.middleware("http")
    async def browser_boundary(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not request.url.path.startswith(
            "/agent/"
        ):
            origin = request.headers.get("origin")
            if request.headers.get("x-workledger-request") != "1" or (
                origin and origin not in settings.allowed_origins
            ):
                return JSONResponse(
                    {"detail": "Request origin or CSRF header rejected"}, status_code=403
                )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.exception_handler(IntegrityError)
    async def integrity_error(_, __):
        return JSONResponse(
            {"detail": "This record conflicts with an existing record"}, status_code=409
        )

    def db_dependency():
        with factory() as db:
            yield db

    def current_user(request: Request, db=Depends(db_dependency)):
        return principal(request, db)

    def site_for(db, user, site_id):
        site = db.scalar(
            select(Site).where(Site.id == str(site_id), Site.company_id == user.company_id)
        )
        if not site:
            raise HTTPException(404, "Site not found")
        return site

    def employee_for(db, user, employee_id):
        employee = db.scalar(
            select(Employee).where(
                Employee.id == str(employee_id), Employee.company_id == user.company_id
            )
        )
        if not employee:
            raise HTTPException(404, "Employee not found")
        return employee

    def audit(db, user, action, target, detail=None):
        db.add(
            Audit(
                company_id=user.company_id,
                actor_id=user.id,
                action=action,
                target=str(target),
                detail=detail or {},
            )
        )

    def employee_json(employee, user):
        fields = [
            "id",
            "site_id",
            "code",
            "name",
            "nickname",
            "department",
            "designation",
            "active",
            "joined_on",
            "left_on",
        ]
        result = {key: getattr(employee, key) for key in fields}
        runtime = app.state.vision
        count = (
            sum(
                t["employee_id"] == employee.id
                for t in runtime.store.templates(employee.company_id)
            )
            if runtime
            else 0
        )
        result["enrollment_status"] = "enrolled" if count else "not_enrolled"
        result["face_sample_count"] = count
        if user.role in {"admin", "payroll"}:
            result.update(
                monthly_salary=str(employee.monthly_salary),
                overtime_rate=str(employee.overtime_rate),
                salary_version=employee.salary_version,
            )
        return result

    def event_query(db, company_id, site_id):
        events = db.scalars(
            select(SourceEvent).where(
                SourceEvent.company_id == company_id, SourceEvent.site_id == site_id
            )
        ).all()
        if settings.mode != "test":
            events = [event for event in events if not event.simulated]
        reviews = {
            r.event_id: r
            for r in db.scalars(
                select(EventReview).where(EventReview.company_id == company_id)
            ).all()
        }
        result = []
        for source in events:
            review = reviews.get(source.id)
            if review and review.outcome in {"dismissed", "visitor", "voided"}:
                continue
            if review:
                effective = SimpleNamespace(
                    **{
                        column.name: getattr(source, column.name)
                        for column in SourceEvent.__table__.columns
                    }
                )
                effective.employee_id, effective.identity_status = review.employee_id, "confirmed"
                effective.fingerprint = digest(
                    (source.fingerprint, review.employee_id, review.reason, review.approved_at)
                )
                result.append(effective)
            else:
                result.append(source)
        return result

    def day_rows(db, user, site, day):
        employees = db.scalars(
            select(Employee)
            .where(
                Employee.company_id == user.company_id,
                Employee.site_id == site.id,
                Employee.sample.is_(False),
            )
            .order_by(Employee.code)
        ).all()
        events = [
            e for e in event_query(db, user.company_id, site.id) if e.kind in {"entry", "exit"}
        ]
        decisions = {
            d.employee_id: d
            for d in db.scalars(
                select(Decision).where(
                    Decision.company_id == user.company_id,
                    Decision.site_id == site.id,
                    Decision.business_date == day.isoformat(),
                )
            ).all()
        }
        rows = []
        from .payroll import calendar_map, day_kind, employee_eligible

        calendar = calendar_map(db, user.company_id, site.id, day.strftime("%Y-%m"))
        schedule_kind = day_kind(day, site, calendar)
        for employee in employees:
            if not employee_eligible(employee, day) or not (employee.active or employee.left_on):
                continue
            row = attendance([e for e in events if e.employee_id == employee.id], day, site)
            decision = decisions.get(employee.id)
            stale = bool(decision and decision.source_revision != row["revision"])
            status = decision.outcome if decision and not stale else "needs_review"
            if schedule_kind != "working_day" and not row["event_count"] and not decision:
                status = "holiday" if schedule_kind == "paid_holiday" else "off_day"
            rows.append(
                {
                    **row,
                    "employee": employee_json(employee, user),
                    "status": status,
                    "schedule_kind": schedule_kind,
                    "approval_stale": stale,
                    "decision": None
                    if not decision
                    else {
                        "outcome": decision.outcome,
                        "reason": decision.reason,
                        "paid_leave": decision.paid_leave,
                        "overtime_minutes": decision.overtime_minutes,
                        "holiday_minutes": decision.holiday_minutes,
                        "approved_at": decision.approved_at,
                    },
                }
            )
        return rows

    def payroll_preview(db, user, site, month):
        from .payroll import preview

        return preview(db, user, site, month, event_query)

    @app.get("/api/v1/health")
    def health():
        return {
            "status": "ok",
            "version": "0.3.0",
            "mode": settings.mode,
            "capabilities": {
                "camera_inference": bool(
                    app.state.vision and app.state.vision.model_state == "ready"
                ),
                "biometric_enrollment": bool(app.state.vision),
                "remote_video": False,
                "attendance_events": True,
                "payroll_preview": True,
                "holiday_calendar": True,
                "payroll_finalization": True,
                "workstation_polygons": bool(app.state.vision),
                "daily_identity_profiles": bool(app.state.vision),
            },
        }

    @app.get("/api/v1/setup")
    def setup_status(db=Depends(db_dependency)):
        return {
            "required": not db.scalar(select(func.count()).select_from(User)),
            "available": settings.mode != "production",
        }

    @app.post("/api/v1/setup", status_code=201)
    def setup(body: SetupInput, request: Request, response: Response, db=Depends(db_dependency)):
        if settings.mode == "production" or (
            settings.mode != "test" and request.client.host not in {"127.0.0.1", "::1"}
        ):
            raise HTTPException(403, "Development setup is available only on the local machine")
        with setup_lock:
            if db.scalar(select(func.count()).select_from(User)):
                raise HTTPException(409, "Setup has already been completed")
            company = Company(name=body.company, demo=bool(body.demo and settings.mode == "test"))
            db.add(company)
            db.flush()
            site = Site(company_id=company.id, name=body.site)
            user = User(
                company_id=company.id,
                email=body.email.lower(),
                name=body.name,
                password_hash=hasher.hash(body.password),
                role="admin",
            )
            db.add_all([site, user])
            db.flush()
            if company.demo:
                seed_demo(db, site, user)
                db.flush()
                events = event_query(db, company.id, site.id)
                for decision in db.scalars(
                    select(Decision).where(Decision.company_id == company.id)
                ):
                    decision.source_revision = attendance(
                        [e for e in events if e.employee_id == decision.employee_id],
                        date.fromisoformat(decision.business_date),
                        site,
                    )["revision"]
            sign_in(db, response, user, settings)
            audit(db, user, "workspace.created", company.id, {"demo": body.demo})
            db.commit()
        return {"ok": True}

    @app.post("/api/v1/auth/login")
    def login(body: LoginInput, request: Request, response: Response, db=Depends(db_dependency)):
        key = request.client.host
        with rate_lock:
            current = time.monotonic()
            attempts = login_attempts[key]
            while attempts and current - attempts[0] > 300:
                attempts.popleft()
            if len(attempts) >= 10:
                raise HTTPException(429, "Too many attempts. Try again in five minutes")
            attempts.append(current)
        user = db.scalar(select(User).where(User.email == body.email.lower()))
        valid = verify_password(user.password_hash if user else DUMMY, body.password)
        if not user or not valid or user.role == "disabled":
            raise HTTPException(401, "Incorrect email or password")
        sign_in(db, response, user, settings)
        db.commit()
        return {"ok": True}

    @app.post("/api/v1/auth/logout")
    def logout(request: Request, response: Response, db=Depends(db_dependency)):
        session = db.get(LoginSession, token_hash(request.cookies.get(COOKIE, "")))
        if session:
            db.delete(session)
            db.commit()
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/v1/me")
    def me(user=Depends(current_user), db=Depends(db_dependency)):
        company = db.get(Company, user.company_id)
        sites = db.scalars(select(Site).where(Site.company_id == user.company_id)).all()
        events = db.scalars(
            select(SourceEvent)
            .where(SourceEvent.company_id == user.company_id)
            .order_by(SourceEvent.occurred_at.desc())
            .limit(1)
        ).all()
        default_date = (
            business_date(events[0].occurred_at, sites[0].timezone, sites[0].shift_start)
            if company.demo and events
            else now().astimezone(ZoneInfo(sites[0].timezone)).date()
        )
        return {
            "user": {"name": user.name, "email": user.email, "role": user.role},
            "company": {"id": company.id, "name": company.name, "demo": company.demo},
            "sites": [
                {
                    "id": s.id,
                    "name": s.name,
                    "timezone": s.timezone,
                    "shift_start": s.shift_start,
                    "shift_end": s.shift_end,
                    "weekdays": s.weekdays,
                    "grace_minutes": s.grace_minutes,
                    "break_minutes": s.break_minutes,
                    "retention_days": s.retention_days,
                    "policy_version": s.policy_version,
                    "payroll_policy": s.payroll_policy,
                }
                for s in sites
            ],
            "default_date": default_date.isoformat(),
        }

    @app.get("/api/v1/employees")
    def employees(site_id: str, user=Depends(current_user), db=Depends(db_dependency)):
        site_for(db, user, site_id)
        rows = db.scalars(
            select(Employee)
            .where(
                Employee.company_id == user.company_id,
                Employee.site_id == site_id,
                Employee.sample.is_(False),
            )
            .order_by(Employee.code)
        ).all()
        return [employee_json(e, user) for e in rows]

    @app.post("/api/v1/employees", status_code=201)
    def add_employee(body: EmployeeInput, user=Depends(current_user), db=Depends(db_dependency)):
        require_role(user, "admin")
        site_for(db, user, body.site_id)
        employee = Employee(company_id=user.company_id, **body.model_dump(mode="json"))
        db.add(employee)
        db.flush()
        audit(db, user, "employee.created", employee.id)
        db.commit()
        return employee_json(employee, user)

    @app.get("/api/v1/cameras")
    def cameras(site_id: str, user=Depends(current_user), db=Depends(db_dependency)):
        site_for(db, user, site_id)
        rows = db.scalars(
            select(Camera)
            .where(
                Camera.company_id == user.company_id,
                Camera.site_id == site_id,
                Camera.sample.is_(False),
                Camera.system.is_(False),
            )
            .order_by(Camera.role, Camera.name)
        ).all()
        return [
            {
                "id": c.id,
                "name": c.name,
                "role": c.role,
                "target_fps": c.target_fps,
                "status": app.state.vision.state(c.id)["state"]
                if app.state.vision
                else "not_connected",
                "detail": "Local camera runtime" if app.state.vision else "Local AI disabled",
            }
            for c in rows
        ]

    @app.post("/api/v1/cameras", status_code=201)
    def add_camera(body: CameraInput, user=Depends(current_user), db=Depends(db_dependency)):
        require_role(user, "admin")
        site_for(db, user, body.site_id)
        camera = Camera(company_id=user.company_id, **body.model_dump(mode="json"))
        db.add(camera)
        db.flush()
        audit(db, user, "camera.configured", camera.id)
        db.commit()
        return {"id": camera.id}

    @app.put("/api/v1/sites/{site_id}/policy")
    def save_policy(
        site_id: str, body: PolicyInput, user=Depends(current_user), db=Depends(db_dependency)
    ):
        require_role(user, "admin")
        site = site_for(db, user, site_id)
        for key, value in body.model_dump().items():
            setattr(site, key, value)
        site.policy_version += 1
        audit(db, user, "policy.updated", site.id, {"version": site.policy_version})
        db.commit()
        return {"version": site.policy_version}

    @app.get("/api/v1/attendance")
    def daily(site_id: str, day: date, user=Depends(current_user), db=Depends(db_dependency)):
        site = site_for(db, user, site_id)
        rows = day_rows(db, user, site, day)
        unknown = [
            e
            for e in event_query(db, user.company_id, site.id)
            if e.employee_id is None
            and e.kind in {"entry", "exit"}
            and business_date(e.occurred_at, site.timezone, site.shift_start) == day
        ]
        return {"rows": rows, "unknown_events": len(unknown)}

    @app.post("/api/v1/attendance/decisions")
    def decide(body: DecisionInput, user=Depends(current_user), db=Depends(db_dependency)):
        require_role(user, "admin", "hr")
        employee = employee_for(db, user, body.employee_id)
        site = site_for(db, user, employee.site_id)
        day = date.fromisoformat(body.business_date)
        if day > now().astimezone(ZoneInfo(site.timezone)).date():
            raise HTTPException(422, "Future attendance cannot be approved")
        state = attendance(
            [e for e in event_query(db, user.company_id, site.id) if e.employee_id == employee.id],
            day,
            site,
        )
        if body.outcome == "present" and not state["complete"]:
            raise HTTPException(
                409, "Resolve incomplete or contradictory movement events before approving presence"
            )
        if body.outcome in {"absent", "leave"} and state["first_in"]:
            raise HTTPException(
                409, "Confirmed presence conflicts with a full-day absence or leave decision"
            )
        from .payroll import calendar_map, day_kind

        schedule_kind = day_kind(
            day, site, calendar_map(db, user.company_id, site.id, day.strftime("%Y-%m"))
        )
        if schedule_kind == "working_day" and body.holiday_minutes:
            raise HTTPException(
                422, "Holiday work minutes apply only to holidays or weekly days off"
            )
        if schedule_kind != "working_day" and body.overtime_minutes:
            raise HTTPException(422, "Use holiday work minutes for a holiday or weekly day off")
        if (
            body.overtime_minutes > state["inside_minutes"]
            or body.holiday_minutes > state["inside_minutes"]
        ):
            raise HTTPException(
                422,
                "Approved extra-work minutes cannot exceed verified office time; correct movements first",
            )
        row = db.scalar(
            select(Decision).where(
                Decision.employee_id == employee.id,
                Decision.company_id == user.company_id,
                Decision.business_date == body.business_date,
            )
        )
        if not row:
            row = Decision(
                company_id=user.company_id,
                site_id=site.id,
                employee_id=employee.id,
                business_date=body.business_date,
            )
            db.add(row)
        row.outcome, row.paid_leave, row.reason = body.outcome, body.paid_leave, body.reason
        row.overtime_minutes, row.source_revision = body.overtime_minutes, state["revision"]
        row.holiday_minutes = body.holiday_minutes
        row.approved_by, row.approved_at = user.id, now()
        audit(
            db,
            user,
            "attendance.reviewed",
            employee.id,
            {"date": body.business_date, "outcome": body.outcome, "reason": body.reason},
        )
        db.commit()
        return {"ok": True}

    @app.get("/api/v1/events")
    def events(site_id: str, day: date, user=Depends(current_user), db=Depends(db_dependency)):
        site = site_for(db, user, site_id)
        rows = [
            e
            for e in event_query(db, user.company_id, site.id)
            if business_date(e.occurred_at, site.timezone, site.shift_start) == day
        ]
        return [
            {
                "id": e.id,
                "employee_id": e.employee_id,
                "camera_id": e.camera_id,
                "kind": e.kind,
                "occurred_at": utc(e.occurred_at),
                "identity_status": e.identity_status,
                "source": "simulated"
                if e.simulated
                else "manual"
                if e.boot_id == "manual"
                else "camera",
            }
            for e in sorted(rows, key=lambda e: utc(e.occurred_at), reverse=True)
        ]

    @app.post("/api/v1/payroll/preview")
    def preview(body: PayrollInput, user=Depends(current_user), db=Depends(db_dependency)):
        require_role(user, "admin", "payroll")
        return payroll_preview(db, user, site_for(db, user, body.site_id), body.month)

    @app.post("/api/v1/payroll/runs", status_code=201)
    def create_run(body: PayrollInput, user=Depends(current_user), db=Depends(db_dependency)):
        require_role(user, "admin", "payroll")
        snapshot = jsonable_encoder(
            payroll_preview(db, user, site_for(db, user, body.site_id), body.month)
        )
        run = PayrollRun(
            company_id=user.company_id,
            site_id=str(body.site_id),
            month=body.month,
            snapshot=snapshot,
            input_revision=snapshot["input_revision"],
            created_by=user.id,
        )
        db.add(run)
        db.flush()
        audit(db, user, "payroll.drafted", run.id, {"month": body.month})
        db.commit()
        return {"id": run.id, "status": run.status}

    @app.get("/api/v1/payroll/runs")
    def runs(site_id: str, user=Depends(current_user), db=Depends(db_dependency)):
        require_role(user, "admin", "payroll")
        site_for(db, user, site_id)
        rows = db.scalars(
            select(PayrollRun)
            .where(PayrollRun.company_id == user.company_id, PayrollRun.site_id == site_id)
            .order_by(PayrollRun.created_at.desc())
            .limit(30)
        ).all()
        return [
            {
                "id": r.id,
                "month": r.month,
                "status": r.status,
                "total": r.snapshot["total"],
                "created_at": r.created_at,
            }
            for r in rows
        ]

    @app.get("/api/v1/reports/attendance.csv")
    def export_csv(site_id: str, day: date, user=Depends(current_user), db=Depends(db_dependency)):
        site = site_for(db, user, site_id)
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            [
                "Employee ID",
                "Name",
                "Business date",
                "First IN (UTC)",
                "Last OUT (UTC)",
                "Confirmed office minutes",
                "Away minutes",
                "Review status",
                "Issues",
            ]
        )

        def safe(value):
            value = str(value or "")
            return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value

        for row in day_rows(db, user, site, day):
            writer.writerow(
                [
                    safe(v)
                    for v in [
                        row["employee"]["code"],
                        row["employee"]["name"],
                        day,
                        row["first_in"],
                        row["last_out"],
                        row["inside_minutes"],
                        row["away_minutes"],
                        row["status"],
                        "; ".join(row["issues"]),
                    ]
                ]
            )
        audit(db, user, "report.exported", site.id, {"date": day.isoformat(), "format": "csv"})
        db.commit()
        return Response(
            "\ufeff" + output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="attendance-{day.isoformat()}.csv"'
            },
        )

    @app.get("/api/v1/audit")
    def audits(user=Depends(current_user), db=Depends(db_dependency)):
        require_role(user, "admin")
        rows = db.scalars(
            select(Audit)
            .where(Audit.company_id == user.company_id)
            .order_by(Audit.created_at.desc())
            .limit(100)
        ).all()
        return [
            {
                "id": a.id,
                "action": a.action,
                "target": a.target,
                "detail": a.detail,
                "created_at": a.created_at,
            }
            for a in rows
        ]

    @app.get("/api/v1/agents")
    def agents(site_id: str, user=Depends(current_user), db=Depends(db_dependency)):
        require_role(user, "admin")
        site_for(db, user, site_id)
        rows = db.scalars(
            select(Agent).where(Agent.company_id == user.company_id, Agent.site_id == site_id)
        ).all()
        return [{"id": a.id, "name": a.name, "last_seen": a.last_seen} for a in rows]

    @app.post("/api/v1/agents", status_code=201)
    def add_agent(body: AgentInput, user=Depends(current_user), db=Depends(db_dependency)):
        require_role(user, "admin")
        site_for(db, user, body.site_id)
        token = secrets.token_urlsafe(48)
        agent = Agent(
            company_id=user.company_id,
            site_id=str(body.site_id),
            name=body.name,
            token_hash=token_hash(token),
        )
        db.add(agent)
        db.flush()
        audit(db, user, "agent.provisioned", agent.id)
        db.commit()
        return {"id": agent.id, "token": token, "notice": "Store locally; this token is shown once"}

    @app.post("/agent/v1/events:batch")
    def ingest(body: EventBatch, request: Request, db=Depends(db_dependency)):
        authorization = request.headers.get("authorization", "")
        raw = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        agent = db.scalar(select(Agent).where(Agent.token_hash == token_hash(raw))) if raw else None
        if not agent:
            raise HTTPException(401, "Invalid site agent credential")
        accepted, duplicates = [], []
        for item in body.events:
            payload = item.model_dump(mode="json")
            fingerprint = digest(payload)
            existing = db.get(SourceEvent, str(item.event_id))
            if existing:
                if existing.agent_id != agent.id or existing.fingerprint != fingerprint:
                    raise HTTPException(409, "Event ID conflicts with a different payload")
                duplicates.append(existing.id)
                continue
            camera = db.scalar(
                select(Camera).where(
                    Camera.id == str(item.camera_id),
                    Camera.company_id == agent.company_id,
                    Camera.site_id == agent.site_id,
                )
            )
            if not camera or camera.role != item.kind:
                raise HTTPException(422, "Camera is not a matching portal in this site")
            if item.employee_id:
                employee = db.scalar(
                    select(Employee).where(
                        Employee.id == str(item.employee_id),
                        Employee.company_id == agent.company_id,
                        Employee.site_id == agent.site_id,
                        Employee.active.is_(True),
                        Employee.sample.is_(False),
                    )
                )
                if not employee:
                    raise HTTPException(422, "Employee is not active in this site")
            if utc(item.occurred_at) > now() + timedelta(minutes=2):
                raise HTTPException(422, "Event capture time is in the future")
            row = SourceEvent(
                id=str(item.event_id),
                company_id=agent.company_id,
                site_id=agent.site_id,
                agent_id=agent.id,
                camera_id=camera.id,
                employee_id=str(item.employee_id) if item.employee_id else None,
                kind=item.kind,
                identity_status=item.identity_status,
                occurred_at=item.occurred_at,
                boot_id=item.boot_id,
                sequence=item.sequence,
                fingerprint=fingerprint,
            )
            db.add(row)
            db.flush()
            accepted.append(row.id)
        agent.last_seen = now()
        db.commit()
        return {"accepted": accepted, "duplicates": duplicates}

    from .vision.routes import register

    register(app, factory, current_user, site_for, employee_for, audit, settings)
    from .business import register as register_business

    register_business(
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
    )
    from .users import register as register_users

    register_users(app, factory, current_user, audit)
    return app


app = create_app()
