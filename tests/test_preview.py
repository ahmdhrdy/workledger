"""Regression tests for geometry synchronized to displayed video frames."""

import threading
import time
from types import SimpleNamespace

import cv2
import numpy as np

from workledger.config import Settings
from workledger.vision.inference import Inference
from workledger.vision.pose_matcher import PoseAdaptiveMatcher
from workledger.vision.preview import PreviewTracker
from workledger.vision.tracking import Tracker


def frame(image, at):
    okay, jpeg = cv2.imencode(".jpg", image)
    assert okay
    return {"jpeg": jpeg.tobytes(), "at": at}


def scene():
    image = np.zeros((360, 640, 3), dtype=np.uint8)
    image[60:260, 100:220] = np.random.default_rng(123).integers(
        0, 256, (200, 120, 3), dtype=np.uint8
    )
    return image


def analysis(source, persons=None):
    return {
        "source_jpeg": source["jpeg"],
        "at": source["at"],
        "persons": persons
        if persons is not None
        else [
            {
                "id": 1,
                "box": [100, 60, 220, 260],
                "face_box": [120, 80, 190, 150],
                "label": {
                    "name": "Employee",
                    "identity_state": "current",
                    "identity_expires_at": time.time() + 2,
                    "confirmed": True,
                },
            }
        ],
    }


def test_late_analysis_replays_frame_history():
    tracker = PreviewTracker()
    image = scene()
    source = frame(image, 10)
    latest = source
    for i in range(10):
        moved = cv2.warpAffine(image, np.float32([[1, 0, i * 4], [0, 1, 0]]), (640, 360))
        latest = frame(moved, 10 + i * 0.1)
        tracker.render(latest, None)
    result = tracker.render(latest, analysis(source))
    assert result["people"] == 1 and len(result["labels"]) == 1
    assert abs(result["labels"][0]["x"] * 640 - (120 + 36)) < 3
    assert result["analysis_age_ms"] == 900


def test_head_moves_independently_of_body():
    tracker = PreviewTracker()
    image = scene()
    source = frame(image, 10)
    tracker.render(source, analysis(source))
    moved = image.copy()
    moved[80:150, 120:190] = 0
    moved[80:150, 128:198] = image[80:150, 120:190]
    result = tracker.render(frame(moved, 10.1), analysis(source))
    assert len(result["labels"]) == 1
    assert abs(result["labels"][0]["x"] * 640 - 128) < 3


def test_people_without_faces_count_and_empty_scene_resets():
    tracker = PreviewTracker()
    source = frame(scene(), 10)
    people = [{"id": 1, "box": [100, 60, 220, 260], "face_box": None, "label": {}}]
    result = tracker.render(source, analysis(source, people))
    assert result["people"] == 1 and result["labels"] == []
    newer = frame(np.zeros_like(scene()), 10.5)
    assert tracker.render(newer, analysis(newer, []))["people"] == 0
    stale = tracker.render(frame(scene(), 16), analysis(newer, []))
    assert stale["people"] == -1


def test_expired_identity_loses_name_but_keeps_face_geometry():
    tracker = PreviewTracker()
    source = frame(scene(), time.time())
    result = analysis(source)
    result["persons"][0]["label"].update(
        identity_state="tracking",
        identity_expires_at=time.time() - 1,
    )
    output = tracker.render(source, result)
    assert len(output["labels"]) == 1 and output["labels"][0]["name"] == "Unknown"


def test_short_face_detection_gap_preserves_tracked_face():
    tracker = PreviewTracker()
    source = frame(scene(), 10)
    tracker.render(source, analysis(source))
    newer = frame(scene(), 10.6)
    people = [{"id": 1, "box": [100, 60, 220, 260], "face_box": None, "label": {}}]
    result = tracker.render(newer, analysis(newer, people))
    assert len(result["labels"]) == 1 and result["people"] == 1


def scheduler():
    model = object.__new__(Inference)
    model.settings = Settings()
    model.lock = threading.RLock()
    model._people = lambda frame: []
    vector = np.zeros(512, dtype=np.float32)
    vector[0] = 1
    calls = []

    def embedding(crop):
        calls.append(1)
        return vector.copy()

    model.engine = SimpleNamespace(
        detect_faces=lambda frame: [(np.array([10.0, 10.0, 100.0, 100.0]), 0.9, np.zeros((5, 2)))],
        preprocess_and_align=lambda *args: (
            np.zeros((112, 112, 3)),
            {"yaw_ratio": 0.9, "quality": 100},
        ),
        get_embedding=embedding,
    )
    return model, vector, calls


def test_scheduler_confirms_before_caching_and_throttles_only_failed_matches():
    model, vector, calls = scheduler()
    model.settings.face_verify_seconds = 1
    tracker = Tracker()
    matcher = PoseAdaptiveMatcher(["employee"], [vector])
    image = np.zeros((120, 120, 3), np.uint8)
    for at in (1, 1.1, 1.2):
        people = model.analyze(image, [], tracker=tracker, at=at, matcher=matcher)
        track = tracker.update(people, at)[0]
    assert len(calls) == 3 and track.hits == 3
    people = model.analyze(image, [], tracker=tracker, at=1.3, matcher=matcher)
    track = tracker.update(people, 1.3)[0]
    assert len(calls) == 3 and track.method == "face_cached"
    vector[:] = 0
    vector[1] = 1
    people = model.analyze(image, [], tracker=tracker, at=2.5, matcher=matcher)
    tracker.update(people, 2.5)
    assert len(calls) == 4 and people[0]["employee_id"] is None
    assert track.is_locked and track.employee_id == "employee"
    people = model.analyze(image, [], tracker=tracker, at=2.7, matcher=matcher)
    tracker.update(people, 2.7)
    assert len(calls) == 4 and people[0]["employee_id"] is None
    model.analyze(image, [], tracker=tracker, at=3.6, matcher=matcher)
    assert len(calls) == 5


def test_empty_gallery_skips_embeddings():
    model, _, calls = scheduler()
    model.analyze(np.zeros((120, 120, 3), np.uint8), [])
    assert calls == [] and model.timings["embeddings_computed"] == 0


def test_body_features_are_sampled_per_track():
    model, _, _ = scheduler()
    model.engine.detect_faces = lambda frame: []
    model._people = lambda frame: [
        {
            "box": [10, 10, 90, 100],
            "body": None,
            "person_detected": True,
            "employee_id": None,
            "method": "unknown",
            "score": 0,
            "face_vector": None,
        }
    ]
    calls = []

    class ReID:
        def __call__(self, values):
            calls.append(1)
            return {"body": np.ones((1, 256), np.float32)}

        def output(self, index):
            return "body"

    model.reid_model = ReID()
    image = np.zeros((120, 120, 3), np.uint8)
    tracker = Tracker()
    for at in (1, 1.2, 1.4):
        people = model.analyze(image, [], tracker=tracker, at=at)
        tracker.update(people, at)
    assert len(calls) == 1
    model.analyze(image, [], tracker=tracker, at=3.5)
    assert len(calls) == 2


def test_person_count_bridges_one_detection_miss_with_fresh_flow():
    tracker = PreviewTracker()
    source = frame(scene(), 10)
    tracker.render(source, analysis(source))
    newer = frame(scene(), 10.5)
    result = tracker.render(newer, analysis(newer, []))
    assert result["people"] == 1
    expired = frame(scene(), 12)
    assert tracker.render(expired, analysis(newer, []))["people"] == 1
    gone = frame(np.zeros_like(scene()), 12.1)
    tracker.render(gone, analysis(newer, []))
    assert tracker.render(frame(np.zeros_like(scene()), 13), analysis(newer, []))["people"] == 0
