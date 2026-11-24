"""Immutable hot configs and asynchronous, idempotent crossing persistence."""

import copy
import logging
import threading
import time
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from workledger.domain import digest
from workledger.models import CameraDoorZone, DoorCrossing, Employee, SourceEvent

from .door_crossing import DoorCrossingEngine

logger = logging.getLogger("uvicorn.error.workledger.door.storage")


class DoorZoneService:
    def __init__(self, factory):
        self.factory = factory
        self.lock = threading.RLock()
        self.configs = {}
        self.applied = {}
        self.engine = DoorCrossingEngine()
        self.outbox = {}
        self.error = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, name="door-event-writer", daemon=True)
        self.boot = str(uuid4())
        self.sequence = 0

    @staticmethod
    def snapshot(row):
        return {
            **copy.deepcopy(row.config),
            "id": row.id,
            "camera_id": row.camera_id,
            "company_id": row.company_id,
            "site_id": row.site_id,
            "version": row.version,
            "active": row.active,
            "effective_at": row.updated_at.timestamp(),
        }

    def install(self, config):
        with self.lock:
            old = self.configs.get(config["camera_id"])
            if old is None or config["version"] >= old["version"]:
                self.configs[config["camera_id"]] = copy.deepcopy(config)

    def refresh(self):
        with self.factory() as db:
            rows = db.scalars(select(CameraDoorZone)).all()
            snapshots = [self.snapshot(row) for row in rows]
        for config in snapshots:
            self.install(config)

    def config(self, camera):
        with self.lock:
            return copy.deepcopy(self.configs.get(camera))

    def status(self, camera):
        with self.lock:
            config = self.configs.get(camera)
            return {
                "saved_version": config["version"] if config else 0,
                "active": bool(config and config.get("active")),
                "anchor": config.get("anchor") if config else None,
                "last_processed_at": self.engine.diagnostics.get(camera, {}).get("at"),
                "last_processed_age_ms": round(
                    max(0, time.time() - self.engine.diagnostics[camera]["at"]) * 1000
                )
                if self.engine.diagnostics.get(camera, {}).get("at")
                else None,
                "tracks": [
                    track
                    for track in self.engine.diagnostics.get(camera, {}).get("tracks", {}).values()
                    if time.time() - track.get("at", 0) <= 10
                ],
                "reason_counts": dict(self.engine.diagnostics.get(camera, {}).get("counts", {})),
                "applied_version": self.applied.get(camera),
                "writer_error": self.error,
                "pending_writes": sum(e["camera_id"] == camera for e in self.outbox.values()),
            }

    def observe(self, camera, observations, at):
        with self.lock:
            config = self.configs.get(camera)
            if not config:
                return
            if len(self.outbox) >= 4096:
                self.error = "Crossing persistence backlog full; doorway processing paused"
                return
            events = self.engine.observe(camera, config, observations, at)
            self.applied[camera] = config["version"]
            self.engine.diagnostics.setdefault(camera, {})["at"] = at
            for event in events:
                self.outbox[event["id"]] = event
                logger.info(
                    "Door crossing camera=%s track=%s direction=%s status=%s reason=%s",
                    camera,
                    event["track_id"],
                    event["kind"],
                    event["status"],
                    event["reason"],
                )

    def persist(self, event):
        with self.factory() as db:
            row = db.scalar(
                select(DoorCrossing).where(DoorCrossing.id == event["id"]).with_for_update()
            )
            if row is not None and row.status in {"confirmed", "review"}:
                return
            details = {
                key: value
                for key, value in event.items()
                if key in {"reason", "deadline", "anchor", "confidence", "door_name"}
            }
            employee = (
                db.scalar(
                    select(Employee).where(
                        Employee.id == event.get("employee_id"),
                        Employee.company_id == event["company_id"],
                        Employee.site_id == event["site_id"],
                    )
                )
                if event.get("employee_id")
                else None
            )
            safe_employee = employee.id if employee is not None else None
            if row is None:
                row = DoorCrossing(
                    id=event["id"],
                    zone_id=event["zone_id"],
                    zone_version=event["zone_version"],
                    camera_id=event["camera_id"],
                    company_id=event["company_id"],
                    site_id=event["site_id"],
                    track_id=event["track_id"],
                    employee_id=safe_employee,
                    kind=event["kind"],
                    status=event["status"],
                    occurred_at=datetime.fromtimestamp(event["occurred_at"], UTC),
                    details=details,
                )
                db.add(row)
            row.status, row.details = event["status"], details
            row.employee_id = safe_employee
            if row.status == "confirmed":
                if employee is None or not employee.active or employee.sample:
                    row.status = "review"
                    row.details = {
                        **details,
                        "reason": "Employee is unavailable or outside this site",
                    }
                else:
                    # One source event per crossing, independent of same-day/hour repeats.
                    if db.get(SourceEvent, event["id"]) is None:
                        self.sequence += 1
                        db.add(
                            SourceEvent(
                                id=event["id"],
                                company_id=row.company_id,
                                site_id=row.site_id,
                                agent_id=None,
                                camera_id=row.camera_id,
                                employee_id=row.employee_id,
                                kind=row.kind,
                                identity_status="confirmed",
                                occurred_at=row.occurred_at,
                                boot_id=self.boot,
                                sequence=self.sequence,
                                fingerprint=digest(event),
                                simulated=False,
                            )
                        )
                        db.flush()
                    row.source_event_id = event["id"]
            db.commit()
            logger.info(
                "Door crossing persisted camera=%s crossing=%s status=%s",
                event["camera_id"],
                event["id"],
                row.status,
            )

    def expire_pending(self):
        with self.factory() as db:
            rows = db.scalars(
                select(DoorCrossing).where(DoorCrossing.status == "pending").limit(500)
            ).all()
            for row in rows:
                if row.details.get("deadline", float("inf")) < time.time():
                    row.status = "review"
                    row.details = {**row.details, "reason": "Verification window expired"}
            db.commit()

    def start(self):
        self.refresh()
        self.thread.start()

    def shutdown(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=4)

    def run(self):
        polled = 0
        while not self.stop.is_set() or self.outbox:
            try:
                if not self.stop.is_set() and time.monotonic() - polled > 1:
                    self.refresh()
                    self.expire_pending()
                    polled = time.monotonic()
                with self.lock:
                    batch = list(self.outbox.items())[:64]
                for identifier, event in batch:
                    self.persist(event)
                    with self.lock:
                        if self.outbox.get(identifier) is event:
                            self.outbox.pop(identifier, None)
                self.error = None
            except Exception as error:
                logger.warning("Door persistence retry error_type=%s", type(error).__name__)
                self.error = "Door log/config storage unavailable; queued writes will retry"
                if self.stop.is_set():
                    break
            self.stop.wait(0.1)
