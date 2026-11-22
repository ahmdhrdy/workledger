"""Reviewed bootstrap of the former unversioned development schema, with backup."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect


def upgrade_local(engine, settings):
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "migrations")
    )
    config.attributes["database_url"] = settings.database_url
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "alembic_version" not in tables and tables:
        expected = {
            "companies",
            "sites",
            "users",
            "sessions",
            "employees",
            "cameras",
            "agents",
            "source_events",
            "attendance_decisions",
            "event_reviews",
            "payroll_runs",
            "audit_log",
        }
        required = {
            "employees": {
                "id",
                "company_id",
                "site_id",
                "code",
                "name",
                "department",
                "designation",
                "active",
                "monthly_salary",
                "overtime_rate",
                "salary_version",
            },
            "cameras": {"id", "company_id", "site_id", "name", "role", "target_fps"},
            "sites": {
                "id",
                "company_id",
                "name",
                "timezone",
                "shift_start",
                "shift_end",
                "weekdays",
                "grace_minutes",
                "break_minutes",
                "retention_days",
                "policy_version",
            },
        }
        if tables != expected or any(
            {column["name"] for column in inspector.get_columns(table)} != columns
            for table, columns in required.items()
        ):
            raise RuntimeError(
                "Unrecognized unversioned database. Preserve it and apply a reviewed migration; no automatic stamp was performed."
            )
        if not settings.database_url.startswith("sqlite:///"):
            raise RuntimeError("Legacy schema adoption is supported only for local SQLite")
        filename = Path(settings.database_url.removeprefix("sqlite:///"))
        backup_dir = filename.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        destination = backup_dir / (
            filename.stem + "-before-v03-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f") + ".db"
        )
        with sqlite3.connect(filename) as source, sqlite3.connect(destination) as backup:
            source.backup(backup)
        command.stamp(config, "dd46ee67ef13")
    command.upgrade(config, "head")
