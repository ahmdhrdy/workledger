import base64
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from .downloads import PERSON_ONNX, REID, missing
from .face_engine import FaceEngine
from .person_detector import PersonDetector
from .pose_matcher import PoseAdaptiveMatcher


def normalized(vector):
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = np.linalg.norm(vector)
    if not np.isfinite(norm) or norm < 1e-8:
        raise ValueError("Invalid biometric feature vector")
    return vector / norm


def rank(vector, templates, threshold, margin, counts=None):
    scores = {}
    vector = normalized(vector)
    for sample in templates:
        score = float(np.dot(vector, normalized(sample["vector"])))
        employee = sample["employee_id"]
        scores[employee] = max(scores.get(employee, -1), score)
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if not ordered:
        return None, 0.0
    best, score = ordered[0]
    runner_up = ordered[1][1] if len(ordered) > 1 else -1
    if score < threshold:
        if counts is not None:
            counts["below_threshold"] += 1
        return None, score
    if score - runner_up < margin:
        if counts is not None:
            counts["ambiguous_match"] += 1
        return None, score
    return best, score


class Inference:
    def __init__(self, settings):
        absent = missing(settings.model_dir)
        if absent:
            raise ValueError("Missing local models: " + ", ".join(absent))
        self.settings = settings
        cv2.setNumThreads(settings.inference_threads)
        self.lock = threading.RLock()
        self.engine = FaceEngine(settings)
        root = Path(settings.model_dir)
        import openvino as ov

        options = {
            "INFERENCE_NUM_THREADS": settings.inference_threads,
            "NUM_STREAMS": 1,
            "PERFORMANCE_HINT": "LATENCY",
        }
        core = ov.Core()
        self.person_detector = PersonDetector(
            root / PERSON_ONNX, settings.inference_threads, settings.person_detection_threshold
        )
        self.reid_model = core.compile_model(str(root / (REID + ".xml")), "CPU", options)
        self.timings = {}

    @staticmethod
    def _reject(error, box, confidence, counts, rejected):
        message = str(error)
        key = "invalid_boundary"
        if "too small" in message:
            key = "too_small"
        elif "blurry" in message:
            key = "too_blurry"
        elif "alignment" in message or "landmarks" in message:
            key = "poor_pose"
        if counts is not None:
            counts[key] += 1
        if rejected is not None:
            rejected.append({"box": list(box), "reason": message, "detection_score": confidence})

    def faces(self, frame, counts=None, rejected=None, min_pixels=None, detection_threshold=None):
        minimum = self.settings.face_min_pixels if min_pixels is None else min_pixels
        with self.lock:
            detected = self.engine.detect_faces(frame, detection_threshold)
            if not detected and counts is not None:
                counts["no_face"] += 1
            faces = []
            for box, confidence, kps in detected:
                try:
                    aligned, quality = self.engine.preprocess_and_align(frame, box, kps, minimum)
                except ValueError as error:
                    self._reject(error, box, confidence, counts, rejected)
                    continue
                faces.append(
                    {
                        **quality,
                        "box": box.tolist(),
                        "detection_score": confidence,
                        "vector": self.engine.get_embedding(aligned),
                        "aligned": aligned,
                    }
                )
            return faces

    def enrollment(self, frame, augment_masked=False):
        rejected = []
        faces = self.faces(
            frame,
            rejected=rejected,
            min_pixels=self.settings.enrollment_face_min_pixels,
            detection_threshold=self.settings.enrollment_detection_threshold,
        )
        count = len(faces) + len(rejected)
        if count == 0:
            raise ValueError(
                f"No face detected at {self.settings.enrollment_detection_threshold:.0%} enrollment confidence."
            )
        if count != 1:
            raise ValueError(f"Found {count} faces. Provide a photo containing exactly one person.")
        if rejected:
            raise ValueError(f"Enrollment rejected: {rejected[0]['reason']}")
        face = faces[0]
        variants = []
        for kind, crop in self.engine.augmentation_crops(face["aligned"], augment_masked):
            okay, jpeg = cv2.imencode(".jpg", crop)
            if not okay:
                raise ValueError("Unable to create augmentation preview")
            variants.append(
                {
                    "kind": kind,
                    "vector": self.engine.get_embedding(crop).tolist(),
                    "thumbnail": base64.b64encode(jpeg.tobytes()).decode(),
                }
            )
        face["augmentations"] = variants
        return face

    def _people(self, frame):
        people = []
        for box, confidence in self.person_detector.detect(frame):
            people.append(
                {
                    "box": box,
                    "body": None,
                    "person_detected": True,
                    "person_score": confidence,
                    "employee_id": None,
                    "score": 0.0,
                    "method": "unknown",
                    "face_vector": None,
                }
            )
        return people

    def _body_embedding(self, frame, person):
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = np.asarray(person["box"]).astype(int).clip([0, 0, 0, 0], [w, h, w, h])
        if x2 <= x1 or y2 <= y1:
            return
        crop = cv2.resize(frame[y1:y2, x1:x2], (128, 256))
        blob = crop.transpose(2, 0, 1)[None].astype(np.float32)
        person.update(
            body=normalized(self.reid_model([blob])[self.reid_model.output(0)]), body_fresh=True
        )

    def analyze(
        self,
        frame,
        templates,
        counts=None,
        tracker=None,
        at=None,
        role="interior",
        matcher=None,
        association_hints=None,
        on_progress=None,
    ):
        with self.lock:
            began = time.perf_counter()
            people = self._people(frame)
            body_done = time.perf_counter()
            detected = self.engine.detect_faces(frame)
            if not detected and counts is not None:
                counts["no_face"] += 1
            for box, confidence, kps in detected:
                cx, cy = (box[:2] + box[2:]) / 2
                candidates = [
                    person
                    for person in people
                    if person.get("face_box") is None
                    and person["box"][0] - (person["box"][2] - person["box"][0]) * 0.1
                    <= cx
                    <= person["box"][2] + (person["box"][2] - person["box"][0]) * 0.1
                    and person["box"][1] - (person["box"][3] - person["box"][1]) * 0.15
                    <= cy
                    <= person["box"][1] + (person["box"][3] - person["box"][1]) * 0.65
                ]
                if candidates:

                    def head_distance(p):
                        pb = p["box"]
                        return abs(cx - (pb[0] + pb[2]) / 2) / max(pb[2] - pb[0], 1) + abs(
                            cy - (pb[1] + (pb[3] - pb[1]) * 0.18)
                        ) / max(pb[3] - pb[1], 1)

                    candidates.sort(key=head_distance)
                    if (
                        len(candidates) > 1
                        and head_distance(candidates[1]) - head_distance(candidates[0]) < 0.12
                    ):
                        continue
                    person = candidates[0]
                else:
                    # A second/poorly located face inside an existing body must not
                    # become another counted person or borrow that body's identity.
                    if any(
                        p["box"][0] <= cx <= p["box"][2] and p["box"][1] <= cy <= p["box"][3]
                        for p in people
                    ):
                        continue
                    person = {
                        "box": box.tolist(),
                        "body": None,
                        "employee_id": None,
                        "method": "unknown",
                        "score": 0.0,
                        "face_vector": None,
                    }
                    people.append(person)
                person.update(face_box=box.tolist(), face_kps=kps, detection_score=confidence)
            if on_progress is not None:
                # Publish all detected face boxes before association can request
                # body features, or quality gates/embedding work can block them.
                on_progress(people, "detected")
            if tracker is not None:
                tracker.associate(
                    people,
                    at,
                    appearance=lambda p: self._body_embedding(frame, p),
                    hints=association_hints,
                )
            if matcher is None:
                ids, vectors = [], []
                for sample in templates:
                    ids.append(sample["employee_id"])
                    vectors.append(sample["vector"])
                    for variant in sample.get("augmentations", []):
                        ids.append(sample["employee_id"])
                        vectors.append(variant["vector"])
                matcher = PoseAdaptiveMatcher(
                    ids,
                    np.asarray(vectors, np.float32).reshape(-1, 512),
                    self.settings.face_threshold,
                    self.settings.face_margin,
                    self.settings.side_face_threshold,
                    self.settings.side_face_margin,
                )
            embedded, skipped = 0, 0
            for person in people:
                if "face_box" not in person:
                    continue
                track = tracker.tracks.get(person.get("_track_id")) if tracker else None
                interval = (
                    self.settings.portal_verify_seconds
                    if role in {"entry", "exit"}
                    else self.settings.face_verify_seconds
                )
                if (
                    track is not None
                    and track.is_locked
                    and track.hits >= self.settings.confirmation_frames
                    and track.locked_employee_id in matcher.employee_ids
                    and at - track.verified_at < interval
                    and not track.needs_reverification
                    and not person.get("_recovered")
                    and not person.get("_association_ambiguous")
                    and person.get("_association_reliable")
                ):
                    person.update(
                        employee_id=track.locked_employee_id,
                        method="face_cached",
                        score=track.display_score,
                        recognition_skipped=True,
                        attendance_eligible=False,
                    )
                    skipped += 1
                    continue
                try:
                    aligned, quality = self.engine.preprocess_and_align(
                        frame,
                        person["face_box"],
                        person["face_kps"],
                        self.settings.face_min_pixels,
                    )
                except ValueError as error:
                    self._reject(error, person["face_box"], person["detection_score"], counts, None)
                    person["face_preview_reason"] = str(error)
                    continue
                if not matcher.employee_ids:
                    person["face_preview_reason"] = "No AdaFace enrollment samples"
                    skipped += 1
                    continue
                if (
                    track is not None
                    and track.is_locked
                    and track.last_face_vector is not None
                    and track.last_face_identity is None
                    and at - track.face_attempt_at < self.settings.unknown_face_retry_seconds
                    and abs(quality["yaw_ratio"] - track.face_attempt_yaw) < 0.15
                    and quality["quality"] < track.face_attempt_quality * 1.5
                    and person.get("_association_reliable")
                    and not person.get("_association_ambiguous")
                ):
                    person.update(
                        score=track.last_face_score,
                        face_vector=track.last_face_vector,
                        recognition_skipped=True,
                        attendance_eligible=False,
                    )
                    skipped += 1
                    continue
                # Any quality-qualified angle may verify the same locked identity.
                # Switching identities still requires sustained strong frontal evidence.
                vector = self.engine.get_embedding(aligned)
                identity, score, gap = matcher.match(vector, quality["yaw_ratio"])
                embedded += 1
                if track is not None:
                    track.last_face_vector = vector
                    track.last_face_score = score
                    track.last_face_identity = identity
                    track.face_attempt_at = at
                    track.face_attempt_yaw = quality["yaw_ratio"]
                    track.face_attempt_quality = quality["quality"]
                if identity is None and counts is not None:
                    counts[
                        "below_threshold"
                        if gap >= self.settings.side_face_margin
                        else "ambiguous_match"
                    ] += 1
                person.update(
                    employee_id=identity,
                    method="face" if identity else "unknown",
                    score=score,
                    face_vector=vector,
                    face_preview_reason=None,
                    attendance_eligible=bool(
                        identity
                        and score >= self.settings.attendance_face_threshold
                        and gap >= self.settings.attendance_face_margin
                    ),
                    yaw_ratio=quality["yaw_ratio"],
                    nose_offset=quality.get("nose_offset", 0),
                    match_gap=gap,
                )
                if on_progress is not None:
                    on_progress(people, "matched")
            face_done = time.perf_counter()
            body_computed = 0
            # Sample workplace re-ID per stable track. Doorways extract body features
            # when needed for a confirmed identity seed or an unknown-event review.
            for person in people:
                if not person.get("person_detected"):
                    continue
                track = tracker.tracks.get(person.get("_track_id")) if tracker else None
                if person.get("body_fresh"):
                    body_computed += 1
                    if track is not None and person.get("_association_reliable"):
                        track.body_vector, track.body_at = person["body"], at
                    continue
                if role != "interior":
                    confirmed_seed = (
                        person.get("employee_id") is not None
                        and person.get("method") == "face"
                        and (track is None or track.hits >= self.settings.confirmation_frames - 1)
                    )
                    unknown_review = (
                        track is not None
                        and track.observations >= 7
                        and not track.unknown_emitted
                        and not person.get("employee_id")
                    )
                    if not (confirmed_seed or unknown_review):
                        continue
                if (
                    track is not None
                    and track.body_vector is not None
                    and person.get("employee_id") in (None, track.last_identity)
                    and at - track.body_at < self.settings.body_feature_seconds
                    and person.get("_association_reliable")
                    and not person.get("_association_ambiguous")
                ):
                    person["body"] = track.body_vector
                    person["body_fresh"] = False
                    continue
                x1, y1, x2, y2 = map(int, person["box"])
                crop = cv2.resize(frame[y1:y2, x1:x2], (128, 256))
                blob = crop.transpose(2, 0, 1)[None].astype(np.float32)
                body = normalized(self.reid_model([blob])[self.reid_model.output(0)])
                person.update(body=body, body_fresh=True)
                body_computed += 1
                if track is not None:
                    if person.get("_association_reliable") and not person.get(
                        "_association_ambiguous"
                    ):
                        track.body_vector, track.body_at = body, at
            body_features_done = time.perf_counter()
            claims = {}
            for person in people:
                if person.get("employee_id"):
                    claims.setdefault(person["employee_id"], []).append(person)
            for matches in claims.values():
                if len(matches) > 1:
                    for person in matches:
                        person.update(employee_id=None, method="unknown", identity_conflict=True)
                        if counts is not None:
                            counts["ambiguous_match"] += 1
            self.timings = {
                "person_detection_ms": round((body_done - began) * 1000, 1),
                "body_features_ms": round((body_features_done - face_done) * 1000, 1),
                "body_embeddings_computed": body_computed,
                "face_pipeline_ms": round((face_done - body_done) * 1000, 1),
                "embeddings_computed": embedded,
                "embeddings_skipped": skipped,
            }
            return people
