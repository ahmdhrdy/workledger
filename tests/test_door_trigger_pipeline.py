"""Regression coverage for temporary preview IDs and doorway observability."""

import copy

import numpy as np
import pytest
from test_door_zones import config, feed, obs, payload
from test_door_zones import door_client as door_client
from test_preview import analysis, frame, scene

from workledger.vision.door_crossing import DoorCrossingEngine
from workledger.vision.preview import PreviewTracker


def test_temporary_to_authoritative_ids_keep_door_path_without_transferring_identity():
    preview = PreviewTracker()
    source = frame(scene(), 1)
    metadata = analysis(source)
    metadata["persons"][0]["id"] = -1
    preview.render(source, metadata)
    original = preview.door_observations(1, 3)[0]
    assert original["reliable"]
    assert original["employee_id"] is None
    revised = copy.deepcopy(metadata)
    revised["revision"] = 1
    revised["persons"][0]["id"] = 7
    preview.render(source, revised)
    current = preview.door_observations(1, 3)[0]
    assert current["track_id"] == original["track_id"]
    assert current["visual_track_id"] == 7 and current["employee_id"] is None
    later = frame(scene(), 1.2)
    detected = analysis(later)
    detected["persons"][0]["id"] = -2
    preview.render(later, detected)
    assert preview.door_observations(1.2, 3)[0]["track_id"] == original["track_id"]


def test_face_only_detection_never_supplies_a_reliable_foot_anchor():
    preview = PreviewTracker()
    source = frame(scene(), 1)
    metadata = analysis(source)
    metadata["persons"][0]["person_detected"] = False
    preview.render(source, metadata)
    assert not preview.door_observations(1, 3)[0]["reliable"]


def test_ambiguous_identity_geometry_is_recorded_for_review_not_attendance():
    engine = DoorCrossingEngine()
    cfg = config()
    events = []
    for index, x in enumerate([0.15, 0.15, 0.45, 0.45, 0.75, 0.75]):
        at = 1 + index * 0.2
        events += engine.observe("camera", cfg, [obs(x, at, identity_reliable=False)], at)
    assert len(events) == 1 and events[0]["status"] == "review"
    assert "ambiguous" in events[0]["reason"]


def test_status_reports_outside_anchor_instead_of_silent_empty_logs():
    engine = DoorCrossingEngine()
    cfg = config()
    engine.observe("camera", cfg, [obs(0.99, 1)], 1)
    trace = engine.diagnostics["camera"]["tracks"]["1"]
    assert trace["reason"] == "outside_zones" and trace["point"] == [0.99, 0.5]


def test_ambiguous_body_pairs_cannot_share_door_trajectory():
    preview = PreviewTracker()

    def entity(identifier):
        return {
            "box": [10, 10, 80, 100],
            "body_tracked_at": 1,
            "door_track_id": identifier,
            "label": {},
        }

    previous = {1: entity("one"), 2: entity("two")}
    preview.entities = {-1: entity("new")}
    preview._carry_door_ids(previous, 1.1)
    assert preview.entities[-1]["door_track_id"] == "new"


@pytest.mark.parametrize("role", ["entry", "exit", "interior"])
def test_complete_crossing_is_persisted_for_every_camera_role(door_client, role):
    from sqlalchemy import select

    from workledger.models import Camera, DoorCrossing, SourceEvent

    client, camera, person = door_client
    runtime = client.app.state.vision
    with client.app.state.session_factory() as db:
        db.get(Camera, camera).role = role
        db.commit()
    root = f"/api/v1/local/cameras/{camera}"
    assert client.put(root + "/door-zone", json=payload()).status_code == 200
    cfg = runtime.doors.config(camera)
    cfg["effective_at"] = 0
    for event in feed(DoorCrossingEngine(), cfg, [0.15, 0.15, 0.45, 0.45, 0.75, 0.75]):
        event["employee_id"] = person
        event["confidence"] = 0.61
        runtime.doors.persist(event)
    rows = client.get(root + "/door-crossings").json()
    assert len(rows) == 1 and rows[0]["status"] == "confirmed"
    assert rows[0]["camera_name"] == "Door camera" and rows[0]["zone_name"] == "Test door"
    assert rows[0]["confidence"] == 0.61 and rows[0]["employee_id"] == person
    with client.app.state.session_factory() as db:
        assert len(db.scalars(select(DoorCrossing)).all()) == 1
        assert len(db.scalars(select(SourceEvent)).all()) == 1


@pytest.mark.parametrize("recognized", [True, False])
def test_preview_membership_and_confirmation_survive_provisional_id_churn(recognized):
    preview = PreviewTracker()
    engine = DoorCrossingEngine()
    cfg = config(verification_wait_ms=5000)
    events = []
    for index, x in enumerate(np.linspace(0.15, 0.78, 36)):
        at = 1 + index * 0.2
        image = np.zeros((360, 640, 3), np.uint8)
        center = round(x * 640)
        image[80:250, center - 35 : center + 35] = np.random.default_rng(7).integers(
            0, 256, (170, 70, 3), dtype=np.uint8
        )
        source = frame(image, at)
        label = {
            "locked": True,
            "employee_id": "person",
            "score": 0.61,
            "attendance_proof": {
                "attendance_employee_id": "person",
                "attendance_verified_at": at,
                "hits": 3,
                "needs_reverification": False,
                "association_reliable": True,
            },
        }
        metadata = {
            "at": at,
            "source_jpeg": source["jpeg"],
            "revision": index,
            "persons": [
                {
                    "id": -(index + 1) if index % 2 else index + 1,
                    "person_detected": True,
                    "box": [center - 35, 80, center + 35, 250],
                    "face_box": [center - 20, 85, center + 20, 130],
                    "label": label if recognized else {},
                }
            ],
        }
        preview.render(source, metadata)
        events += engine.observe("camera", cfg, preview.door_observations(at, 3), at)
    assert len(events) == 1
    if recognized:
        assert events[0]["status"] == "confirmed"
    else:
        assert events[0]["status"] == "pending"
        metadata = copy.deepcopy(metadata)
        metadata["revision"] += 1
        metadata["persons"][0]["id"] = 99
        metadata["persons"][0]["label"] = label
        preview.render(source, metadata)
        verified = engine.observe("camera", cfg, preview.door_observations(at, 3), at)
        assert len(verified) == 1 and verified[0]["id"] == events[0]["id"]
        assert verified[0]["status"] == "confirmed"
