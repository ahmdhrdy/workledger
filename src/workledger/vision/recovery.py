"""Camera-local, bounded lost-track memory. Proximity alone never restores identity."""

import numpy as np


def cosine(left, right):
    if left is None or right is None:
        return -1.0
    a, b = np.asarray(left).ravel(), np.asarray(right).ravel()
    if a.shape != b.shape:
        return -1.0
    return float(a @ b / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-8))


class RecentlyLostIdentityCache:
    def __init__(self, seconds=8):
        self.seconds = seconds
        self.entries = {}

    def remember(self, track):
        if track.is_locked:
            self.entries[track.id] = track

    def prune(self, at):
        self.entries = {
            key: t for key, t in self.entries.items() if 0 <= at - t.seen <= self.seconds
        }

    def candidates(self, person, at, active_employees=()):
        self.prune(at)
        scores = []
        box = np.asarray(person["box"], float)
        center = (box[:2] + box[2:]) / 2
        for track in self.entries.values():
            if track.locked_employee_id in active_employees:
                continue
            if person.get("method") == "face" and person.get("employee_id") not in (
                None,
                track.locked_employee_id,
            ):
                continue
            old = np.asarray(track.box)
            distance = np.linalg.norm(center - (old[:2] + old[2:]) / 2) / max(
                np.linalg.norm(old[2:] - old[:2]), 1
            )
            # Require current appearance, a clear margin, and plausible travel.
            similarity = cosine(track.body_vector, person.get("body"))
            face_score = cosine(track.locked_face_vector, person.get("face_vector"))
            face_verified = (
                person.get("method") == "face"
                and person.get("employee_id") == track.locked_employee_id
                and person.get("score", 0) >= 0.5
                and person.get("match_gap", 0) >= 0.08
                and face_score >= 0.55
            )
            if (similarity >= 0.88 or face_verified) and distance <= min(
                3, 0.75 + (at - track.seen) * 0.4
            ):
                scores.append((max(similarity, face_score if face_verified else -1), track))
        return sorted(scores, key=lambda x: x[0], reverse=True)
