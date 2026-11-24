"""Door geometry, repeated crossings, hot reload and attendance verification."""

import threading
import time
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from workledger.config import Settings
from workledger.domain import attendance
from workledger.main import create_app
from workledger.models import Camera, DoorCrossing, Employee, SourceEvent
from workledger.vision.door_crossing import DoorCrossingEngine, DoorZoneInput
from workledger.vision.door_service import DoorZoneService
from workledger.vision.runtime import VisionRuntime


def payload(**extra):
    # Test geometry only. Production starts with no coordinates.
    return {
        "expected_version": 0,
        "name": "Test door",
        "active": True,
        "entry_forward": True,
        "anchor": "foot",
        "inertia": 2,
        "stable_ms": 100,
        "max_gap_ms": 750,
        "traversal_ms": 10000,
        "cooldown_ms": 0,
        "verification_wait_ms": 1000,
        "zones": [
            {
                "role": "a",
                "name": "Outside",
                "points": [[0.05, 0.2], [0.3, 0.2], [0.3, 0.8], [0.05, 0.8]],
            },
            {
                "role": "transition",
                "name": "Doorway",
                "points": [[0.35, 0.2], [0.6, 0.2], [0.6, 0.8], [0.35, 0.8]],
            },
            {
                "role": "b",
                "name": "Inside",
                "points": [[0.65, 0.2], [0.9, 0.2], [0.9, 0.8], [0.65, 0.8]],
            },
        ],
        **extra,
    }


def config(**extra):
    return {
        **DoorZoneInput(**payload()).model_dump(),
        "id": "zone",
        "camera_id": "camera",
        "company_id": "company",
        "site_id": "site",
        "version": 1,
        "effective_at": 0,
        **extra,
    }


def obs(x, at, verified=True, **extra):
    return {
        "track_id": 1,
        "foot": [x, 0.5],
        "torso": [x, 0.5],
        "reliable": True,
        "employee_id": "person",
        "attendance_employee_id": "person",
        "attendance_verified_at": at if verified else -1e9,
        "hits": 3,
        "needs_reverification": False,
        **extra,
    }


def feed(engine, cfg, positions, start=1, verified=True):
    events = []
    for index, x in enumerate(positions):
        at = start + index * 0.2
        events += engine.observe(cfg["camera_id"], cfg, [obs(x, at, verified)], at)
    return events


def test_every_entry_exit_cycle_is_recorded():
    engine = DoorCrossingEngine()
    cfg = config()
    events = feed(
        engine,
        cfg,
        [
            0.15,
            0.15,
            0.45,
            0.45,
            0.75,
            0.75,
            0.45,
            0.45,
            0.15,
            0.15,
            0.45,
            0.45,
            0.75,
            0.75,
            0.45,
            0.45,
            0.15,
            0.15,
        ],
    )
    assert [e["kind"] for e in events] == ["entry", "exit", "entry", "exit"]
    assert len({e["id"] for e in events}) == 4 and all(e["status"] == "confirmed" for e in events)


def test_turnback_skipped_transition_and_gap_do_not_create_events():
    engine = DoorCrossingEngine()
    cfg = config()
    assert feed(engine, cfg, [0.15, 0.15, 0.45, 0.45, 0.15, 0.15]) == []
    assert feed(DoorCrossingEngine(), cfg, [0.15, 0.15, 0.75, 0.75]) == []
    engine = DoorCrossingEngine()
    feed(engine, cfg, [0.15, 0.15, 0.45, 0.45])
    assert feed(engine, cfg, [0.75, 0.75], start=5) == []


def test_flip_and_hot_edit_do_not_reinterpret_old_trajectory():
    assert (
        feed(
            DoorCrossingEngine(), config(entry_forward=False), [0.15, 0.15, 0.45, 0.45, 0.75, 0.75]
        )[0]["kind"]
        == "exit"
    )
    engine = DoorCrossingEngine()
    feed(engine, config(), [0.15, 0.15, 0.45, 0.45])
    assert feed(engine, config(version=2), [0.75, 0.75], start=2) == []


def test_predicted_unreliable_anchors_cannot_trigger_and_disable_stops_events():
    engine = DoorCrossingEngine()
    cfg = config()
    for index, x in enumerate([0.15, 0.15, 0.45, 0.45, 0.75, 0.75]):
        at = 1 + index * 0.2
        assert engine.observe("camera", cfg, [obs(x, at, reliable=False)], at) == []
    assert (
        feed(DoorCrossingEngine(), config(active=False), [0.15, 0.15, 0.45, 0.45, 0.75, 0.75]) == []
    )


def test_pending_crossing_verifies_once_or_expires_for_review():
    engine = DoorCrossingEngine()
    cfg = config()
    events = feed(engine, cfg, [0.15, 0.15, 0.45, 0.45, 0.75, 0.75], verified=False)
    assert len(events) == 1 and events[0]["status"] == "pending"
    later = engine.observe("camera", cfg, [obs(0.75, 2.2)], 2.2)
    assert (
        len(later) == 1 and later[0]["id"] == events[0]["id"] and later[0]["status"] == "confirmed"
    )
    assert engine.observe("camera", cfg, [obs(0.75, 2.4)], 2.4) == []
    engine = DoorCrossingEngine()
    events = feed(engine, cfg, [0.15, 0.15, 0.45, 0.45, 0.75, 0.75], verified=False)
    assert engine.observe("camera", cfg, [], 4)[0]["status"] == "review"


def test_geometry_is_normalized_and_not_created_automatically():
    assert DoorZoneService(None).configs == {}
    invalid = payload()
    invalid["zones"][0]["points"][0][0] = 2
    with pytest.raises(ValueError):
        DoorZoneInput(**invalid)
    invalid = payload()
    invalid["zones"][2]["points"] = invalid["zones"][0]["points"]
    with pytest.raises(ValueError):
        DoorZoneInput(**invalid)
    invalid = payload()
    invalid["zones"][2]["role"] = "a"
    with pytest.raises(ValueError):
        DoorZoneInput(**invalid)


@pytest.fixture
def door_client(tmp_path):
    app = create_app(
        Settings(
            database_url="sqlite:///:memory:",
            mode="test",
            local_ai=True,
            local_data_dir=str(tmp_path / "vault"),
            model_dir=str(tmp_path / "models"),
        )
    )
    with TestClient(app, headers={"x-workledger-request": "1"}) as client:
        result = client.post(
            "/api/v1/setup",
            json={
                "company": "Door tests",
                "site": "Office",
                "name": "Admin",
                "email": "door@example.test",
                "password": "test-password-12345",
                "demo": False,
            },
        )
        assert result.status_code == 201
        me = client.get("/api/v1/me").json()
        site = me["sites"][0]["id"]
        company = me["company"]["id"]
        with app.state.session_factory() as db:
            camera = Camera(
                company_id=company, site_id=site, name="Door camera", role="entry", target_fps=2
            )
            person = Employee(
                company_id=company,
                site_id=site,
                name="Person",
                code="DOOR",
                monthly_salary=0,
                overtime_rate=0,
                active=True,
                sample=False,
            )
            db.add_all([camera, person])
            db.commit()
            camera_id = camera.id
            person_id = person.id
        yield client, camera_id, person_id


def test_config_api_hot_reload_version_conflict_and_no_camera_restart(door_client):
    client, camera, person = door_client
    runtime = client.app.state.vision
    runtime.disconnect = lambda *args, **kwargs: pytest.fail("Zone save restarted camera")
    root = f"/api/v1/local/cameras/{camera}/door-zone"
    assert client.get(root).json()["zones"] == []
    first = client.put(root, json=payload())
    assert first.status_code == 200, first.text
    assert first.json()["version"] == 1 and runtime.doors.config(camera)["version"] == 1
    assert client.put(root, json=payload()).status_code == 409
    changed = client.put(root, json=payload(expected_version=1, entry_forward=False))
    assert changed.status_code == 200
    assert runtime.doors.config(camera)["entry_forward"] is False
    assert client.get(root + "/status").json()["saved_version"] == 2


def test_crossing_persistence_is_idempotent_and_does_not_apply_one_hour_suppression(door_client):
    client, camera, person = door_client
    runtime = client.app.state.vision
    assert (
        client.put(f"/api/v1/local/cameras/{camera}/door-zone", json=payload()).status_code == 200
    )
    cfg = runtime.doors.config(camera)
    cfg["effective_at"] = 0
    events = feed(
        DoorCrossingEngine(),
        cfg,
        [0.15, 0.15, 0.45, 0.45, 0.75, 0.75, 0.45, 0.45, 0.15, 0.15, 0.45, 0.45, 0.75, 0.75],
    )
    for event in events:
        event["employee_id"] = person
        event["occurred_at"] += datetime(2026, 11, 23, 3, tzinfo=UTC).timestamp()
        runtime.doors.persist(event)
        runtime.doors.persist(event)
    with client.app.state.session_factory() as db:
        sources = db.scalars(select(SourceEvent).order_by(SourceEvent.occurred_at)).all()
        assert [e.kind for e in sources] == ["entry", "exit", "entry"]
        assert len(db.scalars(select(DoorCrossing)).all()) == 3
    result = client.get(f"/api/v1/local/cameras/{camera}/door-crossings").json()
    assert len(result) == 3 and all(e["status"] == "confirmed" for e in result)


def test_daily_summary_keeps_first_entry_last_exit_and_all_events():
    def event(identifier, kind, hour):
        return SimpleNamespace(
            id=identifier,
            kind=kind,
            occurred_at=datetime(2026, 11, 23, hour, tzinfo=UTC),
            identity_status="confirmed",
            fingerprint=identifier,
        )

    site = SimpleNamespace(timezone="UTC", shift_start="08:00", grace_minutes=0, policy_version=1)
    events = [
        event("1", "entry", 8),
        event("2", "exit", 10),
        event("3", "entry", 11),
        event("4", "exit", 17),
    ]
    result = attendance(events, date(2026, 11, 23), site)
    assert (
        result["first_in"].hour == 8
        and result["last_out"].hour == 17
        and result["event_count"] == 4
    )
    assert result["inside_minutes"] == 480 and result["away_minutes"] == 60


def test_waiting_beside_door_does_not_expire_traversal_origin():
    engine = DoorCrossingEngine()
    cfg = config(traversal_ms=1000)
    assert feed(engine, cfg, [0.15] * 20) == []
    events = feed(engine, cfg, [0.45, 0.45, 0.75, 0.75], start=5)
    assert len(events) == 1 and events[0]["kind"] == "entry"


def test_verification_after_deadline_cannot_confirm_crossing():
    engine = DoorCrossingEngine()
    cfg = config()
    feed(engine, cfg, [0.15, 0.15, 0.45, 0.45, 0.75, 0.75], verified=False)
    events = engine.observe("camera", cfg, [obs(0.75, 4)], 4)
    assert len(events) == 1 and events[0]["status"] == "review"


def test_preview_worker_observes_without_browser_for_all_connected_cameras():
    runtime = VisionRuntime.__new__(VisionRuntime)
    runtime.lock = threading.RLock()
    runtime.stop = threading.Event()
    runtime.settings = SimpleNamespace(visual_tracking_fps=10, confirmation_frames=3)
    seen = threading.Event()
    received = []
    stamp = time.time()

    def accept(camera, observations, at):
        received.append((camera, observations, at))
        seen.set()

    runtime.doors = SimpleNamespace(
        config=lambda camera: {"active": True} if camera == "door" else None,
        observe=accept,
    )
    preview = SimpleNamespace(
        lock=threading.RLock(),
        render=lambda frame, overlay: {"tracking_ms": 1, "people": 1},
        door_observations=lambda at, hits: [obs(0.15, at, required_hits=hits)],
    )
    runtime.preview_trackers = {"door": preview, "other": preview}
    runtime.overlays = {}
    runtime.states = {}
    runtime.latest_frame = lambda camera: {"at": stamp}
    worker = threading.Thread(target=runtime.preview_loop)
    worker.start()
    try:
        assert seen.wait(2), "Door processing depended on a browser request"
    finally:
        runtime.stop.set()
        worker.join(2)
    assert received[0][0] == "door"
    assert received[0][1][0]["required_hits"] == 3
    assert not worker.is_alive()


def test_frame_http_uses_published_pair_without_waiting_for_optical_flow(door_client):
    import base64
    import json

    import cv2
    import numpy as np

    client, camera, _ = door_client
    runtime = client.app.state.vision
    runtime.door_thread = SimpleNamespace(is_alive=lambda: True)
    _, jpeg = cv2.imencode(".jpg", np.zeros((60, 80, 3), np.uint8))
    source = {"at": time.time(), "jpeg": jpeg.tobytes()}
    overlay = {"revision": 7, "geometry": {}}
    result = {
        "labels": [{"name": "Stable employee"}],
        "people": 1,
        "analysis_age_ms": 100,
        "tracking_ms": 2,
    }
    runtime.preview_frames[camera] = (source, overlay, result, [])
    runtime.preview_trackers[camera] = SimpleNamespace(
        render=lambda *args: pytest.fail("HTTP waited for optical flow")
    )
    response = client.get(f"/api/v1/local/cameras/{camera}/frame")
    assert response.status_code == 200 and response.content == source["jpeg"]
    assert float(response.headers["X-Captured-At"]) == source["at"]
    assert json.loads(base64.b64decode(response.headers["X-Workledger-Labels"])) == result["labels"]
    repeat = client.get(
        f"/api/v1/local/cameras/{camera}/frame",
        params={"after": response.headers["X-Preview-Revision"]},
    )
    assert repeat.status_code == 204 and repeat.content == b""
    runtime.preview_frames[camera] = (source, {**overlay, "revision": 8}, result, [])
    assert (
        client.get(
            f"/api/v1/local/cameras/{camera}/frame",
            params={"after": response.headers["X-Preview-Revision"]},
        ).status_code
        == 200
    )
    # Restore the real thread object so the fixture can tear down normally.
    runtime.door_thread = threading.Thread()


def test_background_event_retry_cannot_duplicate_committed_unknown_event(door_client):
    client, camera, _ = door_client
    runtime = client.app.state.vision
    with client.app.state.session_factory() as db:
        row = db.get(Camera, camera)
        cfg = {"company_id": row.company_id, "site_id": row.site_id, "role": row.role}
    snapshot = SimpleNamespace(employee_id=None, score=0.1, latest={})
    identifier = "retry-idempotency-test"
    for _ in range(2):
        assert runtime.write_event(
            camera, cfg, snapshot, time.time(), force_unknown=True, event_id=identifier
        )
    with client.app.state.session_factory() as db:
        assert len(db.scalars(select(SourceEvent).where(SourceEvent.id == identifier)).all()) == 1
