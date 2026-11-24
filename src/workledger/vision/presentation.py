"""Low-cost preview publication independent of recognition/attendance confirmation."""

import time

from .tracking import iou


def detection_overlay(frame, people, tracker, names, stage):
    h, w = frame["shape"][:2]
    labels = []
    persons = []
    used = set()
    for index, person in enumerate(people):
        face = person.get("face_box")
        track = tracker.tracks.get(person.get("_track_id"))
        if track is None:
            candidates = sorted(
                (
                    (iou(person["box"], t.box), t)
                    for t in tracker.tracks.values()
                    if t.id not in used and 0 <= frame["at"] - t.seen < 3
                ),
                key=lambda item: item[0],
                reverse=True,
            )
            if (
                candidates
                and candidates[0][0] >= 0.5
                and (len(candidates) == 1 or candidates[0][0] - candidates[1][0] >= 0.1)
            ):
                track = candidates[0][1]
        if person.get("_association_ambiguous") or not person.get("_association_reliable", True):
            track = None
        identifier = track.id if track else -(index + 1)
        used.add(identifier)
        # A locked identity wins over a transient weak/different comparison.
        employee = track.display_employee_id if track and track.association_reliable else None
        if (
            employee is None
            and person.get("method") == "face"
            and not person.get("identity_conflict")
            and not person.get("_association_ambiguous")
            and person.get("_association_reliable", True)
        ):
            candidate = person.get("employee_id")
            other_owner = any(
                t.id != identifier
                and t.is_locked
                and t.locked_employee_id == candidate
                and 0 <= frame["at"] - t.seen < 3
                for t in tracker.tracks.values()
            )
            duplicate_claim = any(
                other is not person
                and other.get("method") == "face"
                and other.get("employee_id") == candidate
                for other in people
            )
            employee = None if other_owner or duplicate_claim else candidate
        score = (
            track.display_score
            if track and employee == track.display_employee_id
            else person.get("score", 0)
        )
        label = {
            "track_id": identifier,
            "attendance_proof": {
                "attendance_verified_at": track.attendance_verified_at if track else -1e9,
                "attendance_employee_id": track.attendance_employee_id if track else None,
                "hits": track.hits if track else 0,
                "needs_reverification": track.needs_reverification if track else True,
                "association_reliable": track.association_reliable if track else True,
            },
            "name": names.get(employee, "Checking"),
            "confirmed": bool(employee),
            "locked": bool(employee),
            "employee_id": employee,
            "identity_status": "LOCKED" if employee else "UNIDENTIFIED",
            "identity_state": "current",
            "identity_expires_at": 0,
            "method": person.get("method", "unknown"),
            "score": round(float(score), 3),
            "latest_match_score": round(float(person.get("score", 0)), 3),
            "detection_score": round(float(person.get("detection_score", 0)), 3),
            "transition_reason": "FIRST_VALID_MATCH" if employee else "DETECTED",
            "quality_reason": person.get("face_preview_reason"),
            "source_width": w,
            "source_height": h,
        }
        if face is not None:
            label.update(
                x=face[0] / w,
                y=face[1] / h,
                width=(face[2] - face[0]) / w,
                height=(face[3] - face[1]) / h,
            )
            labels.append(label)
        persons.append(
            {
                "id": identifier,
                "box": list(person["box"]),
                "face_box": face,
                "label": label,
                "person_detected": person.get("person_detected", False),
            }
        )
    return {
        "at": frame["at"],
        "source_jpeg": frame["jpeg"],
        "persons": persons,
        "labels": labels,
        "revision": time.monotonic_ns(),
        "stage": stage,
    }
