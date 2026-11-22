"""Behavioral regressions for identity lifetime, ambiguity and attendance continuity."""

import itertools
import time

import numpy as np
from test_preview import analysis, frame, scene

from workledger.vision.motion import assignment
from workledger.vision.preview import PreviewTracker
from workledger.vision.tracking import Tracker, portal_ready


def observation(employee="one", box=None, body=None, **extra):
    return {
        "box": box or [100, 50, 200, 250],
        "employee_id": employee,
        "method": "face" if employee else "unknown",
        "score": 0.8 if employee else 0.2,
        "match_gap": 0.2,
        "yaw_ratio": 0.9,
        "attendance_eligible": True,
        "body": body,
        **extra,
    }


def step(tracker, people, at):
    tracker.associate(people, at)
    return tracker.update(people, at)


def lock(tracker, body=None):
    for at in (1, 1.2, 1.4):
        track = step(tracker, [observation(body=body)], at)[0]
    if body is not None:
        track.body_vector = body
    assert track.is_locked
    return track


def test_lock_survives_failed_match_quality_loss_and_face_absence():
    tracker = Tracker()
    track = lock(tracker)
    for at, data in (
        (2, {"face_vector": np.ones(512)}),
        (3, {"face_preview_reason": "Face too blurry"}),
        (4, {"face_preview_reason": "Face alignment unreliable"}),
        (5, {}),
    ):
        current = step(tracker, [observation(None, **data)], at)[0]
        assert current.id == track.id and current.employee_id == "one" and current.is_locked
        assert current.display_employee_id == "one"


def test_cached_or_duplicate_frames_do_not_create_votes():
    tracker = Tracker()
    for at in (1, 1.1, 1.2, 1.3):
        step(tracker, [observation(method="face_cached", recognition_skipped=True)], at)
    assert not next(iter(tracker.tracks.values())).is_locked
    track = step(tracker, [observation()], 1.4)[0]
    assert tracker.update([track.latest], 1.4) == []
    assert len(track.identity_votes) == 1


def test_body_only_matches_never_lock():
    tracker = Tracker()
    for at in (1, 1.2, 1.4, 1.6):
        track = step(tracker, [observation(method="body_tentative")], at)[0]
    assert not track.is_locked
    assert not portal_ready(track, {}, (480, 640), 1.6, 3)


def test_loss_recovery_preserves_id_and_event_state_but_requires_reverification():
    tracker = Tracker()
    body = np.array([1.0, 0.0])
    track = lock(tracker, body)
    track.emitted, track.crossing_emitted_at = True, 1.3
    tracker.associate([], 6.6)
    assert not tracker.tracks
    recovered = step(tracker, [observation(None, body=body)], 7)[0]
    assert recovered.id == track.id and recovered.employee_id == "one"
    assert recovered.emitted and recovered.crossing_emitted_at == 1.3
    assert recovered.needs_reverification
    assert not portal_ready(recovered, {}, (480, 640), 7, 3)
    verified = step(tracker, [observation(body=body)], 7.2)[0]
    assert not verified.needs_reverification


def test_same_position_without_matching_appearance_does_not_restore_identity():
    tracker = Tracker()
    old = lock(tracker, np.array([1.0, 0.0]))
    tracker.associate([], 6.6)
    replacement = step(tracker, [observation(None, body=np.array([0.0, 1.0]))], 7)[0]
    assert replacement.id != old.id and not replacement.is_locked


def test_recovery_expires_and_cannot_assign_one_identity_twice():
    tracker = Tracker()
    old = lock(tracker, np.array([1.0, 0.0]))
    tracker.associate([], 6.6)
    tracks = step(
        tracker,
        [
            observation(None, body=np.array([1.0, 0.0])),
            observation(None, body=np.array([1.0, 0.0])),
        ],
        7,
    )
    assert all(not t.is_locked for t in tracks)
    tracker.associate([], 10)
    new = step(tracker, [observation(None, body=np.array([1.0, 0.0]))], 11)[0]
    assert new.id != old.id and not new.is_locked


def test_identity_switch_needs_four_strong_frontal_votes_and_never_downgrades():
    tracker = Tracker()
    track = lock(tracker)
    for at in (2, 2.2, 2.4):
        track = step(tracker, [observation("two", score=0.9)], at)[0]
        assert track.employee_id == "one" and track.needs_reverification
    track = step(tracker, [observation("two", score=0.9)], 2.6)[0]
    assert track.employee_id == "two" and track.is_locked


def test_ambiguous_association_withholds_locked_identity():
    tracker = Tracker()
    old = lock(tracker)
    results = step(tracker, [observation(None), observation(None)], 2)
    assert all(t.id != old.id and not t.is_locked for t in results)
    assert tracker.tracks[old.id].locked_employee_id == "one"


def test_crossing_while_looking_away_uses_lock_without_counting_new_face_votes():
    tracker = Tracker()
    track = lock(tracker)
    config = {"trigger": "line", "line_position": 0.5}
    assert not portal_ready(track, config, (480, 640), 1.4, 3)
    track.box = [330, 50, 430, 250]
    track.method = "locked"
    assert portal_ready(track, config, (480, 640), 2, 3)
    track.needs_reverification = True
    assert not portal_ready(track, config, (480, 640), 2.1, 3)


def test_occlusion_never_creates_a_crossing():
    tracker = Tracker()
    track = lock(tracker, np.array([1.0, 0.0]))
    config = {"trigger": "line", "line_position": 0.5}
    portal_ready(track, config, (480, 640), 1.4, 3)
    returned = step(
        tracker, [observation(None, box=[330, 50, 430, 250], body=np.array([1.0, 0.0]))], 6
    )[0]
    assert not portal_ready(returned, config, (480, 640), 6, 3)


def test_locked_preview_stays_green_and_label_survives_missing_face():
    tracker = PreviewTracker()
    source = frame(scene(), time.time())
    metadata = analysis(source)
    label = metadata["persons"][0]["label"]
    label.update(locked=True, identity_expires_at=0)
    tracker.render(source, metadata)
    for index in range(1, 11):
        result = tracker.render(frame(scene(), source["at"] + index * 0.1), metadata)
    assert result["labels"][0]["confirmed"] and result["labels"][0]["name"] == "Employee"
    metadata["at"] = source["at"] + 1.1
    metadata["persons"][0]["face_box"] = None
    tracker.face_memory.clear()
    result = tracker.render(frame(scene(), source["at"] + 1.1), metadata)
    assert result["labels"][0]["width"] > 0 and result["labels"][0]["name"] == "Employee"


def test_preview_does_not_double_count_reassigned_overlapping_id():
    tracker = PreviewTracker()
    source = frame(scene(), 10)
    tracker.render(source, analysis(source))
    newer = frame(scene(), 10.5)
    metadata = analysis(newer)
    metadata["persons"][0]["id"] = 2
    assert tracker.render(newer, metadata)["people"] == 1


def test_global_assignment_matches_brute_force_optimum():
    rng = np.random.default_rng(42)
    for n, m in ((2, 4), (3, 5), (4, 3)):
        costs = rng.random((n, m))
        pairs = assignment(costs)
        actual = sum(costs[i, j] for i, j in pairs)
        if n <= m:
            expected = min(
                sum(costs[i, j] for i, j in enumerate(p))
                for p in itertools.permutations(range(m), n)
            )
        else:
            expected = min(
                sum(costs[i, j] for j, i in enumerate(p))
                for p in itertools.permutations(range(n), m)
            )
        assert abs(actual - expected) < 1e-9


def test_first_valid_match_stays_locked_through_subsequent_failures():
    tracker = Tracker()
    first = step(tracker, [observation()], 1)[0]
    assert first.is_locked and first.employee_id == "one" and first.hits == 1
    for at in (1.1, 1.2, 1.3, 1.4, 1.5):
        track = step(tracker, [observation(None, face_vector=np.ones(512))], at)[0]
        assert track.is_locked and track.employee_id == "one"


def test_weak_conflicting_matches_never_switch_lock():
    tracker = Tracker()
    track = lock(tracker)
    for at in (2, 2.2, 2.4, 2.6, 2.8):
        track = step(tracker, [observation("two", score=0.49)], at)[0]
    assert track.employee_id == "one" and track.needs_reverification
    assert not portal_ready(track, {}, (480, 640), 2.8, 3)


def test_fresh_face_can_restore_lost_identity_without_body_match():
    tracker = Tracker()
    old = lock(tracker)
    vector = np.zeros(512)
    vector[0] = 1
    old.locked_face_vector = vector
    tracker.associate([], 6.6)
    people = [observation(None)]
    tracker.associate(people, 7)
    people[0].update(
        employee_id="one",
        method="face",
        face_vector=vector,
        score=0.8,
        match_gap=0.2,
        yaw_ratio=0.9,
    )
    track = tracker.update(people, 7)[0]
    assert track.id == old.id and track.is_locked and not track.needs_reverification


def test_appearance_resolves_crowded_motion_assignment():
    tracker = Tracker()
    left, right = [100, 50, 200, 250], [250, 50, 350, 250]
    for at in (1, 1.2, 1.4):
        tracks = step(tracker, [observation("one", box=left), observation("two", box=right)], at)
    one, two = tracks
    one.body_vector, two.body_vector = np.array([1.0, 0.0]), np.array([0.0, 1.0])
    people = [
        observation(None, box=[165, 50, 265, 250], body=np.array([1.0, 0.0])),
        observation(None, box=[185, 50, 285, 250], body=np.array([0.0, 1.0])),
    ]
    tracker.associate(people, 1.8, appearance=lambda p: None)
    result = tracker.update(people, 1.8)
    assert [t.id for t in result] == [one.id, two.id]
    assert [t.employee_id for t in result] == ["one", "two"]


def test_candidate_evidence_for_other_employee_cannot_authorize_attendance():
    tracker = Tracker()
    step(tracker, [observation("one")], 1)
    for at in (1.2, 1.4, 1.6):
        track = step(
            tracker, [observation("two", score=0.4, yaw_ratio=0.3, attendance_eligible=False)], at
        )[0]
    assert track.is_locked and track.employee_id == "one"
    assert not portal_ready(track, {}, (480, 640), 1.6, 3)
