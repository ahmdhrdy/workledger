from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .identity import TrackIdentityManager
from .inference import normalized, rank
from .motion import BoxMotion, assignment
from .recovery import RecentlyLostIdentityCache, cosine


def iou(a, b):
    width = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    intersection = width * height
    return intersection / max(
        1, (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    )


@dataclass
class Track:
    id: int
    box: list
    seen: float
    employee_id: str | None = None
    method: str = "unknown"
    score: float = 0
    hits: int = 0
    emitted: bool = False
    unknown_emitted: bool = False
    observations: int = 0
    last_side: float | None = None
    crossing_at: float = 0
    latest: dict = field(default_factory=dict)
    last_identity: str | None = None
    display_employee_id: str | None = None
    display_until: float = 0
    display_score: float = 0
    attendance_employee_id: str | None = None
    attendance_verified_at: float = -1e9
    verified_at: float = -1e9
    body_vector: object = None
    body_at: float = -1e9
    locked_face_vector: object = None
    last_face_vector: object = None
    last_face_score: float = 0
    last_face_identity: str | None = None
    face_attempt_at: float = -1e9
    face_attempt_yaw: float = 0
    face_attempt_quality: float = 0
    identity_state: str = "UNIDENTIFIED"
    is_locked: bool = False
    locked_employee_id: str | None = None
    identity_votes: deque = field(default_factory=lambda: deque(maxlen=5))
    conflict_votes: deque = field(default_factory=lambda: deque(maxlen=4))
    last_vote_at: float = -1e9
    association_reliable: bool = True
    identity_conflict: bool = False
    needs_reverification: bool = False
    recovered: bool = False
    motion: object = None
    last_side_at: float = 0
    last_visual_at: float = -1e9
    transition_reason: str = "NEW_TRACK"
    last_update_at: float = -1e9
    episode_id: str = ""
    crossing_emitted_at: float = 0


class Tracker:
    def __init__(self, confirmations=3, identity_hold_seconds=2.0, settings=None):
        self.tracks = {}
        self.counter = 0
        self.confirmations = confirmations
        self.identity_hold_seconds = identity_hold_seconds
        self.max_age = getattr(settings, "track_lost_seconds", 5.0)
        self.recovery = RecentlyLostIdentityCache(getattr(settings, "recovery_seconds", 8.0))
        self.identities = TrackIdentityManager(
            confirmations,
            getattr(settings, "identity_vote_seconds", 8.0),
            getattr(settings, "identity_switch_score", 0.65),
            getattr(settings, "identity_switch_margin", 0.15),
        )

    def associate(self, people, at, appearance=None, hints=None):
        """One motion/appearance assignment per neural refresh, consumed by update."""
        for person in people:
            for key in (
                "_track_id",
                "_recovered",
                "_association_ambiguous",
                "_association_reliable",
                "_visual_continuity",
            ):
                person.pop(key, None)
        hints = {
            key: hint
            for key, hint in (hints or {}).items()
            if key in self.tracks
            and 0 <= at - hint["at"] <= 0.25
            and 0 <= hint["at"] - hint["visual_at"] <= 0.25
            and hint["continuity_since"] <= self.tracks[key].seen + 0.25
            and hint["detected_at"] <= at
        }
        for key, hint in hints.items():
            self.tracks[key].last_visual_at = hint["visual_at"]
        self.recovery.prune(at)
        for identifier, track in list(self.tracks.items()):
            if at - max(track.seen, track.last_visual_at) > self.max_age:
                self.recovery.remember(track)
                del self.tracks[identifier]
        tracks = list(self.tracks.values())
        predicted = []
        for track in tracks:
            predicted_box = track.motion.predict(at) if track.motion else track.box
            predicted.append(hints[track.id]["box"] if track.id in hints else predicted_box)
        cost = np.full((len(people), len(tracks) + len(people)), 0.95)
        for i, person in enumerate(people):
            box = np.asarray(person["box"], float)
            center = (box[:2] + box[2:]) / 2
            for j, (track, prediction) in enumerate(zip(tracks, predicted, strict=True)):
                old = np.asarray(prediction)
                size = max(np.linalg.norm(old[2:] - old[:2]), 1)
                distance = np.linalg.norm(center - (old[:2] + old[2:]) / 2) / size
                overlap = iou(box, old)
                if overlap < 0.05 and distance > 0.65:
                    cost[i, j] = 10
                    continue
                gap = at - track.seen
                continuous = track.id in hints and overlap >= 0.5
                if (gap > 1.5 and not continuous) or overlap < 0.18:
                    if appearance and person.get("body") is None:
                        appearance(person)
                    similarity = cosine(track.body_vector, person.get("body"))
                    if similarity < 0.88:
                        cost[i, j] = 10
                        continue
                    cost[i, j] = (
                        0.25 * (1 - overlap) + 0.15 * min(distance, 1) + 0.6 * (1 - similarity)
                    )
                else:
                    cost[i, j] = 0.7 * (1 - overlap) + 0.3 * min(distance, 1)
        # Appearance resolves close motion hypotheses before global assignment.
        if appearance:
            for i, person in enumerate(people):
                viable = [j for j in range(len(tracks)) if cost[i, j] < 0.9]
                crowded = len(viable) > 1 or any(
                    sum(cost[k, j] < 0.9 for k in range(len(people))) > 1 for j in viable
                )
                if crowded:
                    if person.get("body") is None:
                        appearance(person)
                    for j in viable:
                        similarity = cosine(tracks[j].body_vector, person.get("body"))
                        if similarity >= 0.7:
                            cost[i, j] = 0.4 * cost[i, j] + 0.6 * (1 - similarity)
                        elif tracks[j].body_vector is not None:
                            cost[i, j] = 10
        matched = set()
        for i, j in assignment(cost):
            if j >= len(tracks) or cost[i, j] >= 0.9:
                continue
            track, person = tracks[j], people[i]
            alternatives = [cost[i, k] for k in range(len(tracks)) if k != j]
            competitors = [cost[k, j] for k in range(len(people)) if k != i]
            ambiguous = bool(
                (alternatives and min(alternatives) - cost[i, j] < 0.08)
                or (competitors and min(competitors) - cost[i, j] < 0.08)
            )
            # Never attach a locked identity to an ambiguous assignment.
            if ambiguous:
                person["_association_ambiguous"] = True
                continue
            continuous = track.id in hints and iou(person["box"], hints[track.id]["box"]) >= 0.5
            recovered = at - track.seen > 1.5 and not continuous
            person.update(
                _track_id=track.id,
                _association_reliable=True,
                _association_ambiguous=False,
                _recovered=recovered,
                _visual_continuity=continuous,
            )
            matched.add(i)
        assigned_ids = {p.get("_track_id") for p in people}
        for track in tracks:
            if track.id not in assigned_ids and track.is_locked and at - track.seen > 1.5:
                self.recovery.remember(track)
        used_employees = {
            self.tracks[p["_track_id"]].locked_employee_id
            for p in people
            if p.get("_track_id") in self.tracks
        }
        used_employees.update(t.locked_employee_id for t in tracks if at - t.seen <= 1.5)
        for i, person in enumerate(people):
            if i in matched:
                continue
            if appearance and self.recovery.entries and person.get("body") is None:
                appearance(person)
            candidates = self.recovery.candidates(person, at, used_employees)
            # Unique recovery in both directions: no two detections may claim one lost person.
            if candidates and (len(candidates) == 1 or candidates[0][0] - candidates[1][0] >= 0.08):
                score, candidate = candidates[0]
                competitors = [
                    cosine(candidate.body_vector, other.get("body"))
                    for k, other in enumerate(people)
                    if k != i and k not in matched
                ]
                if not competitors or score - max(competitors) >= 0.08:
                    track = self.recovery.entries.pop(candidate.id)
                    self.tracks[track.id] = track
                    used_employees.add(track.locked_employee_id)
                    person.update(
                        _track_id=track.id,
                        _association_reliable=True,
                        _association_ambiguous=False,
                        _recovered=True,
                    )
                    continue
            self.counter += 1
            track = Track(self.counter, list(person["box"]), at)
            track.motion = BoxMotion(track.box, at)
            track.episode_id = str(track.id)
            self.tracks[track.id] = track
            person.update(
                _track_id=track.id,
                _association_reliable=not person.get("_association_ambiguous", False),
            )

    def update(self, people, at):
        if any(person.get("_track_id") not in self.tracks for person in people):
            self.associate(people, at)
        result = []
        for person in people:
            track = self.tracks[person["_track_id"]]
            # A fresh face can recover a lost lock when body appearance is inconclusive.
            if (
                not track.is_locked
                and person.get("method") == "face"
                and person.get("face_vector") is not None
            ):
                active = {t.locked_employee_id for t in self.tracks.values() if at - t.seen <= 1.5}
                candidates = self.recovery.candidates(person, at, active)
                if candidates and (
                    len(candidates) == 1 or candidates[0][0] - candidates[1][0] >= 0.08
                ):
                    restored = self.recovery.entries.pop(candidates[0][1].id)
                    del self.tracks[track.id]
                    track = restored
                    self.tracks[track.id] = track
                    if track.motion:
                        track.motion.predict(at)
                    person.update(_track_id=track.id, _recovered=True, _association_reliable=True)
            if at <= track.last_update_at:
                continue
            track.last_update_at = at
            previous_identity, previous_method = track.employee_id, track.method
            track.recovered = bool(person.get("_recovered"))
            track.association_reliable = bool(person.get("_association_reliable", True))
            track.identity_conflict = bool(person.get("identity_conflict"))
            if at - track.seen > 1.5:
                # Do not infer a line crossing from a gap in detector observations.
                track.last_side, track.crossing_at = None, 0
            if track.recovered or not track.association_reliable or track.identity_conflict:
                track.last_side, track.crossing_at = None, 0
                track.needs_reverification = True
            track.transition_reason = (
                "RECOVERED"
                if track.recovered
                else "ASSOCIATION_UNCERTAIN"
                if not track.association_reliable
                else "VISUAL_CONTINUITY"
                if person.get("_visual_continuity")
                else "DETECTION_ASSOCIATED"
                if track.observations
                else "NEW_TRACK"
            )
            face = person.get("face_box")
            previous_face = track.latest.get("preview_face_box", track.latest.get("face_box"))
            if face is not None:
                smoothed = np.asarray(face, float)
                if previous_face is not None and track.association_reliable and not track.recovered:
                    old_box, new_box = np.asarray(track.box), np.asarray(person["box"])
                    relative = (np.asarray(previous_face) - np.tile(old_box[:2], 2)) / np.tile(
                        np.maximum(old_box[2:] - old_box[:2], 1), 2
                    )
                    predicted_face = np.tile(new_box[:2], 2) + relative * np.tile(
                        new_box[2:] - new_box[:2], 2
                    )
                    if (
                        np.linalg.norm(smoothed - predicted_face)
                        < max(new_box[2:] - new_box[:2]) * 0.5
                    ):
                        smoothed = 0.7 * smoothed + 0.3 * predicted_face
                person["preview_face_box"] = smoothed.tolist()
            track.seen, track.box, track.latest = at, list(person["box"]), person
            if track.motion:
                track.motion.correct(track.box)
            track.observations += 1
            identity = person.get("employee_id")
            method = person.get("method", "unknown")
            track.score = person.get("score", 0)
            if identity and not person.get("recognition_skipped"):
                if track.last_identity and track.last_identity != identity and not track.is_locked:
                    track.emitted = track.unknown_emitted = False
                track.hits = (
                    track.hits + 1
                    if previous_identity == identity
                    and previous_method in (method, "face_cached", "locked")
                    else 1
                )
                track.last_identity = identity
            elif not person.get("recognition_skipped") and not track.is_locked:
                track.hits = 0
            track.employee_id, track.method = identity, method
            was_locked = track.is_locked
            if (
                not track.is_locked
                and identity
                and any(
                    other.id != track.id
                    and other.is_locked
                    and other.locked_employee_id == identity
                    and at - max(other.seen, other.last_visual_at) <= 1.5
                    for other in self.tracks.values()
                )
            ):
                # A new candidate cannot steal a currently tracked locked identity.
                person = {**person, "identity_conflict": True}
                track.latest = person
                track.identity_conflict = True
            self.identities.update(track, person, at)
            if track.is_locked and not was_locked:
                track.transition_reason = "IDENTITY_LOCKED"
            elif track.is_locked and person.get("face_vector") is not None and identity is None:
                track.transition_reason = "LOCK_HELD_LOW_MATCH"
            if track.is_locked:
                track.last_identity = track.locked_employee_id
                fresh_same = (
                    method == "face"
                    and identity == track.locked_employee_id
                    and not person.get("recognition_skipped")
                )
                track.method = (
                    "face"
                    if fresh_same
                    else "face_cached"
                    if person.get("recognition_skipped")
                    else "locked"
                )
                track.score = track.display_score
            result.append(track)
        # Enforce one visible owner per locked employee. Keep memory but withhold attribution.
        owners = {}
        for track in result:
            if track.is_locked:
                owners.setdefault(track.locked_employee_id, []).append(track)
        for claims in owners.values():
            if len(claims) > 1:
                for track in claims:
                    track.association_reliable = False
                    track.needs_reverification = True
        return result


class BodyGallery:
    def __init__(self):
        self.samples = deque(maxlen=300)

    def add(self, company, employee, camera, vector, at):
        if vector is not None:
            self.samples.append(
                {
                    "company": company,
                    "employee_id": employee,
                    "camera": camera,
                    "vector": normalized(vector),
                    "at": at,
                }
            )

    def match(self, company, vector, at, threshold, margin):
        self.samples = deque((s for s in self.samples if 0 <= at - s["at"] < 300), maxlen=300)
        if vector is None:
            return None, 0
        return rank(vector, [s for s in self.samples if s["company"] == company], threshold, margin)


def portal_ready(track, config, frame_shape, at, confirmations):
    """Observe crossing geometry even when identity verification is deferred."""
    reliable = getattr(track, "association_reliable", True)
    if config.get("trigger", "presence") == "line":
        h, w = frame_shape[:2]
        center = (
            (track.box[0] + track.box[2]) / 2 / w
            if config.get("line_axis", "vertical") == "vertical"
            else track.box[3] / h
        )
        side = center - config.get("line_position", 0.5)
        previous = track.last_side
        recent = at - getattr(track, "last_side_at", at) <= 1.5
        if reliable and abs(side) > 0.02:
            if previous is not None and recent and previous * side < 0:
                forward = side > previous
                if forward == (config.get("line_direction", "positive") == "positive"):
                    track.crossing_at = at
            track.last_side, track.last_side_at = side, at
        elif not reliable:
            track.last_side, track.crossing_at = None, 0
        geometry_ready = bool(track.crossing_at and at - track.crossing_at < 4)
    else:
        geometry_ready = True
    if getattr(track, "is_locked", False):
        return bool(
            geometry_ready
            and reliable
            and track.hits >= confirmations
            and not track.needs_reverification
            and track.attendance_employee_id == track.locked_employee_id
            and at - track.attendance_verified_at <= 5
        )
    # Compatibility for simple callers. Unlocked body matches cannot create events.
    return bool(
        geometry_ready
        and getattr(track, "method", "unknown") == "face"
        and track.hits >= confirmations
    )
