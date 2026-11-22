"""First-result display is independent of attendance confirmations and quality rejection."""

import threading
import time

import numpy as np
import pytest
from test_preview import frame, scheduler
from test_sticky_identity import observation, step

from workledger.vision.pose_matcher import PoseAdaptiveMatcher
from workledger.vision.presentation import detection_overlay
from workledger.vision.preview import PreviewTracker
from workledger.vision.tracking import Tracker, portal_ready


@pytest.mark.parametrize("pose", [0.9, 0.3])
def test_first_qualifying_front_or_side_match_has_immediate_sticky_display(pose):
    tracker = Tracker(confirmations=3)
    first = step(tracker, [observation(yaw_ratio=pose)], 1)[0]
    assert first.is_locked and first.display_employee_id == "one" and first.hits == 1
    assert not portal_ready(first, {}, (480, 640), 1, 3)
    second = step(tracker, [observation(yaw_ratio=pose)], 1.1)[0]
    assert not portal_ready(second, {}, (480, 640), 1.1, 3)
    third = step(tracker, [observation(yaw_ratio=pose)], 1.2)[0]
    assert portal_ready(third, {}, (480, 640), 1.2, 3)


def test_boxes_are_published_before_blocking_embedding():
    model, vector, _ = scheduler()
    tracker = Tracker()
    detected = threading.Event()
    release = threading.Event()
    events = []
    errors = []

    def embed(image):
        assert detected.is_set()
        release.wait(3)
        return vector

    model.engine.get_embedding = embed

    def progress(people, stage):
        events.append((stage, [dict(p) for p in people]))
        if stage == "detected":
            detected.set()

    def run():
        try:
            model.analyze(
                np.zeros((120, 120, 3), np.uint8),
                [],
                tracker=tracker,
                at=1,
                matcher=PoseAdaptiveMatcher(["one"], [vector]),
                on_progress=progress,
            )
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert detected.wait(2)
        assert events[0][1][0]["face_box"] == [10.0, 10.0, 100.0, 100.0]
        assert not any(stage == "matched" for stage, _ in events)
    finally:
        release.set()
        worker.join(timeout=4)
    assert not errors and any(stage == "matched" for stage, _ in events)


def test_quality_rejection_does_not_suppress_detected_box():
    model, vector, _ = scheduler()
    events = []

    def reject(*args):
        raise ValueError("Face too blurry")

    model.engine.preprocess_and_align = reject
    people = model.analyze(
        np.zeros((120, 120, 3), np.uint8),
        [],
        tracker=Tracker(),
        at=1,
        matcher=PoseAdaptiveMatcher(["one"], [vector]),
        on_progress=lambda people, stage: events.append((stage, people[0]["face_box"])),
    )
    assert events[0] == ("detected", [10.0, 10.0, 100.0, 100.0])
    assert (
        people[0]["employee_id"] is None and people[0]["face_preview_reason"] == "Face too blurry"
    )


def test_same_frame_detection_then_match_updates_label_without_next_frame():
    tracker = Tracker()
    preview = PreviewTracker()
    source = frame(
        np.random.default_rng(42).integers(0, 256, (120, 120, 3), dtype=np.uint8), time.time()
    )
    source["shape"] = (120, 120, 3)
    person = {"box": [10, 10, 100, 100], "face_box": [10, 10, 100, 100], "detection_score": 0.9}
    raw = detection_overlay(source, [person], tracker, {"one": "Employee"}, "detected")
    result = preview.render(source, raw)
    assert result["labels"][0]["width"] > 0.5 and result["labels"][0]["name"] == "Checking"
    person.update(employee_id="one", method="face", score=0.6)
    match = detection_overlay(source, [person], tracker, {"one": "Employee"}, "matched")
    result = preview.render(source, match)
    assert result["labels"][0]["name"] == "Employee" and result["labels"][0]["locked"]
    assert match["at"] == raw["at"] and match["revision"] > raw["revision"]


def test_low_score_progress_keeps_existing_locked_employee_name():
    tracker = Tracker()
    known = step(tracker, [observation()], 1)[0]
    source = frame(np.zeros((480, 640, 3), np.uint8), 1.1)
    source["shape"] = (480, 640, 3)
    person = observation(None, face_box=[120, 70, 180, 130], score=0.2)
    person["_track_id"] = known.id
    overlay = detection_overlay(source, [person], tracker, {"one": "Employee"}, "matched")
    assert overlay["labels"][0]["name"] == "Employee" and overlay["labels"][0]["locked"]


def test_same_frame_match_metadata_does_not_replay_video_again():
    tracker = Tracker()
    preview = PreviewTracker()
    source = frame(
        np.random.default_rng(24).integers(0, 256, (120, 120, 3), dtype=np.uint8), time.time()
    )
    source["shape"] = (120, 120, 3)
    person = {"box": [10, 10, 100, 100], "face_box": [10, 10, 100, 100], "detection_score": 0.9}
    preview.render(
        source, detection_overlay(source, [person], tracker, {"one": "Employee"}, "detected")
    )

    def no_replay(metadata):
        raise AssertionError("Same-frame name update repeated video replay")

    preview._seed = no_replay
    person.update(employee_id="one", method="face", score=0.6)
    result = preview.render(
        source, detection_overlay(source, [person], tracker, {"one": "Employee"}, "matched")
    )
    assert result["labels"][0]["name"] == "Employee"


def test_unidentified_face_is_not_delayed_by_failed_match_retry_timer():
    model, vector, calls = scheduler()
    matcher = PoseAdaptiveMatcher(["one"], [vector.copy()])
    tracker = Tracker()
    image = np.zeros((120, 120, 3), np.uint8)
    vector[:] = 0
    vector[1] = 1
    first = model.analyze(image, [], tracker=tracker, at=1, matcher=matcher)
    assert not tracker.update(first, 1)[0].is_locked
    vector[:] = 0
    vector[0] = 1
    next_frame = model.analyze(image, [], tracker=tracker, at=1.1, matcher=matcher)
    track = tracker.update(next_frame, 1.1)[0]
    assert len(calls) == 2 and track.is_locked and track.display_employee_id == "one"
