import logging
import threading
import time
from collections import Counter, deque
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
from sqlalchemy import select

from workledger.domain import digest
from workledger.models import Camera, Employee, Site, SourceEvent

from .appearance import DailyGallery, clothing_colors, day_window
from .capture import Decoder
from .downloads import missing
from .face_engine import MODEL_REVISION, model_identity
from .flow import Flow
from .gallery_index import GalleryIndex
from .inference import Inference
from .presentation import detection_overlay
from .preview import PreviewTracker
from .store import LocalStore
from .tracking import Tracker, portal_ready
from .zones import during_window, membership

logger = logging.getLogger(__name__)

METRICS_WINDOW_SECONDS = 5
RECOGNITION_COUNTERS = (
    "no_face",
    "too_small",
    "too_blurry",
    "invalid_boundary",
    "poor_pose",
    "below_threshold",
    "ambiguous_match",
    "confirmation_reset",
)


class VisionRuntime:
    def __init__(self, settings, factory):
        self.settings, self.factory = settings, factory
        self.model_id = (
            model_identity(settings.model_dir)
            if (Path(settings.model_dir) / "adaface_ir50_ms1mv2.onnx").exists()
            else MODEL_REVISION
        )
        self.store = LocalStore(settings.local_data_dir, model_id=self.model_id)
        self.gallery_index = GalleryIndex(
            settings.local_data_dir, self.model_id, settings, factory=factory
        )
        self.gallery_errors = {}
        self.gallery_build_lock = threading.RLock()
        self.lock = threading.RLock()
        self.model_lock = threading.Lock()
        self.model = None
        self.model_state = "not_loaded"
        self.model_message = "Install the models to enable local inference"
        self.workers, self.states, self.frames, self.trackers = {}, {}, {}, {}
        self.overlays = {}
        self.preview_trackers = {}
        self.configs = {}
        self.processed, self.last_inference = {}, {}
        self.refresh_times = {}
        self.recognition_counts = {}
        self.gallery = DailyGallery(self.store)
        self.flows = {}
        self.last_daily_purge = 0
        self.stop = threading.Event()
        self.boot = str(uuid4())
        self.sequence = 0
        self.templates_cache = {}
        self.last_observation = {}
        self.last_retention = 0
        self.thread = threading.Thread(target=self.loop, name="local-vision", daemon=True)

    def start(self):
        self.thread.start()
        if not missing(self.settings.model_dir):
            threading.Thread(target=self.load_model, daemon=True).start()
        for identifier in self.store.camera_ids():
            config = self.store.camera(identifier)
            if config.get("enabled"):
                try:
                    self.connect(identifier)
                except ValueError:
                    pass

    def preview_loop(self):
        """Fast geometry worker, independent of model locks and browser polling."""
        while not self.stop.is_set():
            began = time.monotonic()
            with self.lock:
                previews = list(self.preview_trackers.items())
            for identifier, preview in previews:
                if self.stop.is_set():
                    break
                try:
                    # Same lock order as the frame route: preview then runtime.
                    with preview.lock:
                        with self.lock:
                            if self.preview_trackers.get(identifier) is not preview:
                                continue
                            frame = self.latest_frame(identifier)
                            overlay = self.overlays.get(identifier)
                        if not frame or time.time() - frame["at"] > 2:
                            continue
                        result = preview.render(frame, overlay)
                    with self.lock:
                        if self.preview_trackers.get(identifier) is preview:
                            self.states.setdefault(identifier, {}).update(
                                visual_tracking_at=frame["at"],
                                visual_tracking_ms=result["tracking_ms"],
                                visual_people=result["people"],
                                visual_tracking_error=False,
                            )
                except Exception:
                    with self.lock:
                        self.states.setdefault(identifier, {}).update(visual_tracking_error=True)
            self.stop.wait(
                max(0.005, 1 / self.settings.visual_tracking_fps - (time.monotonic() - began))
            )

    def load_model(self):
        with self.model_lock:
            if self.model is not None:
                return self.model
            self.model_state = "loading"
            try:
                self.model = Inference(self.settings)
                self.model_state, self.model_message = "ready", "CPU models loaded"
            except Exception:
                self.model_state, self.model_message = (
                    "error",
                    "Model loading failed. Run the model self-test; check downloaded files and CPU runtime.",
                )
                logger.warning("Local inference model loading failed; run model self-test")
                raise ValueError(self.model_message) from None
            return self.model

    def connect(self, identifier):
        config = self.store.camera(identifier)
        if not config or not config.get("path"):
            raise ValueError("Discover the camera stream or provide its RTSP path first")
        with self.factory() as db:
            camera = db.get(Camera, identifier)
            if not camera:
                raise ValueError("Camera metadata was removed")
            site = db.get(Site, camera.site_id)
            config.update(
                role=camera.role,
                target_fps=camera.target_fps,
                company_id=camera.company_id,
                site_id=camera.site_id,
                timezone=site.timezone,
                live_fps=self.settings.live_fps,
                decode_threads=self.settings.decode_threads,
            )
        self.disconnect(identifier, disable=False)
        with self.lock:
            if len(self.workers) >= self.settings.max_active_cameras:
                raise ValueError("Active camera limit reached; stop another camera first")
            self.workers[identifier] = Decoder(config)
            self.configs[identifier] = config
            self.trackers[identifier] = Tracker(
                confirmations=self.settings.confirmation_frames, settings=self.settings
            )
            self.flows[identifier] = Flow()
            self.preview_trackers[identifier] = PreviewTracker()
            self.refresh_times[identifier] = deque()
            self.recognition_counts[identifier] = Counter(
                {name: 0 for name in RECOGNITION_COUNTERS}
            )
            self.states[identifier] = {"state": "connecting", "message": "Opening RTSP over TCP"}
            config["enabled"] = True
            self.store.put_camera(identifier, config)

    def disconnect(self, identifier, disable=True):
        with self.lock:
            decoder = self.workers.pop(identifier, None)
            if decoder:
                decoder.close()
            self.states[identifier] = {"state": "stopped"}
            self.frames.pop(identifier, None)
            self.overlays.pop(identifier, None)
            self.preview_trackers.pop(identifier, None)
            self.configs.pop(identifier, None)
            self.trackers.pop(identifier, None)
            self.flows.pop(identifier, None)
            self.processed.pop(identifier, None)
            self.last_inference.pop(identifier, None)
            self.refresh_times.pop(identifier, None)
            self.recognition_counts.pop(identifier, None)
            if disable:
                config = self.store.camera(identifier)
                if config:
                    config["enabled"] = False
                    self.store.put_camera(identifier, config)

    def shutdown(self):
        self.stop.set()
        self.thread.join(timeout=5)
        for identifier in list(self.workers):
            self.disconnect(identifier, disable=False)

    def latest_frame(self, identifier):
        """Drain capture independently of inference; callers share the runtime lock."""
        with self.lock:
            decoder = self.workers.get(identifier)
            message = decoder.poll() if decoder is not None else None
            if message:
                state = self.states.setdefault(identifier, {})
                state.update(
                    state=message["state"],
                    message=message.get("message", "Stream receiving"),
                )
                if "jpeg" in message:
                    state.update(
                        last_frame=message["at"],
                        decoded_fps=message.get("decoded_fps", 0),
                        source_width=message.get("source_width"),
                        source_height=message.get("source_height"),
                        source_fps=message.get("source_fps"),
                    )
                    self.frames[identifier] = {"jpeg": message["jpeg"], "at": message["at"]}
            return self.frames.get(identifier)

    def state(self, identifier):
        with self.lock:
            result = dict(
                self.states.get(
                    identifier,
                    {"state": "not_configured" if not self.store.camera(identifier) else "stopped"},
                )
            )
            if result.get("state") == "online" and time.time() - result.get("last_frame", 0) > 8:
                result["state"] = "stale"
            refreshes = self.refresh_times.get(identifier)
            if refreshes is not None:
                cutoff = time.monotonic() - METRICS_WINDOW_SECONDS
                while refreshes and refreshes[0] <= cutoff:
                    refreshes.popleft()
                result["full_refresh_fps"] = round(len(refreshes) / METRICS_WINDOW_SECONDS, 1)
                result["recognition_counts"] = dict(self.recognition_counts[identifier])
            if result["state"] != "online":
                result["decoded_fps"] = 0
                result["full_refresh_fps"] = 0
                result.pop("frame_age_ms", None)
            return result

    def templates(self, company, site):
        with self.gallery_build_lock:
            return self._templates(company, site)

    def _templates(self, company, site):
        cached = self.templates_cache.get((company, site))
        if cached is None or time.monotonic() - cached[0] > 5:
            with self.factory() as db:
                active = {
                    employee.id: employee.nickname or employee.name
                    for employee in db.scalars(
                        select(Employee).where(
                            Employee.company_id == company,
                            Employee.site_id == site,
                            Employee.active.is_(True),
                            Employee.sample.is_(False),
                        )
                    ).all()
                }
            cached = (
                time.monotonic(),
                [
                    {**t, "name": active[t["employee_id"]]}
                    for t in self.store.templates(company)
                    if t["employee_id"] in active
                ],
            )
            self.gallery_index.update(company, site, cached[1])
            self.templates_cache[(company, site)] = cached
        return cached[1]

    def refresh_gallery(self, company, site):
        with self.gallery_build_lock:
            return self._refresh_gallery(company, site)

    def _refresh_gallery(self, company, site):
        with self.lock:
            self.templates_cache.pop((company, site), None)
            self.gallery_index.invalidate(company, site)
            for camera, config in self.configs.items():
                if config["company_id"] == company and config["site_id"] == site:
                    self.trackers[camera] = Tracker(
                        confirmations=self.settings.confirmation_frames, settings=self.settings
                    )
                    self.flows[camera] = Flow()
                    self.overlays.pop(camera, None)
                    self.preview_trackers[camera] = PreviewTracker()
        try:
            self.templates(company, site)
            self.gallery_errors.pop((company, site), None)
            entry = self.gallery_index.entries[(company, site)]
            return {key: entry[key] for key in ("version", "model_id", "samples", "embeddings")}
        except (OSError, ValueError) as error:
            self.gallery_errors[(company, site)] = type(error).__name__
            return {"status": "pending_rebuild", "error": "Gallery export failed; retry rebuild."}

    def write_event(self, identifier, config, track, captured, jpeg=None, force_unknown=False):
        employee = None if force_unknown else track.employee_id
        with self.factory() as db:
            if employee:
                active = db.scalar(
                    select(Employee.id).where(
                        Employee.id == employee,
                        Employee.company_id == config["company_id"],
                        Employee.site_id == config["site_id"],
                        Employee.active.is_(True),
                    )
                )
                if not active:
                    return False
                latest = db.scalar(
                    select(SourceEvent)
                    .where(
                        SourceEvent.company_id == config["company_id"],
                        SourceEvent.site_id == config["site_id"],
                        SourceEvent.employee_id == employee,
                        SourceEvent.identity_status == "confirmed",
                    )
                    .order_by(SourceEvent.occurred_at.desc())
                    .limit(1)
                )
                if (
                    latest
                    and latest.kind == config["role"]
                    and captured - latest.occurred_at.timestamp() < 3600
                ):
                    return False
            self.sequence += 1
            event_id = str(uuid4())
            payload = {
                "id": event_id,
                "employee": employee,
                "camera": identifier,
                "at": captured,
                "score": track.score,
            }
            db.add(
                SourceEvent(
                    id=event_id,
                    company_id=config["company_id"],
                    site_id=config["site_id"],
                    agent_id=None,
                    camera_id=identifier,
                    employee_id=employee,
                    kind=config["role"],
                    identity_status="confirmed" if employee else "unknown",
                    occurred_at=datetime.fromtimestamp(captured, UTC),
                    boot_id=self.boot,
                    sequence=self.sequence,
                    fingerprint=digest(payload),
                    simulated=False,
                )
            )
            db.commit()
        if jpeg:
            try:
                self.store.put_evidence(event_id, config["company_id"], identifier, captured, jpeg)
            except Exception:
                logger.warning("Attendance saved; local event evidence could not be stored")
        body = track.latest.get("body")
        if body is not None:
            _, expires = day_window(captured, config.get("timezone", "Asia/Dhaka"))
            self.store.put_context(
                event_id,
                expires,
                {
                    "body": body.tolist(),
                    "at": captured,
                    "clothing": track.latest.get("clothing", {}),
                },
            )
        return True

    def loop(self):
        while not self.stop.wait(0.015):
            if time.time() - self.last_daily_purge > 1:
                current = time.time()
                self.store.purge_daily(current)
                # Also remove expired identities from live boxes at midnight.
                for camera_id, config in list(self.configs.items()):
                    day, _ = day_window(current, config.get("timezone", "Asia/Dhaka"))
                    if config.get("identity_day") not in {None, day}:
                        self.trackers[camera_id] = Tracker(
                            confirmations=self.settings.confirmation_frames, settings=self.settings
                        )
                        self.flows[camera_id] = Flow()
                        self.overlays.pop(camera_id, None)
                        self.preview_trackers[camera_id] = PreviewTracker()
                    config["identity_day"] = day
                self.last_daily_purge = current
            if time.time() - self.last_retention > 3600:
                try:
                    with self.factory() as db:
                        for site in db.scalars(select(Site)).all():
                            camera_ids = db.scalars(
                                select(Camera.id).where(
                                    Camera.site_id == site.id, Camera.company_id == site.company_id
                                )
                            ).all()
                            self.store.purge_evidence(
                                camera_ids, time.time() - site.retention_days * 86400
                            )
                            self.store.purge_observations(
                                camera_ids, time.time() - site.retention_days * 86400
                            )
                    self.last_retention = time.time()
                except Exception:
                    self.last_retention = time.time()
            with self.lock:
                identifiers = sorted(
                    self.workers,
                    key=lambda identifier: (
                        0 if self.configs[identifier]["role"] in {"entry", "exit"} else 1,
                        self.last_inference.get(identifier, 0),
                    ),
                )
            for identifier in identifiers:
                if self.stop.is_set():
                    break
                try:
                    with self.lock:
                        decoder = self.workers.get(identifier)
                        if decoder is None:
                            continue
                        frame = self.latest_frame(identifier)
                        config = self.configs.get(identifier)
                        active_tracker = self.trackers.get(identifier)
                    if not decoder.process.is_alive():
                        self.states[identifier] = {
                            "state": "error",
                            "message": "Decoder stopped. Reconnect this camera.",
                        }
                    if (
                        not frame
                        or not config
                        or self.model is None
                        or time.time() - frame["at"] > 3
                    ):
                        continue
                    if frame["at"] == self.processed.get(identifier):
                        continue
                    full_refresh = time.monotonic() - self.last_inference.get(
                        identifier, 0
                    ) >= 1 / min(
                        config["target_fps"],
                        self.settings.portal_inference_fps
                        if config["role"] in {"entry", "exit"}
                        else self.settings.inference_fps,
                    )
                    if full_refresh:
                        # Schedule from the start, so model execution does not
                        # add another delay to every confirmation observation.
                        self.last_inference[identifier] = time.monotonic()
                    self.processed[identifier] = frame["at"]
                    began = time.perf_counter()
                    image = cv2.imdecode(np.frombuffer(frame["jpeg"], np.uint8), cv2.IMREAD_COLOR)
                    frame_counts = Counter()
                    templates = self.templates(config["company_id"], config["site_id"])
                    names = {
                        sample["employee_id"]: sample.get("name", "Unknown") for sample in templates
                    }

                    def publish_progress(detected_people, stage):
                        snapshot = detection_overlay(
                            {**frame, "shape": image.shape},
                            detected_people,
                            active_tracker,
                            names,
                            stage,
                        )
                        with self.lock:
                            if (
                                self.trackers.get(identifier) is not active_tracker
                                or self.workers.get(identifier) is not decoder
                            ):
                                return
                            snapshot["geometry"] = self.overlays.get(identifier, {}).get(
                                "geometry", {}
                            )
                            self.overlays[identifier] = snapshot

                    people = (
                        self.model.analyze(
                            image,
                            templates,
                            frame_counts,
                            tracker=active_tracker,
                            on_progress=publish_progress,
                            at=frame["at"],
                            role=config["role"],
                            matcher=self.gallery_index.entries[
                                (config["company_id"], config["site_id"])
                            ]["matcher"],
                        )
                        if full_refresh
                        else []
                    )
                    refreshed_at = time.monotonic()
                    for person in people:
                        if (
                            not person.get("employee_id")
                            and not person.get("identity_conflict")
                            and person.get("body") is not None
                            and person.get("face_vector") is None
                        ):
                            employee, score = self.gallery.match(
                                config["site_id"],
                                person["body"],
                                frame["at"],
                                self.settings.reid_threshold,
                                self.settings.reid_margin,
                            )
                            if employee:
                                person.update(
                                    employee_id=employee, score=score, method="body_tentative"
                                )
                    claimed = {}
                    for person in people:
                        if person.get("employee_id"):
                            claimed.setdefault(person["employee_id"], []).append(person)
                    for matches in claimed.values():
                        if len(matches) > 1:
                            face_matches = [
                                person for person in matches if person.get("method") == "face"
                            ]
                            for person in matches:
                                if len(face_matches) != 1 or person is not face_matches[0]:
                                    person.update(
                                        employee_id=None, method="unknown", identity_conflict=True
                                    )
                    with self.lock:
                        tracker = self.trackers.get(identifier)
                        if (
                            tracker is None
                            or tracker is not active_tracker
                            or self.workers.get(identifier) is not decoder
                        ):
                            continue
                        if full_refresh:
                            previous_face_hits = {
                                track.id: track.hits
                                for track in tracker.tracks.values()
                                if track.method == "face"
                                and track.hits > 0
                                and frame["at"] - track.seen < self.settings.track_lost_seconds
                            }
                            tracks = tracker.update(people, frame["at"])
                            frame_counts["confirmation_reset"] += sum(
                                track.id in previous_face_hits
                                and (
                                    track.method != "face"
                                    or track.hits <= previous_face_hits[track.id]
                                )
                                for track in tracks
                            )
                            refreshes = self.refresh_times[identifier]
                            refreshes.append(refreshed_at)
                            cutoff = refreshed_at - METRICS_WINDOW_SECONDS
                            while refreshes and refreshes[0] <= cutoff:
                                refreshes.popleft()
                            self.recognition_counts[identifier].update(frame_counts)
                            self.flows[identifier].prime(image, tracks, frame["at"])
                        else:
                            tracks = self.flows[identifier].update(image, tracker, frame["at"])
                    for track in tracks:
                        if (
                            full_refresh
                            and track.employee_id
                            and track.method == "face"
                            and track.hits >= self.settings.confirmation_frames
                        ):
                            if track.latest.get("body") is not None and track.latest.get(
                                "body_fresh", True
                            ):
                                track.latest["clothing"] = clothing_colors(image, track.box)
                                with self.factory() as db:
                                    employee = db.get(Employee, track.employee_id)
                                    details = {
                                        "name": employee.name,
                                        "code": employee.code,
                                        "nickname": employee.nickname,
                                        "clothing": track.latest["clothing"],
                                        "source": "face",
                                    }
                                self.gallery.add(
                                    config["site_id"],
                                    track.employee_id,
                                    identifier,
                                    track.latest["body"],
                                    frame["at"],
                                    config.get("timezone", "Asia/Dhaka"),
                                    details,
                                )
                        # Attendance never accepts a body-only similarity.
                        if full_refresh and config["role"] in {"entry", "exit"}:
                            ready = portal_ready(
                                track,
                                config,
                                image.shape,
                                frame["at"],
                                self.settings.confirmation_frames,
                            )
                            if (
                                ready
                                and track.is_locked
                                and (
                                    track.crossing_at > track.crossing_emitted_at
                                    if config.get("trigger", "presence") == "line"
                                    else not track.emitted
                                )
                            ):
                                self.write_event(
                                    identifier, config, track, frame["at"], frame["jpeg"]
                                )
                                track.emitted = True
                                track.crossing_emitted_at = track.crossing_at
                        if full_refresh and (
                            (
                                not track.employee_id
                                or config["role"] in {"entry", "exit"}
                                and not track.is_locked
                            )
                            and track.observations >= 8
                            and not track.unknown_emitted
                            and (
                                config["role"] == "interior"
                                and track.latest.get("face_vector") is not None
                                or config["role"] != "interior"
                                and (
                                    config.get("trigger", "presence") == "presence"
                                    or track.crossing_at
                                )
                            )
                        ):
                            track.latest["clothing"] = clothing_colors(image, track.box)
                            self.write_event(
                                identifier,
                                config,
                                track,
                                frame["at"],
                                frame["jpeg"],
                                force_unknown=True,
                            )
                            track.unknown_emitted = True
                        if (
                            config["role"] == "interior"
                            and full_refresh
                            and track.employee_id
                            and track.is_locked
                            and track.hits >= self.settings.confirmation_frames
                            and track.association_reliable
                            and not track.needs_reverification
                        ):
                            key = (identifier, track.employee_id)
                            if frame["at"] - self.last_observation.get(key, 0) >= 1:
                                zone = membership(
                                    track.box,
                                    image.shape,
                                    self.store.zones(identifier),
                                    track.employee_id,
                                )
                                self.store.observe(
                                    track.employee_id,
                                    identifier,
                                    frame["at"],
                                    zone["name"],
                                    "face" if track.method == "face" else "tracking",
                                    track.score,
                                    zone["counts_as_work"]
                                    and during_window(
                                        zone, frame["at"], config.get("timezone", "Asia/Dhaka")
                                    ),
                                    zone["kind"]
                                    if during_window(
                                        zone, frame["at"], config.get("timezone", "Asia/Dhaka")
                                    )
                                    else "outside_allowed_time",
                                )
                                self.last_observation[key] = frame["at"]
                        face_box = track.latest.get("face_box")
                        if face_box is None:
                            continue
                        display_identity = (
                            track.display_employee_id
                            if track.association_reliable and frame["at"] <= track.display_until
                            else None
                        )
                        name = next(
                            (
                                s.get("name", s["employee_id"][:8])
                                for s in self.templates(config["company_id"], config["site_id"])
                                if s["employee_id"] == display_identity
                            ),
                            "Unknown",
                        )
                        reason = track.latest.get("face_preview_reason")
                        if reason and display_identity is None:
                            name = "Unknown"
                        elif not full_refresh and name != "Unknown":
                            name += " (tracking)"
                        # The browser draws Unicode labels at readable CSS size.
                        track.latest["display_name"] = name
                    if not full_refresh:
                        self.states[identifier]["tracking_ms"] = round(
                            (time.perf_counter() - began) * 1000, 1
                        )
                        continue
                    employee_names = {
                        sample["employee_id"]: sample.get("name", "Unknown")
                        for sample in self.templates(config["company_id"], config["site_id"])
                    }
                    with self.lock:
                        if (
                            identifier not in self.workers
                            or self.trackers.get(identifier) is not active_tracker
                        ):
                            continue
                        h, w = image.shape[:2]
                        labels = [
                            {
                                "track_id": t.id,
                                "transition_reason": t.transition_reason,
                                "name": t.latest.get("display_name", "Unknown")[:32],
                                "x": max(0, min(0.95, t.latest["face_box"][0] / w)),
                                "y": max(0, min(0.95, t.latest["face_box"][1] / h)),
                                "source_width": w,
                                "source_height": h,
                                "width": max(
                                    0, (t.latest["face_box"][2] - t.latest["face_box"][0]) / w
                                ),
                                "height": max(
                                    0, (t.latest["face_box"][3] - t.latest["face_box"][1]) / h
                                ),
                                "confirmed": t.is_locked and t.association_reliable,
                                "locked": t.is_locked and t.association_reliable,
                                "employee_id": t.locked_employee_id
                                if t.association_reliable
                                else None,
                                "identity_status": t.identity_state,
                                "latest_match_score": round(t.last_face_score, 3),
                                "last_verified_age_ms": round(
                                    max(0, frame["at"] - t.verified_at) * 1000
                                )
                                if t.is_locked
                                else None,
                                "identity_expires_at": 0 if t.is_locked else t.display_until,
                                "method": t.method,
                                "identity_state": (
                                    "tracking"
                                    if t.display_employee_id
                                    and frame["at"] <= t.display_until
                                    and not (
                                        t.method == "face"
                                        and t.hits >= self.settings.confirmation_frames
                                    )
                                    else "current"
                                ),
                                "quality_reason": t.latest.get("face_preview_reason"),
                                "score": round(
                                    t.display_score
                                    if t.display_employee_id and frame["at"] <= t.display_until
                                    else t.score,
                                    3,
                                ),
                                "detection_score": round(t.latest.get("detection_score", 0), 3),
                            }
                            for t in [track for track in tracks if track.latest.get("face_box")][
                                :30
                            ]
                        ]
                        self.overlays[identifier] = {
                            "source_jpeg": frame["jpeg"],
                            "at": frame["at"],
                            "revision": time.monotonic_ns(),
                            "stage": "complete",
                            "labels": labels,
                            "persons": [
                                {
                                    "id": track.id,
                                    "box": list(track.box),
                                    "face_box": track.latest.get(
                                        "preview_face_box", track.latest.get("face_box")
                                    ),
                                    "label": next(
                                        (
                                            label
                                            for label in labels
                                            if label["track_id"] == track.id
                                        ),
                                        {
                                            "track_id": track.id,
                                            "transition_reason": track.transition_reason,
                                            "name": employee_names.get(
                                                track.display_employee_id, "Unknown"
                                            )
                                            if track.association_reliable
                                            and frame["at"] <= track.display_until
                                            else "Unknown",
                                            "identity_state": "tracking",
                                            "identity_expires_at": 0
                                            if track.is_locked
                                            else track.display_until,
                                            "confirmed": track.is_locked
                                            and track.association_reliable,
                                            "locked": track.is_locked
                                            and track.association_reliable,
                                            "identity_status": track.identity_state,
                                            "employee_id": track.locked_employee_id
                                            if track.association_reliable
                                            else None,
                                            "score": track.display_score,
                                            "method": track.method,
                                        },
                                    ),
                                }
                                for track in tracks
                            ],
                            "geometry": {
                                "zones": [
                                    {"name": zone["name"], "points": zone["points"]}
                                    for zone in self.store.zones(identifier)
                                ],
                                "line": {
                                    "axis": config.get("line_axis", "vertical"),
                                    "position": config.get("line_position", 0.5),
                                }
                                if config.get("trigger") == "line"
                                else None,
                            },
                        }
                        elapsed_ms = round((time.perf_counter() - began) * 1000, 1)
                        self.states[identifier].update(
                            inference_error=False,
                            **(
                                {
                                    "inference_ms": elapsed_ms,
                                    "model_stages_ms": self.model.timings,
                                    "detected_people": len(tracks),
                                    "people_count_at": frame["at"],
                                }
                                if full_refresh
                                else {"tracking_ms": elapsed_ms}
                            ),
                            processing_mode="model" if full_refresh else "tracking",
                            frame_age_ms=round((time.time() - frame["at"]) * 1000, 1),
                            people=len(tracks),
                            recognized=sum(t.is_locked and t.association_reliable for t in tracks),
                            last_inference=frame["at"],
                        )
                    # Reconsider due portal cameras after every inference, with
                    # oldest-first fairness among workplace cameras.
                    if full_refresh:
                        break
                except Exception:
                    with self.lock:
                        self.states.setdefault(identifier, {}).update(
                            message="Inference failed for this frame; see local model self-test",
                            inference_error=True,
                        )
                    # Avoid raw decoder exceptions or config data appearing in logs.
                    self.stop.wait(0.1)
