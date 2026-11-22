from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SetupInput(Strict):
    company: str = Field(min_length=2, max_length=120)
    site: str = Field(min_length=2, max_length=120)
    name: str = Field(min_length=2, max_length=100)
    email: str = Field(min_length=5, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=12, max_length=128)
    demo: bool = False


class LoginInput(Strict):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class EmployeeInput(Strict):
    site_id: UUID
    code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=100)
    nickname: str = Field(default="", max_length=100)
    joined_on: str | None = None
    department: str = Field(min_length=1, max_length=80)
    designation: str = Field(min_length=1, max_length=80)
    monthly_salary: Decimal = Field(ge=0, le=10000000, decimal_places=2)
    overtime_rate: Decimal = Field(ge=0, le=100000, decimal_places=2)

    @field_validator("joined_on")
    @classmethod
    def valid_joining(cls, value):
        if value:
            date.fromisoformat(value)
        return value or None


class CameraInput(Strict):
    site_id: UUID
    name: str = Field(min_length=2, max_length=100)
    role: Literal["entry", "exit", "interior"]
    target_fps: int = Field(ge=1, le=15)


class PolicyInput(Strict):
    timezone: str
    shift_start: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    weekdays: list[int] = Field(min_length=1, max_length=7)
    grace_minutes: int = Field(ge=0, le=120)
    break_minutes: int = Field(ge=0, le=240)
    retention_days: Literal[90, 365]

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value

    @field_validator("weekdays")
    @classmethod
    def valid_days(cls, value):
        if len(set(value)) != len(value) or any(x < 0 or x > 6 for x in value):
            raise ValueError("Use unique weekday numbers 0–6")
        return sorted(value)

    @model_validator(mode="after")
    def distinct_shift_times(self):
        if self.shift_start == self.shift_end:
            raise ValueError("Shift start and end must differ")
        return self


class EventInput(Strict):
    schema_version: Literal[1] = 1
    event_id: UUID
    camera_id: UUID
    employee_id: UUID | None = None
    kind: Literal["entry", "exit"]
    identity_status: Literal["confirmed", "unknown", "ambiguous"]
    occurred_at: AwareDatetime
    boot_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    sequence: int = Field(ge=0)

    @model_validator(mode="after")
    def identity_binding(self):
        if self.identity_status == "confirmed" and self.employee_id is None:
            raise ValueError("Confirmed identities need an employee ID")
        return self


class EventBatch(Strict):
    events: list[EventInput] = Field(min_length=1, max_length=100)


class DecisionInput(Strict):
    employee_id: UUID
    business_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    outcome: Literal["present", "leave", "absent"]
    paid_leave: bool = False
    overtime_minutes: int = Field(ge=0, le=720)
    holiday_minutes: int = Field(default=0, ge=0, le=1440)
    reason: str = Field(min_length=5, max_length=1000)

    @field_validator("business_date")
    @classmethod
    def valid_date(cls, value):
        datetime.strptime(value, "%Y-%m-%d")
        return value

    @model_validator(mode="after")
    def outcome_rules(self):
        if self.outcome != "leave" and self.paid_leave:
            raise ValueError("Paid leave applies only to leave decisions")
        if self.outcome != "present" and (self.overtime_minutes or self.holiday_minutes):
            raise ValueError("Only presence can receive overtime")
        return self


class PayrollInput(Strict):
    site_id: UUID
    month: str = Field(pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$")

    @field_validator("month")
    @classmethod
    def valid_month(cls, value):
        date.fromisoformat(value + "-01")
        return value


class AgentInput(Strict):
    site_id: UUID
    name: str = Field(min_length=2, max_length=100)
