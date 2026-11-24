from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """Normalize before SQLite drops offsets; always return aware UTC datetimes."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Stored timestamps must be timezone aware")
        value = value.astimezone(UTC)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def uid():
    return str(uuid4())


def now():
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Site(Base):
    __tablename__ = "sites"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Dhaka")
    shift_start: Mapped[str] = mapped_column(String(5), default="09:00")
    shift_end: Mapped[str] = mapped_column(String(5), default="18:00")
    weekdays: Mapped[list] = mapped_column(JSON, default=lambda: [0, 1, 2, 3, 4])
    grace_minutes: Mapped[int] = mapped_column(Integer, default=10)
    break_minutes: Mapped[int] = mapped_column(Integer, default=60)
    retention_days: Mapped[int] = mapped_column(Integer, default=90)
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    payroll_policy: Mapped[dict] = mapped_column(JSON, default=dict)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="admin")


class Session(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (UniqueConstraint("company_id", "code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(100))
    department: Mapped[str] = mapped_column(String(80), default="Operations")
    nickname: Mapped[str] = mapped_column(String(100), default="")
    sample: Mapped[bool] = mapped_column(Boolean, default=False)
    joined_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    left_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    designation: Mapped[str] = mapped_column(String(80), default="Employee")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    monthly_salary: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    overtime_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    salary_version: Mapped[int] = mapped_column(Integer, default=1)


class Camera(Base):
    __tablename__ = "cameras"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(16))
    target_fps: Mapped[int] = mapped_column(Integer, default=2)
    sample: Mapped[bool] = mapped_column(Boolean, default=False)
    system: Mapped[bool] = mapped_column(Boolean, default=False)


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    last_seen: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class SourceEvent(Base):
    __tablename__ = "source_events"
    __table_args__ = (UniqueConstraint("agent_id", "camera_id", "boot_id", "sequence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), index=True)
    employee_id: Mapped[str | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    identity_status: Mapped[str] = mapped_column(String(20))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    boot_id: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class Decision(Base):
    __tablename__ = "attendance_decisions"
    __table_args__ = (UniqueConstraint("employee_id", "business_date"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    business_date: Mapped[str] = mapped_column(String(10), index=True)
    outcome: Mapped[str] = mapped_column(String(20))
    paid_leave: Mapped[bool] = mapped_column(Boolean, default=False)
    overtime_minutes: Mapped[int] = mapped_column(Integer, default=0)
    holiday_minutes: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(Text)
    source_revision: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class EventReview(Base):
    __tablename__ = "event_reviews"
    event_id: Mapped[str] = mapped_column(ForeignKey("source_events.id"), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    employee_id: Mapped[str | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    outcome: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    visitor_name: Mapped[str] = mapped_column(String(100), default="")
    approved_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class PayrollRun(Base):
    __tablename__ = "payroll_runs"
    __table_args__ = (
        Index(
            "uq_finalized_payroll_site_month",
            "site_id",
            "month",
            unique=True,
            sqlite_where=text("status = 'finalized'"),
            postgresql_where=text("status = 'finalized'"),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    month: Mapped[str] = mapped_column(String(7))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    snapshot: Mapped[dict] = mapped_column(JSON)
    input_revision: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    finalized_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finalized_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class CalendarDay(Base):
    __tablename__ = "calendar_days"
    __table_args__ = (UniqueConstraint("site_id", "day"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(24))
    note: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)


class PayAdjustment(Base):
    __tablename__ = "pay_adjustments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    month: Mapped[str] = mapped_column(String(7), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Audit(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    actor_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(100))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class CameraDoorZone(Base):
    __tablename__ = "camera_zones"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), unique=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict] = mapped_column(JSON)
    updated_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class DoorZoneVersion(Base):
    __tablename__ = "camera_zone_versions"
    zone_id: Mapped[str] = mapped_column(ForeignKey("camera_zones.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    config: Mapped[dict] = mapped_column(JSON)
    updated_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class DoorCrossing(Base):
    __tablename__ = "door_crossings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    zone_id: Mapped[str] = mapped_column(ForeignKey("camera_zones.id"), index=True)
    zone_version: Mapped[int] = mapped_column(Integer)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    track_id: Mapped[str] = mapped_column(String(100))
    employee_id: Mapped[str | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    source_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_events.id"), nullable=True, unique=True
    )
    details: Mapped[dict] = mapped_column(JSON)
