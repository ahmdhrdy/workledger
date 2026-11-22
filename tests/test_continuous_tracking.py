"""Regression for five-second flashing and score-driven track churn."""

import threading
import time
from types import SimpleNamespace

import cv2
import numpy as np
from test_preview import analysis, frame, scene
from test_sticky_identity import lock, observation

from workledger.vision.preview import PreviewTracker
from workledger.vision.runtime import VisionRuntime
from workledger.vision.tracking import Tracker


def locked_analysis(source):
    metadata = analysis(source)
    metadata["persons"][0]["label"].update(locked=True, identity_expires_at=0)
    return metadata


def test_slow_analysis_does_not_hide_visually_tracked_locked_face():
    preview = PreviewTracker()
    image = scene()
    source = frame(image, 10)
    metadata = locked_analysis(source)
    count = []
    for i in range(71):
        moved = cv2.warpAffine(image, np.float32([[1, 0, i * 0.4], [0, 1, 0]]), (640, 360))
        result = preview.render(frame(moved, 10 + i * 0.1), metadata)
        count.append(result["people"])
        assert result["labels"][0]["name"] == "Employee" and result["labels"][0]["locked"]
        assert result["labels"][0]["width"] > 0
    assert set(count) == {1} and result["analysis_age_ms"] == 7000
    assert abs(result["labels"][0]["x"] * 640 - (120 + 28)) < 5


def test_lost_visual_evidence_drops_geometry_even_if_identity_is_locked():
    preview = PreviewTracker()
    source = frame(scene(), 10)
    metadata = locked_analysis(source)
    preview.render(source, metadata)
    for index in range(1, 15):
        result = preview.render(frame(np.zeros_like(scene()), 10 + index * 0.1), metadata)
    assert not result["labels"]


def test_brief_head_flow_failure_uses_bounded_prediction():
    image = scene()
    image[80:150, 120:190] = 128
    preview = PreviewTracker()
    source = frame(image, 10)
    metadata = locked_analysis(source)
    preview.render(source, metadata)
    for i in range(1, 8):
        result = preview.render(frame(image, 10 + i * 0.1), metadata)
    assert result["labels"][0]["width"] > 0
    assert result["labels"][0]["tracking_state"] == "PREDICTED_HEAD"
    for i in range(8, 19):
        result = preview.render(frame(image, 10 + i * 0.1), metadata)
    assert result["labels"][0]["width"] == 0 and result["labels"][0]["name"] == "Employee"


def test_unobserved_gap_does_not_claim_visual_continuity():
    preview = PreviewTracker()
    source = frame(scene(), 10)
    metadata = locked_analysis(source)
    preview.render(source, metadata)
    result = preview.render(frame(scene(), 16), metadata)
    assert result["labels"][0]["name"] == "Employee"
    assert preview.hints_at(16)[1]["continuity_since"] == 16


def test_hints_are_pinned_to_past_source_frame_not_future_geometry():
    preview = PreviewTracker()
    source = frame(scene(), 10)
    metadata = locked_analysis(source)
    preview.render(source, metadata)
    preview.render(frame(scene(), 10.1), metadata)
    assert preview.hints_at(10.05)[1]["at"] == 10
    assert preview.hints_at(9.9) == {} and preview.hints_at(11) == {}


def test_continuous_visual_evidence_preserves_id_across_slow_detections_and_low_scores():
    tracker = Tracker()
    track = lock(tracker)
    for at, score in ((4, 0.45), (7, 0.44), (10, 0.2), (13, 0)):
        people = [observation(None, score=score, face_vector=np.ones(512))]
        hints = {
            track.id: {
                "at": at,
                "visual_at": at,
                "continuity_since": 1,
                "detected_at": track.seen,
                "box": track.box,
            }
        }
        tracker.associate(people, at, hints=hints)
        current = tracker.update(people, at)[0]
        assert current.id == track.id and current.employee_id == "one" and current.is_locked
        assert not current.recovered and not current.needs_reverification
        assert current.transition_reason == "LOCK_HELD_LOW_MATCH"
        assert current.verified_at == 1.4


def test_stale_visual_hint_cannot_transfer_a_lock_to_replacement_person():
    tracker = Tracker()
    old = lock(tracker)
    hints = {
        old.id: {"at": 4, "visual_at": 2, "continuity_since": 1, "detected_at": 1.4, "box": old.box}
    }
    people = [observation(None)]
    tracker.associate(people, 4, hints=hints)
    new = tracker.update(people, 4)[0]
    assert new.id != old.id and not new.is_locked


def test_visual_worker_updates_without_browser_requests_or_model_inference():
    runtime = VisionRuntime.__new__(VisionRuntime)
    runtime.lock = threading.RLock()
    runtime.stop = threading.Event()
    runtime.settings = SimpleNamespace(visual_tracking_fps=15)
    source = frame(scene(), time.time())
    preview = PreviewTracker()
    runtime.preview_trackers = {"camera": preview}
    runtime.overlays = {"camera": locked_analysis(source)}
    runtime.states = {"camera": {}}
    runtime.latest_frame = lambda identifier: source
    worker = threading.Thread(target=runtime.preview_loop)
    worker.start()
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not runtime.states["camera"].get(
            "visual_tracking_at"
        ):
            time.sleep(0.01)
        assert runtime.states["camera"]["visual_people"] == 1
        assert preview.hints_at(source["at"])
    finally:
        runtime.stop.set()
        worker.join(timeout=2)
    assert not worker.is_alive()


def test_duplicate_face_claim_does_not_erase_geometrically_locked_name():
    tracker = Tracker()
    old = lock(tracker)
    people = [observation(None, identity_conflict=True, face_vector=np.ones(512))]
    tracker.associate(people, 2)
    track = tracker.update(people, 2)[0]
    assert track.id == old.id and track.employee_id == "one" and track.display_employee_id == "one"
    assert track.association_reliable and track.needs_reverification


def test_new_candidate_cannot_steal_visible_locked_employee():
    tracker = Tracker()
    old = lock(tracker)
    for at in (2, 2.2, 2.4, 2.6):
        people = [observation(None), observation("one", box=[350, 50, 450, 250])]
        tracker.associate(people, at)
        tracks = tracker.update(people, at)
    assert tracks[0].id == old.id and tracks[0].is_locked
    assert not tracks[1].is_locked


def test_new_detection_corrects_stale_optical_flow_face_position():
    preview = PreviewTracker()
    source = frame(scene(), 10)
    metadata = locked_analysis(source)
    preview.render(source, metadata)
    for index in range(1, 10):
        preview.render(frame(scene(), 10 + index * 0.1), metadata)
    updated = frame(scene(), 11)
    new_metadata = locked_analysis(updated)
    new_metadata["persons"][0]["face_box"] = [140, 80, 210, 150]
    result = preview.render(updated, new_metadata)
    assert abs(result["labels"][0]["x"] * 640 - 140) < 1
    assert result["labels"][0]["name"] == "Employee" and result["labels"][0]["locked"]
