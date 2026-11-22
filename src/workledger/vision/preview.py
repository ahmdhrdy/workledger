"""Track preview geometry on displayed frames independently of neural inference."""

import threading
import time
from collections import deque

import cv2
import numpy as np

from .tracking import iou


class PreviewTracker:
    def __init__(self, max_age=5.0):
        self.lock = threading.RLock()
        self.history = deque(maxlen=180)
        self.snapshots = deque(maxlen=180)
        self.analysis_at = -1.0
        self.analysis_revision = -1
        self.at = -1.0
        self.gray = None
        self.entities = {}
        self.face_memory = {}
        self.max_age = max_age

    @staticmethod
    def _gray(jpeg):
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise ValueError("Invalid preview frame")
        height, width = image.shape
        return cv2.resize(image, (640, max(1, round(height * 640 / width)))), (width, height)

    @staticmethod
    def _points(gray, box, face=False):
        height, width = gray.shape
        x1, y1, x2, y2 = np.asarray(box).astype(int)
        if face:
            inset = max(2, round(min(x2 - x1, y2 - y1) * 0.15))
            x1, y1, x2, y2 = x1 + inset, y1 + inset, x2 - inset, y2 - inset
        mask = np.zeros_like(gray)
        mask[max(0, y1) : min(height, y2), max(0, x1) : min(width, x2)] = 255
        return cv2.goodFeaturesToTrack(
            gray, maxCorners=24, qualityLevel=0.01, minDistance=3, mask=mask
        )

    def _seed(self, analysis):
        gray, (width, height) = self._gray(analysis["source_jpeg"])
        scale = np.array([gray.shape[1] / width, gray.shape[0] / height] * 2)
        entities = {}
        for person in analysis["persons"]:
            box = np.asarray(person["box"], dtype=float) * scale
            face = person.get("face_box")
            label = dict(person.get("label") or {})
            if face is not None:
                face = np.asarray(face, dtype=float) * scale
                size = np.maximum(box[2:] - box[:2], 1)
                self.face_memory[person["id"]] = {
                    "at": analysis["at"],
                    "relative": (face - np.tile(box[:2], 2)) / np.tile(size, 2),
                    "label": label,
                }
            else:
                memory = self.face_memory.get(person["id"])
                if memory and analysis["at"] - memory["at"] <= 1.5:
                    size = np.maximum(box[2:] - box[:2], 1)
                    face = np.tile(box[:2], 2) + memory["relative"] * np.tile(size, 2)
                    label = label or dict(memory["label"])
            entities[person["id"]] = {
                "box": box,
                "source_box": np.asarray(person["box"], dtype=float),
                "source_face": np.asarray(person["face_box"], dtype=float)
                if person.get("face_box") is not None
                else None,
                "body_points": self._points(gray, box),
                "face": face,
                "face_points": self._points(gray, face, face=True) if face is not None else None,
                "label": label,
                "face_tracked_at": analysis["at"],
                "body_tracked_at": analysis["at"],
                "detected_at": analysis["at"],
                "carried": False,
                "head_velocity": np.zeros(2),
                "points_refreshed_at": analysis["at"],
                "continuity_since": analysis["at"],
                "geometry_state": "DETECTED",
            }
        self.face_memory = {
            identifier: memory
            for identifier, memory in self.face_memory.items()
            if identifier in entities and analysis["at"] - memory["at"] <= self.max_age
        }
        self.entities = entities
        self.gray, self.at, self.analysis_at = gray, analysis["at"], analysis["at"]
        self.analysis_revision = analysis.get("revision", 0)

    def _update_same_frame_metadata(self, analysis):
        """Recognition revisions cannot require replaying the same video twice."""
        if analysis["at"] != self.analysis_at:
            return False
        remaining = dict(self.entities)
        matched = []
        for person in analysis["persons"]:
            face = person.get("face_box")
            candidates = []
            for old_id, entity in remaining.items():
                old_face = entity.get("source_face")
                same_face = (face is None and old_face is None) or (
                    face is not None
                    and old_face is not None
                    and np.allclose(face, old_face, atol=0.001)
                )
                if same_face and np.allclose(person["box"], entity["source_box"], atol=0.001):
                    candidates.append((old_id, entity))
            if len(candidates) != 1:
                return False
            old_id, entity = candidates[0]
            remaining.pop(old_id)
            matched.append((person, old_id, entity))
        entities = {}
        for person, old_id, entity in matched:
            entity["label"] = dict(person.get("label") or {})
            entities[person["id"]] = entity
            if old_id != person["id"] and old_id in self.face_memory:
                self.face_memory[person["id"]] = self.face_memory.pop(old_id)
        for old_id, entity in remaining.items():
            if entity.get("carried"):
                entities[old_id] = entity
        self.entities = entities
        self.analysis_revision = analysis.get("revision", 0)
        return True

    def _advance(self, gray, at):
        if at <= self.at:
            return
        if self.gray is None or self.gray.shape != gray.shape:
            self.entities.clear()
            self.gray, self.at = gray, at
            return
        dt = at - self.at
        # A slow refresh is not an instruction to erase all detections.
        # Forward/backward flow and correlation must still validate movement.
        if dt > 0.75:
            for entity in self.entities.values():
                entity["continuity_since"] = at
        groups, arrays = [], []
        offset = 0
        for identifier, entity in self.entities.items():
            for kind in ("body", "face"):
                points = entity[kind + "_points"]
                if points is not None and len(points) >= 3:
                    groups.append((identifier, kind, offset, offset + len(points)))
                    arrays.append(points)
                    offset += len(points)
        movements = {}
        if arrays:
            original = np.concatenate(arrays)
            moved, status, _ = cv2.calcOpticalFlowPyrLK(
                self.gray, gray, original, None, winSize=(11, 11), maxLevel=1
            )
            if moved is not None:
                back, reverse, _ = cv2.calcOpticalFlowPyrLK(
                    gray, self.gray, moved, None, winSize=(11, 11), maxLevel=1
                )
                if back is not None:
                    valid = (
                        (status.ravel() == 1)
                        & (reverse.ravel() == 1)
                        & (np.linalg.norm(original - back, axis=2).ravel() < 2)
                    )
                    for identifier, kind, left, right in groups:
                        good = valid[left:right]
                        entity = self.entities[identifier]
                        minimum = 3
                        if good.sum() < minimum:
                            entity[kind + "_points"] = None
                            continue
                        src, dst = original[left:right][good], moved[left:right][good]
                        delta = np.median(dst - src, axis=0).reshape(2)
                        if np.linalg.norm(delta) > max(gray.shape) * 0.18:
                            entity[kind + "_points"] = None
                            continue
                        matrix, _ = cv2.estimateAffinePartial2D(
                            src.reshape(-1, 2), dst.reshape(-1, 2), method=cv2.LMEDS
                        )
                        if matrix is None or not 0.9 <= np.linalg.norm(matrix[:, 0]) <= 1.1:
                            matrix = np.array([[1, 0, delta[0]], [0, 1, delta[1]]])
                        movements[(identifier, kind)] = matrix
                        entity[kind + "_points"] = dst.reshape(-1, 1, 2)
        # Small head motion can be lost in torso-dominated pyramid flow.
        # A bounded face-only correlation search supplies an independent update.
        height, width = gray.shape
        for identifier, entity in self.entities.items():
            face = entity["face"]
            if face is None:
                continue
            x1, y1, x2, y2 = np.asarray(face).astype(int)
            inset = max(1, round(min(x2 - x1, y2 - y1) * 0.15))
            tx1, ty1 = max(0, x1 + inset), max(0, y1 + inset)
            tx2, ty2 = min(width, x2 - inset), min(height, y2 - inset)
            template = self.gray[ty1:ty2, tx1:tx2]
            padding = max(8, min(24, round(max(x2 - x1, y2 - y1) * 0.35)))
            sx1, sy1 = max(0, tx1 - padding), max(0, ty1 - padding)
            search = gray[sy1 : min(height, ty2 + padding), sx1 : min(width, tx2 + padding)]
            if (
                min(template.shape) < 8
                or float(template.std()) < 5
                or search.shape[0] < template.shape[0]
                or search.shape[1] < template.shape[1]
            ):
                continue
            correlation = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
            _, quality, _, location = cv2.minMaxLoc(correlation)
            if quality >= 0.75:
                dx, dy = sx1 + location[0] - tx1, sy1 + location[1] - ty1
                movements[(identifier, "face")] = np.array([[1, 0, dx], [0, 1, dy]], dtype=float)
                entity["face_points"] = self._points(gray, face + [dx, dy, dx, dy], face=True)
        height, width = gray.shape
        for identifier, entity in self.entities.items():
            body_transform = movements.get((identifier, "body"))
            if body_transform is not None:
                entity["body_tracked_at"] = at
                entity["box"] = self._project_box(entity["box"], body_transform, width, height)
            if entity["face"] is not None:
                face_transform = movements.get((identifier, "face"))
                if face_transform is not None:
                    old_face = entity["face"]
                    new_face = self._project_box(old_face, face_transform, width, height)
                    body_delta = body_transform[:, 2] if body_transform is not None else np.zeros(2)
                    relative_velocity = (new_face[:2] - old_face[:2] - body_delta) / max(dt, 0.001)
                    entity["head_velocity"] = (
                        0.5 * entity["head_velocity"] + 0.5 * relative_velocity
                    )
                    entity["face"] = new_face
                    entity["face_tracked_at"] = at
                    entity["geometry_state"] = "HEAD_TRACKED"
                elif body_transform is not None and at - entity["face_tracked_at"] <= 1.5:
                    predicted = body_transform.copy()
                    # Decay relative head velocity; torso motion is applied once.
                    predicted[:, 2] += np.clip(entity["head_velocity"] * dt, -4, 4)
                    entity["head_velocity"] *= 0.75
                    entity["face"] = self._project_box(entity["face"], predicted, width, height)
                    entity["geometry_state"] = "PREDICTED_HEAD"
                else:
                    entity["face"] = None
                    entity["geometry_state"] = "BODY_TRACKED"
                if entity["face"] is not None and min(entity["face"][2:] - entity["face"][:2]) < 4:
                    entity["face"] = None
            if at - entity["points_refreshed_at"] >= 0.75:
                if body_transform is not None:
                    entity["body_points"] = self._points(gray, entity["box"])
                if movements.get((identifier, "face")) is not None and entity["face"] is not None:
                    entity["face_points"] = self._points(gray, entity["face"], face=True)
                entity["points_refreshed_at"] = at
        self.entities = {
            identifier: entity
            for identifier, entity in self.entities.items()
            if min(entity["box"][2:] - entity["box"][:2]) >= 8
            and at - max(entity["body_tracked_at"], entity["face_tracked_at"]) <= 0.75
        }
        self.gray, self.at = gray, at

    @staticmethod
    def _project_box(box, matrix, width, height):
        x1, y1, x2, y2 = box
        center = np.array([(x1 + x2) / 2, (y1 + y2) / 2]) @ matrix[:, :2].T + matrix[:, 2]
        size = np.array([x2 - x1, y2 - y1]) * np.linalg.norm(matrix[:, 0])
        return np.r_[center - size / 2, center + size / 2].clip(
            [0, 0, 0, 0], [width, height, width, height]
        )

    def render(self, frame, analysis):
        began = time.perf_counter()
        with self.lock:
            timestamp = frame["at"]
            if not self.history or timestamp != self.history[-1][0]:
                gray, dimensions = self._gray(frame["jpeg"])
                self.history.append((timestamp, gray, dimensions))
                while self.history and timestamp - self.history[0][0] > 12:
                    self.history.popleft()
            if not analysis:
                return {"labels": [], "people": -1, "analysis_age_ms": -1, "tracking_ms": 0}
            age = max(0, timestamp - analysis["at"])
            # Compatibility for older integrations supplying label-only metadata.
            if "persons" not in analysis or "source_jpeg" not in analysis:
                return {
                    "labels": analysis.get("labels", []) if age <= self.max_age else [],
                    "people": -1,
                    "analysis_age_ms": round(age * 1000),
                    "tracking_ms": 0,
                }
            _, current, (source_width, source_height) = self.history[-1]
            is_new = (analysis["at"], analysis.get("revision", 0)) > (
                self.analysis_at,
                self.analysis_revision,
            )
            metadata_only = is_new and self._update_same_frame_metadata(analysis)
            if is_new and not metadata_only:
                self._advance(current, timestamp)
                previous = self.entities
                self._seed(analysis)
                for at, gray, _ in self.history:
                    if at > self.at:
                        self._advance(gray, at)
                incoming = {person["id"]: person for person in analysis["persons"]}
                # Keep current visual geometry rather than snapping back to delayed inference.
                for identifier, entity in previous.items():
                    if timestamp - entity["body_tracked_at"] > 0.25:
                        continue
                    if identifier in incoming:
                        replacement = self.entities.get(identifier)
                        if replacement is None or iou(entity["box"], replacement["box"]) >= 0.2:
                            entity["label"] = dict(incoming[identifier].get("label") or {})
                            # Fresh detector geometry replayed to this video frame
                            # corrects drift. Never let old flow override a new face box.
                            if replacement is not None and replacement["face"] is not None:
                                entity["face"] = replacement["face"]
                                entity["face_points"] = replacement["face_points"]
                                entity["face_tracked_at"] = replacement["face_tracked_at"]
                                entity["head_velocity"] = replacement["head_velocity"]
                                entity["geometry_state"] = replacement["geometry_state"]
                            entity["detected_at"] = analysis["at"]
                            entity["carried"] = False
                            self.entities[identifier] = entity
                    elif not any(
                        iou(entity["box"], other["box"]) > 0.35 for other in self.entities.values()
                    ):
                        entity["carried"] = True
                        self.entities[identifier] = entity
            else:
                self._advance(current, timestamp)
            labels = []
            if self.entities:
                height, width = current.shape
                for identifier, entity in self.entities.items():
                    label = dict(entity["label"])
                    if entity["face"] is None:
                        if not label.get("locked"):
                            continue
                        # A label anchor is honest when no face rectangle is available.
                        box = entity["box"]
                        face = np.array([box[0], box[1], box[0], box[1]]) / [
                            width,
                            height,
                            width,
                            height,
                        ]
                    else:
                        face = entity["face"] / [width, height, width, height]
                    if (
                        timestamp > self.analysis_at + 0.15
                        and not label.get("locked")
                        and label.get("identity_expires_at", 0) > 0
                    ):
                        label.update(identity_state="tracking", confirmed=False)
                    if (
                        not label.get("locked")
                        and label.get("identity_state") == "tracking"
                        and time.time() > label.get("identity_expires_at", 0)
                    ):
                        label.update(
                            name="Unknown", confirmed=False, score=0, identity_state="current"
                        )
                    if label.get("locked"):
                        label.update(
                            confirmed=True, identity_state="tracking" if age > 0.15 else "current"
                        )
                    label.update(
                        x=float(face[0]),
                        y=float(face[1]),
                        width=float(face[2] - face[0]),
                        height=float(face[3] - face[1]),
                        source_width=source_width,
                        source_height=source_height,
                        track_id=identifier,
                        tracking_state=entity["geometry_state"],
                        last_visual_age_ms=round(
                            max(
                                0,
                                timestamp
                                - max(entity["body_tracked_at"], entity["face_tracked_at"]),
                            )
                            * 1000
                        ),
                    )
                    labels.append(label)
            self.snapshots.append(
                (
                    timestamp,
                    {
                        identifier: {
                            "at": timestamp,
                            "box": (
                                entity["box"]
                                * np.array(
                                    [
                                        source_width / current.shape[1],
                                        source_height / current.shape[0],
                                    ]
                                    * 2
                                )
                            ).tolist(),
                            "visual_at": entity["body_tracked_at"],
                            "continuity_since": entity["continuity_since"],
                            "detected_at": entity["detected_at"],
                        }
                        for identifier, entity in self.entities.items()
                        if 0 <= timestamp - entity["body_tracked_at"] <= 0.25
                    },
                )
            )
            return {
                "labels": labels,
                "people": len(self.entities) if self.entities or age <= self.max_age else -1,
                "analysis_age_ms": round(age * 1000),
                "tracking_ms": round((time.perf_counter() - began) * 1000, 1),
            }

    def hints_at(self, timestamp):
        """Return past-only geometry pinned to the exact inference source time."""
        with self.lock:
            for at, hints in reversed(self.snapshots):
                if at <= timestamp and timestamp - at <= 0.25:
                    return {key: dict(value) for key, value in hints.items()}
            return {}
