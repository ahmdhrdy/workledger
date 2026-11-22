"""Identity evidence is independent of visibility, geometry and attendance evidence."""

from collections import deque


class TrackIdentityManager:
    def __init__(self, confirmations=3, vote_seconds=8, switch_score=0.65, switch_margin=0.15):
        self.confirmations = confirmations
        self.vote_seconds = vote_seconds
        self.switch_score, self.switch_margin = switch_score, switch_margin

    def update(self, track, person, at):
        identity = person.get("employee_id")
        fresh = (
            person.get("method") == "face" or person.get("face_vector") is not None
        ) and not person.get("recognition_skipped")
        valid = (
            fresh
            and person.get("method") == "face"
            and identity is not None
            and not person.get("identity_conflict")
        )
        valid = valid and track.association_reliable
        if fresh and at > track.last_vote_at:
            track.last_vote_at = at
            # A failed valid-quality comparison is a negative vote, never an unlock.
            track.identity_votes.append((at, identity if valid else None))
        track.identity_votes = deque(
            ((t, who) for t, who in track.identity_votes if at - t <= self.vote_seconds), maxlen=5
        )
        if valid and (not track.is_locked or identity == track.locked_employee_id):
            track.verified_at = at
            if person.get("face_vector") is not None:
                track.locked_face_vector = person["face_vector"].copy()
            if person.get("attendance_eligible", True):
                track.attendance_verified_at = at
                track.attendance_employee_id = identity
        if track.is_locked:
            if valid and identity == track.locked_employee_id:
                track.verified_at, track.display_score = at, person.get("score", 0)
                track.conflict_votes.clear()
                track.needs_reverification = False
            elif valid and identity != track.locked_employee_id:
                track.needs_reverification = True
                if (
                    person.get("score", 0) >= self.switch_score
                    and person.get("match_gap", 0) >= self.switch_margin
                    and person.get("yaw_ratio", 0) >= 0.5
                    and abs(person.get("nose_offset", 0)) <= 0.5
                ):
                    track.conflict_votes.append((at, identity))
                else:
                    track.conflict_votes.clear()
                votes = [(t, who) for t, who in track.conflict_votes if at - t <= self.vote_seconds]
                if len(votes) >= 4 and all(who == identity for _, who in votes[-4:]):
                    track.locked_employee_id = identity
                    track.display_score, track.verified_at = person["score"], at
                    track.locked_face_vector = person.get("face_vector")
                    track.attendance_employee_id = (
                        identity if person.get("attendance_eligible", False) else None
                    )
                    track.attendance_verified_at = (
                        at if person.get("attendance_eligible", False) else -1e9
                    )
                    track.emitted = track.unknown_emitted = False
                    track.last_side, track.crossing_at = None, 0
                    track.conflict_votes.clear()
                    track.needs_reverification = False
            # Failed comparison/quality gates do not downgrade a locked identity.
            track.employee_id = track.display_employee_id = track.locked_employee_id
            track.display_until = float("inf")
            track.identity_state = "LOCKED"
            return
        if valid:
            votes = [who for _, who in track.identity_votes]
            # Preview identity is immediate. Attendance independently requires
            # multiple fresh qualifying observations in portal_ready/runtime.
            if votes[-1] == identity:
                track.is_locked = True
                track.locked_employee_id = track.employee_id = track.display_employee_id = identity
                track.identity_state = "LOCKED"
                track.display_score, track.verified_at = person["score"], at
                track.display_until = float("inf")
                return
        track.identity_state = (
            "CANDIDATE" if any(who for _, who in track.identity_votes) else "UNIDENTIFIED"
        )
