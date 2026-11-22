import base64
import json
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from datetime import time as day_time
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import cv2
import numpy as np
from fastapi import Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import Field, SecretStr, field_validator, model_validator
from sqlalchemy import select

from workledger.models import Camera, Employee, EventReview, SourceEvent, now
from workledger.schemas import EmployeeInput, Strict
from workledger.security import require_role

from . import downloads
from .enrollment import sample_metadata as enrollment_metadata
from .onvif import discover, rtsp_url, validate_host
from .presence import summarize
from .preview import PreviewTracker
from .zones import validate_polygon


class CameraSetup(Strict):
    site_id: UUID
    name: str = Field(min_length=2, max_length=100)
    role: Literal["entry", "exit", "interior"]
    host: str
    username: str = Field(max_length=128)
    password: SecretStr | None = None
    connection: Literal["auto", "manual"] = "auto"
    onvif_port: int = Field(default=80, ge=1, le=65535)
    rtsp_port: int = Field(default=554, ge=1, le=65535)
    path: str = Field(default="", max_length=1000)
    target_fps: int = Field(default=2, ge=1, le=8)
    zone_name: str = Field(default="Workplace", min_length=1, max_length=80)
    zone_type: Literal["work", "meeting", "break", "transit"] = "work"
    trigger: Literal["presence", "line"] = "presence"
    line_axis: Literal["vertical", "horizontal"] = "vertical"
    line_position: float = Field(default=0.5, ge=0.05, le=0.95)
    line_direction: Literal["positive", "negative"] = "positive"

    @field_validator("host")
    @classmethod
    def valid_host(cls, value):
        return validate_host(value)

    @model_validator(mode="after")
    def path_required(self):
        if self.connection == "manual" and self.path:
            rtsp_url({"host": self.host, "path": self.path, "rtsp_port": self.rtsp_port})
        return self


class CameraAction(Strict):
    action: Literal["start", "stop", "discover"]


class ResolveUnknown(Strict):
    employee_id: UUID | None = None
    classification: Literal["employee", "visitor", "false_detection"] | None = None
    visitor_name: str = Field(default="", max_length=100)
    reason: str = Field(min_length=5, max_length=1000)

    @model_validator(mode="after")
    def valid_assignment(self):
        if self.classification == "employee" and not self.employee_id:
            raise ValueError("Select the employee HR recognized")
        if self.classification in {"visitor", "false_detection"} and self.employee_id:
            raise ValueError("Visitor/false detections cannot be assigned to an employee")
        return self


class Zone(Strict):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=2, max_length=100)
    kind: Literal["workstation", "meeting", "break", "transit"] = "workstation"
    points: list[list[float]]
    employees: list[UUID] = Field(default_factory=list, max_length=1000)
    applies_all: bool = False
    counts_as_work: bool = True
    priority: int = Field(default=0, ge=0, le=100)
    allowed_from: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    allowed_until: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")

    @field_validator("points")
    @classmethod
    def polygon(cls, value):
        return validate_polygon(value)

    @model_validator(mode="after")
    def assigned(self):
        if (
            bool(self.allowed_from) != bool(self.allowed_until)
            or self.allowed_from
            and self.allowed_from == self.allowed_until
        ):
            raise ValueError("Set both ends of a nonzero allowed time window, or leave both blank")
        if not self.applies_all and not self.employees:
            raise ValueError("Assign employees or apply this area to everyone")
        if self.kind in {"break", "transit"} and self.counts_as_work:
            raise ValueError("Break/transit areas must not count as work")
        return self


class Zones(Strict):
    zones: list[Zone] = Field(max_length=64)


def register(app, factory, current_user, site_for, employee_for, audit, settings):
    def db_dependency():
        with factory() as db:
            yield db

    def local(request: Request):
        if (
            not settings.local_ai
            or settings.mode == "production"
            or request.client.host not in {"127.0.0.1", "::1", "testclient"}
        ):
            raise HTTPException(403, "Local camera services are accessible only on this laptop")
        runtime = app.state.vision
        if runtime is None:
            raise HTTPException(503, "Local AI service is disabled")
        return runtime

    def camera_for(db, user, identifier):
        camera = db.scalar(
            select(Camera).where(Camera.id == identifier, Camera.company_id == user.company_id)
        )
        if not camera:
            raise HTTPException(404, "Camera not found")
        return camera

    def configure(runtime, identifier, config):
        try:
            runtime.states[identifier] = {
                "state": "discovering",
                "message": "Discovering ONVIF media profiles",
            }
            if config["connection"] == "auto":
                config = discover(config)
            runtime.store.put_camera(identifier, config)
            runtime.connect(identifier)
        except Exception:
            runtime.states[identifier] = {
                "state": "error",
                "message": "Cannot discover/open stream. Check ONVIF access, camera time, IP and credentials. Use manual RTSP if discovery is unsupported.",
            }

    @app.get("/api/v1/local/status")
    def status(runtime=Depends(local), user=Depends(current_user)):
        return {
            "tracking_policy": {
                "confirmation_votes": settings.confirmation_frames,
                "lost_seconds": settings.track_lost_seconds,
                "recovery_seconds": settings.recovery_seconds,
                "verification_seconds": settings.face_verify_seconds,
                "identity_policy": "sticky_per_track",
            },
            "model_state": runtime.model_state,
            "message": runtime.model_message,
            "missing_models": downloads.missing(settings.model_dir),
            "model_id": runtime.model_id,
            "gallery_generations": [
                {
                    "company": company,
                    "site": site,
                    **{key: entry[key] for key in ("version", "samples", "embeddings")},
                }
                for (company, site), entry in runtime.gallery_index.entries.items()
                if company == user.company_id
            ],
            "gallery_errors": [
                {"site": site, "error": error}
                for (company, site), error in runtime.gallery_errors.items()
                if company == user.company_id
            ],
            "models": [
                "RetinaFace ResNet50 face detector",
                "AdaFace IR50 MS1MV2 recognition",
                "YOLOX-Nano person detector",
                "OpenVINO body re-identification",
            ],
            "device": "CPU",
            "inference_threads": settings.inference_threads,
            "inference_fps_limit": settings.inference_fps,
            "portal_fps_limit": settings.portal_inference_fps,
            "face_threshold": settings.face_threshold,
            "side_face_threshold": settings.side_face_threshold,
            "side_face_margin": settings.side_face_margin,
            "attendance_face_threshold": settings.attendance_face_threshold,
            "face_blur_min": settings.face_blur_min,
            "face_margin": settings.face_margin,
            "confirmation_frames": settings.confirmation_frames,
            "anti_spoofing": False,
            "masked_face_accuracy": "Requires site validation; no guarantee",
            "body_matching": "Tentative presence only; not accepted for attendance",
        }

    @app.post("/api/v1/local/models/load")
    def load_models(runtime=Depends(local), user=Depends(current_user)):
        require_role(user, "admin")
        if downloads.missing(settings.model_dir):
            raise HTTPException(
                409, "Run the model installer from scripts/install-models.ps1 first"
            )
        threading.Thread(target=runtime.load_model, daemon=True).start()
        return {"state": "loading"}

    @app.get("/api/v1/local/cameras")
    def list_cameras(
        site_id: str, runtime=Depends(local), user=Depends(current_user), db=Depends(db_dependency)
    ):
        site_for(db, user, site_id)
        cameras = db.scalars(
            select(Camera)
            .where(
                Camera.company_id == user.company_id,
                Camera.site_id == site_id,
                Camera.sample.is_(False),
                Camera.system.is_(False),
            )
            .order_by(Camera.name)
        ).all()
        result = []
        for camera in cameras:
            config = runtime.store.camera(camera.id) or {}
            safe = {
                key: config.get(key)
                for key in [
                    "host",
                    "username",
                    "connection",
                    "onvif_port",
                    "rtsp_port",
                    "zone_name",
                    "zone_type",
                    "trigger",
                    "line_axis",
                    "line_position",
                    "line_direction",
                    "profile",
                ]
            }
            result.append(
                {
                    "id": camera.id,
                    "name": camera.name,
                    "role": camera.role,
                    "target_fps": camera.target_fps,
                    **safe,
                    "has_password": bool(config.get("password")),
                    **runtime.state(camera.id),
                }
            )
        return result

    @app.post("/api/v1/local/cameras", status_code=201)
    def onboard(
        body: CameraSetup,
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        require_role(user, "admin")
        site_for(db, user, body.site_id)
        if body.connection == "manual" and not body.path:
            raise HTTPException(422, "Provide an RTSP path for manual onboarding")
        camera = Camera(
            company_id=user.company_id,
            site_id=str(body.site_id),
            name=body.name,
            role=body.role,
            target_fps=body.target_fps,
        )
        db.add(camera)
        db.flush()
        config = body.model_dump(mode="json", exclude={"password"})
        config["password"] = body.password.get_secret_value() if body.password else ""
        config.update(enabled=False, company_id=user.company_id)
        runtime.store.put_camera(camera.id, config)
        audit(db, user, "camera.local_onboarded", camera.id, {"role": camera.role})
        db.commit()
        threading.Thread(target=configure, args=(runtime, camera.id, config), daemon=True).start()
        return {
            "id": camera.id,
            "state": "discovering" if body.connection == "auto" else "connecting",
        }

    @app.put("/api/v1/local/cameras/{identifier}")
    def update_camera(
        identifier: str,
        body: CameraSetup,
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        require_role(user, "admin")
        camera = camera_for(db, user, identifier)
        if str(body.site_id) != camera.site_id:
            raise HTTPException(
                422, "A camera cannot move between sites through connection editing"
            )
        old = runtime.store.camera(identifier) or {}
        if body.connection == "manual" and not body.path:
            body.path = old.get("path", "")
            if not body.path:
                raise HTTPException(422, "Provide the camera's RTSP stream path")
        runtime.disconnect(identifier)
        config = body.model_dump(mode="json", exclude={"password"})
        config["password"] = (
            body.password.get_secret_value() if body.password else old.get("password", "")
        )
        config.update(enabled=False, company_id=user.company_id)
        runtime.store.put_camera(identifier, config)
        camera.name, camera.role, camera.target_fps = body.name, body.role, body.target_fps
        audit(db, user, "camera.local_updated", identifier, {"role": camera.role})
        db.commit()
        threading.Thread(target=configure, args=(runtime, identifier, config), daemon=True).start()
        return {"ok": True}

    @app.post("/api/v1/local/cameras/{identifier}/action")
    def action(
        identifier: str,
        body: CameraAction,
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        require_role(user, "admin")
        camera_for(db, user, identifier)
        if body.action == "stop":
            runtime.disconnect(identifier)
        else:
            config = runtime.store.camera(identifier)
            if not config:
                raise HTTPException(409, "Configure this camera's IP address and credentials first")
            if body.action == "start" and config.get("path"):
                config["connection"] = "manual"
            threading.Thread(
                target=configure, args=(runtime, identifier, config), daemon=True
            ).start()
        audit(db, user, "camera." + body.action, identifier)
        db.commit()
        return {"ok": True}

    @app.get("/api/v1/local/cameras/{identifier}/frame")
    def frame(
        identifier: str,
        raw: bool = False,
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        camera_for(db, user, identifier)
        with runtime.lock:
            preview = runtime.preview_trackers.get(identifier)
            if preview is None:
                preview = PreviewTracker()
                runtime.preview_trackers[identifier] = preview
        # Acquire the preview lock before choosing the frame. Concurrent viewers
        # must not advance geometry beyond the image returned to an older request.
        with preview.lock:
            with runtime.lock:
                frame = runtime.latest_frame(identifier)
                if not frame or time.time() - frame["at"] > 8:
                    raise HTTPException(503, "No recent camera frame")
                overlay = runtime.overlays.get(identifier)
                content = frame["jpeg"]
                timestamp = frame["at"]
            result = (
                preview.render(frame, overlay)
                if not raw
                else {
                    "labels": [],
                    "people": -1,
                    "analysis_age_ms": -1,
                    "tracking_ms": 0,
                }
            )
        return Response(
            content,
            media_type="image/jpeg",
            headers={
                "X-Captured-At": str(timestamp),
                "X-People-Count": str(result["people"]),
                "X-Analysis-Age-Ms": str(result["analysis_age_ms"]),
                "X-Preview-Tracking-Ms": str(result["tracking_ms"]),
                "X-Frame-Age-Ms": str(round(max(0, time.time() - timestamp) * 1000)),
                "Cache-Control": "no-store",
                "X-Workledger-Geometry": base64.b64encode(
                    json.dumps(
                        overlay.get("geometry", {}) if overlay and not raw else {},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode()
                ).decode(),
                "X-Workledger-Labels": base64.b64encode(
                    json.dumps(result["labels"], ensure_ascii=False, separators=(",", ":")).encode()
                ).decode(),
            },
        )

    @app.delete("/api/v1/local/cameras/{identifier}")
    def archive_camera(
        identifier: str,
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        require_role(user, "admin")
        camera = camera_for(db, user, identifier)
        runtime.disconnect(identifier)
        camera.sample = True
        audit(db, user, "camera.archived", identifier, {"name": camera.name})
        db.commit()
        return {"ok": True}

    @app.get("/api/v1/local/enrollments")
    def enrollments(
        site_id: str, runtime=Depends(local), user=Depends(current_user), db=Depends(db_dependency)
    ):
        require_role(user, "admin", "hr")
        site_for(db, user, site_id)
        employees = db.scalars(
            select(Employee).where(
                Employee.company_id == user.company_id,
                Employee.site_id == site_id,
                Employee.sample.is_(False),
            )
        ).all()
        templates = runtime.store.templates(user.company_id)
        legacy = [
            template
            for template in runtime.store.templates(user.company_id, include_legacy=True)
            if template["model"] != runtime.model_id
        ]
        return [
            {
                "employee_id": e.id,
                "name": e.name,
                "legacy_samples": [
                    {
                        key: template.get(key)
                        for key in ("id", "variant", "source_name", "has_preview", "model")
                    }
                    for template in legacy
                    if template["employee_id"] == e.id
                ],
                "samples": [
                    {
                        key: (
                            [{"kind": variant["kind"]} for variant in t.get("augmentations", [])]
                            if key == "augmentations"
                            else t.get(key)
                        )
                        for key in (
                            "id",
                            "variant",
                            "source_name",
                            "quality",
                            "detection_score",
                            "created_at",
                            "has_preview",
                            "model",
                            "augmentations",
                        )
                    }
                    for t in templates
                    if t["employee_id"] == e.id
                ],
            }
            for e in employees
        ]

    def save_face(
        runtime,
        db,
        user,
        employee,
        image,
        variant,
        source_name=None,
        data=None,
        augment_masked=False,
    ):
        if image is None or max(image.shape[:2]) > 4096:
            raise HTTPException(422, "Provide a valid image no larger than 4096 pixels per side")
        try:
            model = runtime.load_model()
            face = (
                model.enrollment(image, augment_masked=True)
                if augment_masked
                else model.enrollment(image)
            )
            metadata = enrollment_metadata(image, face, source_name, data)
            identifier = str(uuid4())
            saved_id = runtime.store.add_template(
                identifier, user.company_id, employee.id, variant, face["vector"].tolist(), metadata
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        runtime.templates_cache.clear()
        duplicate = saved_id != identifier
        if not duplicate:
            audit(db, user, "face.enrolled_locally", employee.id, {"variant": variant})
        db.commit()
        gallery = runtime.refresh_gallery(user.company_id, employee.site_id)
        return {
            "ok": True,
            "gallery": gallery,
            "model_id": runtime.model_id,
            "augmentation_count": next(
                (
                    len(sample.get("augmentations", []))
                    for sample in runtime.store.templates(user.company_id)
                    if sample["id"] == saved_id
                ),
                0,
            ),
            "sample_id": saved_id,
            "duplicate": duplicate,
            "quality": round(face["quality"], 1),
            "detection_score": metadata["detection_score"],
            "notice": "Employee recognition gallery updated. Encrypted review thumbnail retained.",
        }

    @app.get("/api/v1/local/employees/{identifier}/enrollment/{sample_id}/image")
    def enrollment_image(
        identifier: str,
        sample_id: str,
        augmentation: str | None = None,
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        require_role(user, "admin", "hr")
        employee_for(db, user, identifier)
        thumbnail = runtime.store.template_preview(
            user.company_id, identifier, sample_id, augmentation
        )
        if not thumbnail:
            raise HTTPException(404, "Review image unavailable for this sample")
        return Response(
            base64.b64decode(thumbnail),
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @app.delete("/api/v1/local/employees/{identifier}/enrollment/{sample_id}")
    def remove_enrollment_sample(
        identifier: str,
        sample_id: str,
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        require_role(user, "admin", "hr")
        employee = employee_for(db, user, identifier)
        if not runtime.store.remove_template(user.company_id, identifier, sample_id):
            raise HTTPException(404, "Enrollment sample not found")
        runtime.templates_cache.clear()
        runtime.gallery.remove(employee.site_id, identifier)
        with runtime.lock:
            for tracker in runtime.trackers.values():
                tracker.tracks.clear()
        audit(db, user, "face.sample_deleted", identifier, {"sample_id": sample_id})
        db.commit()
        gallery = runtime.refresh_gallery(user.company_id, employee.site_id)
        return {"ok": True, "gallery": gallery}

    @app.post("/api/v1/local/employees/{identifier}/enroll")
    def enroll(
        identifier: str,
        variant: Literal["uncovered", "masked", "front", "left_30", "right_30"] = "uncovered",
        image: UploadFile = File(...),
        augment_masked: bool = Form(False),
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        require_role(user, "admin", "hr")
        employee = employee_for(db, user, identifier)
        data = image.file.read(5_000_001)
        if len(data) > 5_000_000:
            raise HTTPException(413, "Image must be under 5 MB")
        frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        return save_face(
            runtime, db, user, employee, frame, variant, image.filename, data, augment_masked
        )

    @app.post("/api/v1/local/employees/{identifier}/enroll-camera/{camera_id}")
    def enroll_camera(
        identifier: str,
        camera_id: str,
        variant: Literal["uncovered", "masked", "front", "left_30", "right_30"] = "uncovered",
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        require_role(user, "admin", "hr")
        employee = employee_for(db, user, identifier)
        camera = camera_for(db, user, camera_id)
        if employee.site_id != camera.site_id:
            raise HTTPException(422, "Use a camera in the employee's site")
        with runtime.lock:
            frame = runtime.frames.get(camera_id)
            if not frame or time.time() - frame["at"] > 3:
                raise HTTPException(409, "A recent camera frame is required")
            image = cv2.imdecode(np.frombuffer(frame["jpeg"], np.uint8), cv2.IMREAD_COLOR)
        return save_face(runtime, db, user, employee, image, variant)

    @app.delete("/api/v1/local/employees/{identifier}/enrollment")
    def remove_enrollment(
        identifier: str,
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        require_role(user, "admin", "hr")
        employee = employee_for(db, user, identifier)
        runtime.store.remove_templates(user.company_id, identifier)
        runtime.templates_cache.clear()
        runtime.gallery.remove(employee.site_id, identifier)
        with runtime.lock:
            # No live box may keep a deleted biometric identity.
            for tracker in runtime.trackers.values():
                tracker.tracks.clear()
        audit(db, user, "face.enrollment_deleted", identifier)
        db.commit()
        runtime.refresh_gallery(user.company_id, employee.site_id)
        return {"ok": True}

    @app.get("/api/v1/local/presence")
    def presence(
        site_id: str,
        day: date,
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        site = site_for(db, user, site_id)
        employees = db.scalars(
            select(Employee).where(
                Employee.company_id == user.company_id,
                Employee.site_id == site_id,
                Employee.sample.is_(False),
            )
        ).all()
        start = datetime.combine(
            day, day_time.fromisoformat(site.shift_start), ZoneInfo(site.timezone)
        ).timestamp()
        end_day = day + timedelta(days=1) if site.shift_end <= site.shift_start else day
        end = datetime.combine(
            end_day, day_time.fromisoformat(site.shift_end), ZoneInfo(site.timezone)
        ).timestamp()
        observations = runtime.store.observations([e.id for e in employees], start, end)
        grouped = defaultdict(list)
        for record in observations:
            grouped[record["employee_id"]].append(record)
        results = []
        for employee in employees:
            records = grouped[employee.id]
            window_end = max(start, min(end, time.time()))
            results.append(
                {
                    "employee_id": employee.id,
                    "name": employee.name,
                    **summarize(records, start, window_end, site.break_minutes * 60),
                }
            )
        return {
            "day": str(day),
            "items": results,
            "notice": "Intervals within the scheduled shift up to now, without overlapping-camera double counting. Eligible observed time excludes approved break-zone time up to the office allowance. Coverage uses elapsed shift time; gaps are unknown. Face-confirmed and tentative identity stay separate. Presence does not measure productivity.",
        }

    @app.get("/api/v1/local/events/{identifier}/evidence")
    def evidence(
        identifier: str,
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        require_role(user, "admin", "hr")
        source = db.scalar(
            select(SourceEvent.id).where(
                SourceEvent.id == identifier, SourceEvent.company_id == user.company_id
            )
        )
        jpeg = runtime.store.evidence(identifier, user.company_id) if source else None
        if not jpeg:
            raise HTTPException(404, "Evidence is unavailable or its retention period has expired")
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/v1/local/events/{identifier}/resolve")
    def resolve(
        identifier: str,
        body: ResolveUnknown,
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        require_role(user, "admin", "hr")
        source = db.scalar(
            select(SourceEvent).where(
                SourceEvent.id == identifier, SourceEvent.company_id == user.company_id
            )
        )
        if not source or source.identity_status == "confirmed":
            raise HTTPException(409, "Only unmatched detections can be reviewed here")
        original = {
            "fingerprint": source.fingerprint,
            "identity_status": source.identity_status,
            "employee_id": source.employee_id,
        }
        if body.employee_id:
            employee = employee_for(db, user, body.employee_id)
            if employee.site_id != source.site_id:
                raise HTTPException(422, "Employee must belong to the camera site")
            assigned = employee.id
            if not employee.active or employee.sample:
                raise HTTPException(422, "Select an active enrolled employee")
        else:
            assigned = None
        review = db.get(EventReview, source.id)
        if not review:
            review = EventReview(event_id=source.id, company_id=user.company_id)
            db.add(review)
        review.employee_id, review.outcome, review.reason = (
            assigned,
            "assigned"
            if assigned
            else "visitor"
            if body.classification == "visitor"
            else "dismissed",
            body.reason,
        )
        review.approved_by, review.approved_at = user.id, now()
        review.visitor_name = body.visitor_name if review.outcome == "visitor" else ""
        audit(
            db,
            user,
            "identity.reviewed",
            source.id,
            {
                "original": original,
                "employee_id": assigned,
                "reason": body.reason,
                "outcome": review.outcome,
                "visitor_name": review.visitor_name,
            },
        )
        db.commit()
        if assigned:
            context = runtime.store.context(source.id, time.time())
            if context:
                site = site_for(db, user, source.site_id)
                runtime.gallery.add(
                    site.id,
                    assigned,
                    source.camera_id,
                    context["body"],
                    context["at"],
                    site.timezone,
                    {
                        "name": employee.name,
                        "code": employee.code,
                        "nickname": employee.nickname,
                        "clothing": context.get("clothing", {}),
                        "source": "hr_review",
                    },
                )
        return {"ok": True}

    @app.get("/api/v1/local/cameras/{identifier}/zones")
    def zones(
        identifier: str,
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        camera_for(db, user, identifier)
        return runtime.store.zones(identifier)

    @app.put("/api/v1/local/cameras/{identifier}/zones")
    def save_zones(
        identifier: str,
        body: Zones,
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        require_role(user, "admin", "hr")
        camera = camera_for(db, user, identifier)
        if camera.role != "interior":
            raise HTTPException(422, "Workstation polygons belong on workplace cameras")
        if len({zone.id for zone in body.zones}) != len(body.zones):
            raise HTTPException(422, "Zone identifiers must be unique")
        for zone in body.zones:
            for employee_id in zone.employees:
                employee = employee_for(db, user, employee_id)
                if employee.site_id != camera.site_id or employee.sample:
                    raise HTTPException(422, "Assign employees from this site")
        runtime.store.put_zones(identifier, [z.model_dump(mode="json") for z in body.zones])
        audit(db, user, "camera.zones_updated", identifier, {"zones": len(body.zones)})
        db.commit()
        return {"ok": True}

    @app.get("/api/v1/local/daily-profiles")
    def daily_profiles(
        site_id: str, runtime=Depends(local), user=Depends(current_user), db=Depends(db_dependency)
    ):
        require_role(user, "admin", "hr")
        site_for(db, user, site_id)
        active = set(
            db.scalars(
                select(Employee.id).where(
                    Employee.company_id == user.company_id,
                    Employee.site_id == site_id,
                    Employee.active.is_(True),
                    Employee.sample.is_(False),
                )
            ).all()
        )
        return [
            {
                **{key: value for key, value in p.items() if key != "vectors"},
                "signature_count": len(p["vectors"]),
            }
            for p in runtime.gallery.profiles(site_id, time.time())
            if p["employee_id"] in active
        ]

    @app.delete("/api/v1/local/daily-profiles/{employee_id}")
    def reset_profile(
        employee_id: str,
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        require_role(user, "admin", "hr")
        employee = employee_for(db, user, employee_id)
        runtime.gallery.remove(employee.site_id, employee.id)
        audit(db, user, "identity.daily_reset", employee.id)
        db.commit()
        return {"ok": True}

    @app.get("/api/v1/local/identity-reviews")
    def identity_reviews(
        site_id: str,
        day: date,
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        require_role(user, "admin", "hr")
        site = site_for(db, user, site_id)
        from workledger.domain import business_date

        events = db.scalars(
            select(SourceEvent)
            .where(
                SourceEvent.company_id == user.company_id,
                SourceEvent.site_id == site_id,
                SourceEvent.identity_status != "confirmed",
            )
            .order_by(SourceEvent.occurred_at.desc())
        ).all()
        results = []
        for event in events:
            if business_date(event.occurred_at, site.timezone, site.shift_start) != day:
                continue
            review = db.get(EventReview, event.id)
            camera = db.get(Camera, event.camera_id)
            results.append(
                {
                    "id": event.id,
                    "camera_id": event.camera_id,
                    "camera_name": camera.name if camera else "Camera",
                    "kind": event.kind,
                    "occurred_at": event.occurred_at,
                    "employee_id": review.employee_id if review else None,
                    "outcome": review.outcome if review else "pending",
                    "reason": review.reason if review else "",
                    "visitor_name": review.visitor_name if review else "",
                }
            )
        return results[:1000]

    @app.post("/api/v1/local/employees/register", status_code=201)
    def register_employee(
        profile: str = Form(...),
        image: UploadFile = File(...),
        runtime=Depends(local),
        user=Depends(current_user),
        db=Depends(db_dependency),
    ):
        require_role(user, "admin", "hr")
        try:
            body = EmployeeInput.model_validate_json(profile)
        except ValueError:
            raise HTTPException(
                422, "Check employee details, unique ID, salary and joining date"
            ) from None
        site_for(db, user, body.site_id)
        if user.role != "admin" and (body.monthly_salary or body.overtime_rate):
            raise HTTPException(403, "Administrator access required to set salary")
        if db.scalar(
            select(Employee.id).where(
                Employee.company_id == user.company_id, Employee.code == body.code
            )
        ):
            raise HTTPException(409, "Employee ID is already in use")
        data = image.file.read(5_000_001)
        if len(data) > 5_000_000:
            raise HTTPException(413, "Image must be under 5 MB")
        frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if frame is None or max(frame.shape[:2]) > 4096:
            raise HTTPException(422, "Provide a valid face photo up to 4096 pixels per side")
        try:
            face = runtime.load_model().enrollment(frame)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        employee = Employee(
            company_id=user.company_id,
            **body.model_dump(mode="python", exclude={"site_id"}),
            site_id=str(body.site_id),
        )
        db.add(employee)
        db.flush()
        try:
            runtime.store.add_template(
                str(uuid4()),
                user.company_id,
                employee.id,
                "uncovered",
                face["vector"].tolist(),
                enrollment_metadata(frame, face, image.filename, data),
            )
            audit(
                db,
                user,
                "employee.enrolled",
                employee.id,
                {"code": employee.code, "nickname": employee.nickname},
            )
            db.commit()
        except Exception:
            runtime.store.remove_templates(user.company_id, employee.id)
            raise
        runtime.templates_cache.clear()
        gallery = runtime.refresh_gallery(user.company_id, employee.site_id)
        return {
            "gallery": gallery,
            "id": employee.id,
            "quality": round(face["quality"], 1),
            "enrollment_status": "enrolled",
        }
