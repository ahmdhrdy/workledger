import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from workledger.config import Settings
from workledger.main import create_app
from workledger.models import SourceEvent
from workledger.vision.inference import rank
from workledger.vision.onvif import rtsp_url, validate_host
from workledger.vision.store import LocalStore
from workledger.vision.timing import FrameCadence
from workledger.vision.tracking import BodyGallery, Tracker, portal_ready


@pytest.mark.parametrize("source_fps, expected", [(12, 10), (25, 10), (5, 5)])
def test_capture_preserves_target_rate_for_uneven_source_frames(source_fps, expected):
    cadence = FrameCadence()
    published = [
        index for index in range(source_fps) if cadence.ready(100 + index / source_fps, 10)
    ]
    assert len(published) == expected


def test_capture_resumes_after_stall_without_publishing_a_burst():
    cadence = FrameCadence()
    assert cadence.ready(100, 10)
    assert not cadence.ready(100.01, 10)
    assert cadence.ready(110, 10)
    assert not cadence.ready(110.01, 10)
    assert cadence.ready(110.1, 10)


def test_vault_encrypts_credentials_and_biometrics_and_persists(tmp_path):
    store = LocalStore(tmp_path)
    store.put_camera("camera", {"password": "CAMERA-secret-never-log", "host": "192.168.0.10"})
    store.add_template("sample", "company", "employee", "masked", [1.0, 0.0, 0.0])
    assert b"CAMERA-secret-never-log" not in (tmp_path / "local.db").read_bytes()
    store = LocalStore(tmp_path)
    assert store.camera("camera")["password"] == "CAMERA-secret-never-log"
    assert store.templates("other-company") == []
    assert store.templates("company")[0]["vector"] == [1.0, 0.0, 0.0]
    store.remove_templates("other-company", "employee")
    assert len(store.templates("company")) == 1
    store.put_evidence("event", "company", "camera", 100, b"encrypted-local-jpeg")
    assert store.evidence("event", "other-company") is None
    assert store.evidence("event", "company") == b"encrypted-local-jpeg"
    store.purge_evidence(["other-camera"], 101)
    assert store.evidence("event", "company") is not None
    store.purge_evidence(["camera"], 101)
    assert store.evidence("event", "company") is None


def test_match_requires_identity_margin_and_valid_vector():
    vector = [1.0, 0.0, 0.0]
    assert rank(vector, [], 0.55, 0.08)[0] is None
    samples = [{"employee_id": "one", "vector": vector}]
    assert rank(vector, samples, 0.55, 0.08)[0] == "one"
    samples.append({"employee_id": "two", "vector": [0.99, 0.01, 0.0]})
    assert rank(vector, samples, 0.55, 0.08)[0] is None
    with pytest.raises(ValueError):
        rank([float("nan"), 0, 0], samples, 0.55, 0.08)


def test_body_gallery_expires_and_is_site_scoped():
    gallery = BodyGallery()
    gallery.add("site-one", "employee", "entry", [1.0, 0.0], 100)
    assert gallery.match("site-one", [1.0, 0.0], 101, 0.82, 0.08)[0] == "employee"
    assert gallery.match("site-two", [1.0, 0.0], 101, 0.82, 0.08)[0] is None
    assert gallery.match("site-one", [1.0, 0.0], 401, 0.82, 0.08)[0] is None


def test_tracking_resets_confirmation_when_identity_method_changes():
    tracker = Tracker()
    person = {
        "box": [10, 10, 90, 200],
        "employee_id": "one",
        "method": "body_tentative",
        "score": 0.9,
    }
    assert tracker.update([person], 1)[0].hits == 1
    assert tracker.update([person], 2)[0].hits == 2
    person = {**person, "method": "face"}
    assert tracker.update([person], 2.5)[0].hits == 1
    track = tracker.update([person], 3)[0]
    assert not portal_ready(track, {"trigger": "presence"}, (480, 640), 3, 3)
    track = tracker.update([person], 3.5)[0]
    assert portal_ready(track, {"trigger": "presence"}, (480, 640), 3.5, 3)


def test_line_trigger_needs_crossing_in_configured_direction():
    track = SimpleNamespace(
        box=[100, 0, 200, 200], hits=3, last_side=None, crossing_at=0, method="face"
    )
    config = {
        "trigger": "line",
        "line_axis": "vertical",
        "line_position": 0.5,
        "line_direction": "positive",
    }
    assert not portal_ready(track, config, (480, 640), 10, 3)
    track.box = [320, 0, 420, 200]
    assert portal_ready(track, config, (480, 640), 11, 3)
    assert not portal_ready(track, config, (480, 640), 16, 3)


def test_camera_address_and_credential_encoding():
    assert validate_host("192.168.0.9") == "192.168.0.9"
    for host in ["127.0.0.1", "8.8.8.8", "169.254.169.254", "https://192.168.0.9", "0.0.0.0"]:
        with pytest.raises(ValueError):
            validate_host(host)
    value = rtsp_url(
        {"host": "192.168.0.9", "path": "/stream1", "username": "user@site", "password": "p:a@ss#?"}
    )
    assert "user%40site:p%3Aa%40ss%23%3F@" in value


class FakeDecoder:
    def __init__(self, config):
        self.config = config
        self.process = SimpleNamespace(is_alive=lambda: True)

    def close(self):
        pass

    def poll(self):
        return None


@pytest.fixture
def local_client(tmp_path, monkeypatch):
    monkeypatch.setattr("workledger.vision.runtime.Decoder", FakeDecoder)
    app = create_app(
        Settings(
            database_url="sqlite:///:memory:",
            mode="test",
            local_ai=True,
            local_data_dir=str(tmp_path / "vault"),
            model_dir=".local/models",
        )
    )
    with TestClient(app, headers={"x-workledger-request": "1"}) as client:
        assert (
            client.post(
                "/api/v1/setup",
                json={
                    "company": "Vision tests",
                    "site": "Dhaka",
                    "name": "Admin",
                    "email": "vision@example.test",
                    "password": "test-password-only-123",
                    "demo": True,
                },
            ).status_code
            == 201
        )
        yield client


def test_camera_onboard_role_secrets_preview_and_stop(local_client):
    client = local_client
    site = client.get("/api/v1/me").json()["sites"][0]["id"]
    payload = {
        "site_id": site,
        "name": "Real workplace",
        "role": "interior",
        "host": "192.168.0.10",
        "username": "admin",
        "password": "do-not-return",
        "connection": "manual",
        "path": "/stream1",
        "zone_name": "Meeting room",
        "zone_type": "meeting",
    }
    response = client.post("/api/v1/local/cameras", json=payload)
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    for _ in range(50):
        if identifier in client.app.state.vision.workers:
            break
        time.sleep(0.01)
    assert identifier in client.app.state.vision.workers
    listed = client.get("/api/v1/local/cameras", params={"site_id": site})
    assert "do-not-return" not in listed.text
    camera = next(c for c in listed.json() if c["id"] == identifier)
    assert camera["role"] == "interior" and camera["has_password"]
    _, jpeg = cv2.imencode(".jpg", np.zeros((100, 100, 3), np.uint8))
    client.app.state.vision.frames[identifier] = {"jpeg": jpeg.tobytes(), "at": time.time()}
    response = client.get(f"/api/v1/local/cameras/{identifier}/frame")
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    assert (
        client.post(
            f"/api/v1/local/cameras/{identifier}/action", json={"action": "stop"}
        ).status_code
        == 200
    )
    assert client.get(f"/api/v1/local/cameras/{identifier}/frame").status_code == 503
    remote = TestClient(client.app, client=("192.168.0.20", 5000))
    assert remote.get("/api/v1/local/status").status_code == 403


def test_unknown_review_preserves_source_and_retry_dedup(local_client):
    from test_api import event, provision, scope

    client = local_client
    workspace = client.get("/api/v1/me").json()
    headers = provision(client, workspace)
    source = event(client, workspace)
    assert (
        client.post(
            "/agent/v1/events:batch", headers=headers, json={"events": [source]}
        ).status_code
        == 200
    )
    employee = client.get(
        "/api/v1/employees", params={"site_id": scope(workspace)["site_id"]}
    ).json()[-1]
    response = client.post(
        f"/api/v1/local/events/{source['event_id']}/resolve",
        json={"employee_id": employee["id"], "reason": "Reviewed local camera evidence"},
    )
    assert response.status_code == 200, response.text
    assert client.post("/agent/v1/events:batch", headers=headers, json={"events": [source]}).json()[
        "duplicates"
    ] == [source["event_id"]]
    with client.app.state.session_factory() as db:
        assert db.get(SourceEvent, source["event_id"]).employee_id is None
    events = client.get("/api/v1/events", params=scope(workspace)).json()
    assert next(e for e in events if e["id"] == source["event_id"])["employee_id"] == employee["id"]
