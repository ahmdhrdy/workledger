"""Enrollment and live side-view quality policy without relaxing identity matching."""

import numpy as np
import pytest
from test_preview import scheduler
from test_sticky_identity import lock, observation, step

from workledger.config import Settings
from workledger.vision.face_align import landmark_geometry
from workledger.vision.face_engine import FaceEngine
from workledger.vision.pose_matcher import PoseAdaptiveMatcher
from workledger.vision.tracking import Tracker


def side_points():
    return np.array([[110, 100], [150, 100], [170, 135], [128, 170], [163, 170]], float)


def engine():
    value = FaceEngine.__new__(FaceEngine)
    value.settings = Settings(_env_file=None, face_blur_min=60)
    return value


def image():
    return np.random.default_rng(42).integers(0, 256, (300, 300, 3), dtype=np.uint8)


def test_nose_outside_eye_span_is_not_itself_a_rejection():
    points = side_points()
    assert points[2, 0] > points[:2, 0].max()
    aligned, quality = engine().preprocess_and_align(image(), [75, 70, 210, 200], points, 65)
    assert aligned.shape == (112, 112, 3) and quality["nose_offset"] == 1
    assert quality["alignment_error"] < 18 and quality["quality"] >= 60


def test_other_side_is_accepted_with_semantic_landmark_order():
    points = side_points()
    points[:, 0] = 300 - points[:, 0]
    points = points[[1, 0, 2, 4, 3]]
    aligned, quality = engine().preprocess_and_align(image(), [90, 70, 225, 200], points, 65)
    assert aligned.shape == (112, 112, 3) and quality["nose_offset"] == -1


def test_roll_does_not_change_pose_geometry():
    points = side_points()
    angle = np.deg2rad(35)
    rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    moved = (points - [150, 150]) @ rotation.T + [150, 150]
    box = np.r_[moved.min(axis=0) - 20, moved.max(axis=0) + 20]
    before = landmark_geometry(points, [75, 70, 210, 200])
    after = landmark_geometry(moved, box)
    assert np.isclose(before["nose_offset"], after["nose_offset"])
    assert np.isclose(before["yaw_ratio"], after["yaw_ratio"])
    assert engine().preprocess_and_align(image(), box, moved, 32)[0].shape == (112, 112, 3)


@pytest.mark.parametrize("case", ["eyes", "mouth", "ordering", "outside", "nan", "shape"])
def test_invalid_landmarks_still_rejected(case):
    points = side_points()
    if case == "eyes":
        points[1] = points[0]
    elif case == "mouth":
        points[4] = points[3]
    elif case == "ordering":
        points[3:, 1] = 80
    elif case == "outside":
        points[2] = [280, 135]
    elif case == "nan":
        points[2, 0] = np.nan
    else:
        points = points[:4]
    with pytest.raises(ValueError):
        engine().preprocess_and_align(image(), [75, 70, 210, 200], points, 65)


def test_blur_gate_is_preserved_for_side_views():
    with pytest.raises(ValueError, match="blurry"):
        engine().preprocess_and_align(
            np.zeros((300, 300, 3), np.uint8), [75, 70, 210, 200], side_points(), 65
        )


def test_acceptable_side_view_reverifies_existing_identity():
    model, vector, calls = scheduler()
    tracker = Tracker()
    matcher = PoseAdaptiveMatcher(["employee"], [vector])
    for at in (1, 1.1, 1.2):
        people = model.analyze(
            np.zeros((120, 120, 3), np.uint8), [], tracker=tracker, at=at, matcher=matcher
        )
        track = tracker.update(people, at)[0]
    assert track.is_locked
    model.engine.preprocess_and_align = lambda *args: (
        np.zeros((112, 112, 3)),
        {"yaw_ratio": 0.3, "nose_offset": 1.0, "quality": 100},
    )
    # Detector observations continued while embeddings were cached/quality-gated.
    track.seen = 7
    people = model.analyze(
        np.zeros((120, 120, 3), np.uint8), [], tracker=tracker, at=7.1, matcher=matcher
    )
    refreshed = tracker.update(people, 7.1)[0]
    assert refreshed.is_locked and refreshed.verified_at == 7.1 and len(calls) >= 4


def test_side_view_cannot_overwrite_locked_identity_even_with_high_score():
    tracker = Tracker()
    track = lock(tracker)
    for at in (2, 2.2, 2.4, 2.6, 2.8):
        track = step(tracker, [observation("two", score=0.9, nose_offset=1.0)], at)[0]
    assert track.employee_id == "one" and track.is_locked
