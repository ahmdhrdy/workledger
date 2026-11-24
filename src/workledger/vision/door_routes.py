"""Tenant-scoped administrator door config; saves never reconnect cameras."""

from fastapi import Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from workledger.models import CameraDoorZone, DoorCrossing, DoorZoneVersion, Employee, now
from workledger.security import require_role

from .door_crossing import DoorZoneInput
from .door_service import DoorZoneService


def register_door_routes(app, local, current_user, db_dependency, camera_for, site_for, audit):
    def output(row):
        return {
            "version": row.version,
            "id": row.id,
            "camera_id": row.camera_id,
            "active": row.active,
            **row.config,
            "updated_at": row.updated_at.isoformat(),
        }

    @app.get("/api/v1/local/cameras/{identifier}/door-zone")
    def get_zone(
        identifier: str,
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        camera = camera_for(db, user, identifier)
        site_for(db, user, camera.site_id)
        row = db.scalar(select(CameraDoorZone).where(CameraDoorZone.camera_id == identifier))
        return (
            output(row)
            if row
            else {"camera_id": identifier, "version": 0, "active": False, "zones": []}
        )

    @app.put("/api/v1/local/cameras/{identifier}/door-zone")
    def save_zone(
        identifier: str,
        body: DoorZoneInput,
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        require_role(user, "admin")
        camera = camera_for(db, user, identifier)
        site_for(db, user, camera.site_id)
        row = db.scalar(select(CameraDoorZone).where(CameraDoorZone.camera_id == identifier))
        if (row.version if row else 0) != body.expected_version:
            raise HTTPException(
                409, "Door zone changed elsewhere. Reload saved geometry before saving."
            )
        config = body.model_dump(exclude={"expected_version"}, mode="json")
        stamp = now()
        if row:
            previous = row.version
            changed = db.execute(
                update(CameraDoorZone)
                .where(CameraDoorZone.id == row.id, CameraDoorZone.version == previous)
                .values(
                    config=config,
                    active=body.active,
                    version=previous + 1,
                    updated_by=user.id,
                    updated_at=stamp,
                )
            )
            if changed.rowcount != 1:
                db.rollback()
                raise HTTPException(409, "Door zone changed elsewhere; reload first")
            db.refresh(row)
        else:
            row = CameraDoorZone(
                camera_id=identifier,
                company_id=user.company_id,
                site_id=camera.site_id,
                version=1,
                active=body.active,
                config=config,
                updated_by=user.id,
                updated_at=stamp,
            )
            db.add(row)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                raise HTTPException(409, "Door zone changed elsewhere; reload first") from None
        db.add(
            DoorZoneVersion(
                zone_id=row.id,
                version=row.version,
                config=config,
                updated_by=user.id,
                updated_at=stamp,
            )
        )
        audit(
            db,
            user,
            "camera.door_zone_updated",
            identifier,
            {"version": row.version, "active": row.active},
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Door zone changed elsewhere; reload first") from None
        runtime.doors.install(DoorZoneService.snapshot(row))
        return {**output(row), **runtime.doors.status(identifier)}

    @app.get("/api/v1/local/cameras/{identifier}/door-zone/status")
    def status(
        identifier: str,
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        camera = camera_for(db, user, identifier)
        site_for(db, user, camera.site_id)
        return {
            **runtime.doors.status(identifier),
            "camera_running": identifier in runtime.workers,
            "camera_name": camera.name,
            "camera_role": camera.role,
            "visual_tracking_error": runtime.states.get(identifier, {}).get(
                "visual_tracking_error", False
            ),
        }

    @app.get("/api/v1/local/cameras/{identifier}/door-crossings")
    def crossings(
        identifier: str,
        limit: int = 100,
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        require_role(user, "admin", "hr")
        camera = camera_for(db, user, identifier)
        site_for(db, user, camera.site_id)
        rows = db.execute(
            select(DoorCrossing, Employee.name)
            .outerjoin(Employee, Employee.id == DoorCrossing.employee_id)
            .where(DoorCrossing.camera_id == identifier, DoorCrossing.company_id == user.company_id)
            .order_by(DoorCrossing.occurred_at.desc())
            .limit(max(1, min(limit, 500)))
        ).all()
        return [
            {
                "id": row.id,
                "kind": row.kind,
                "employee_id": row.employee_id,
                "employee_name": name,
                "camera_name": camera.name,
                "zone_name": row.details.get("door_name", "Doorway"),
                "confidence": row.details.get("confidence"),
                "track_id": row.track_id,
                "status": row.status,
                "occurred_at": row.occurred_at.isoformat(),
                "zone_version": row.zone_version,
                "reason": row.details.get("reason", ""),
            }
            for row, name in rows
        ]
