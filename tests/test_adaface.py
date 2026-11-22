"""Regression coverage for AdaFace preprocessing, galleries and cached evidence."""

import base64
import json
from types import SimpleNamespace

import cv2
import numpy as np
from fastapi.testclient import TestClient

from workledger.config import Settings
from workledger.main import create_app
from workledger.vision.face_engine import FaceEngine
from workledger.vision.gallery_index import GalleryIndex
from workledger.vision.pose_matcher import PoseAdaptiveMatcher
from workledger.vision.store import LocalStore
from workledger.vision.tracking import Tracker, portal_ready


def vector(first=1.0):
    result = np.zeros(512, dtype=np.float32)
    result[:2] = [first, np.sqrt(max(0, 1 - first * first))]
    return result


def test_input_preserves_bgr_and_normalizes():
    image = np.zeros((112, 112, 3), dtype=np.uint8)
    image[:] = [0, 127, 255]
    blob = FaceEngine.input_blob(image)
    assert blob.shape == (1, 3, 112, 112)
    assert blob.dtype == np.float32 and blob.flags.c_contiguous
    assert blob[0, 0, 0, 0] == -1
    assert blob[0, 2, 0, 0] == 1


def test_pose_threshold_and_employee_margin():
    matcher = PoseAdaptiveMatcher(["one"], [vector()])
    assert matcher.match(vector(0.4), 0.8)[0] is None
    assert matcher.match(vector(0.4), 0.3)[0] == "one"
    ambiguous = PoseAdaptiveMatcher(["one", "two"], [vector(), vector()])
    assert ambiguous.match(vector(), 1)[0] is None
    assert PoseAdaptiveMatcher([], np.empty((0, 512))).match(vector(), 1)[0] is None


def test_gallery_generation_scope_and_deletion(tmp_path):
    settings = Settings()
    gallery = GalleryIndex(tmp_path, "adaface-test", settings)
    sample = {
        "id": "photo",
        "employee_id": "person",
        "vector": vector().tolist(),
        "augmentations": [{"kind": "horizontal_flip", "vector": vector(0.9).tolist()}],
    }
    first = gallery.update("company", "site", [sample])
    assert first["samples"] == 1 and first["embeddings"] == 2
    path = tmp_path / "adaface/company/site/generations" / first["version"]
    assert np.load(path / "emp_embeddings.npy", allow_pickle=False).shape == (2, 512)
    assert json.loads((path / "manifest.json").read_text())["model_id"] == "adaface-test"
    assert gallery.update("company", "site", [sample])["version"] == first["version"]
    cold = GalleryIndex(tmp_path, "adaface-test", settings)
    assert cold.update("company", "site", [sample])["version"] == first["version"]
    assert gallery.update("company", "other-site", [])["embeddings"] == 0
    removed = gallery.update("company", "site", [])
    assert removed["version"] != first["version"]
    assert removed["matcher"].match(vector(), 1)[0] is None


def test_legacy_model_exclusion_and_sample_variants(tmp_path):
    old = LocalStore(tmp_path, model_id="arcface-old")
    old.add_template("old", "company", "person", "uncovered", vector().tolist())
    new = LocalStore(tmp_path, model_id="adaface-new")
    assert new.templates("company") == []
    assert len(new.templates("company", include_legacy=True)) == 1
    metadata = {
        "sha256": "same-photo",
        "thumbnail": "original",
        "augmentations": [
            {"kind": "horizontal_flip", "vector": vector().tolist(), "thumbnail": "flip"}
        ],
    }
    assert (
        new.add_template("new", "company", "person", "uncovered", vector().tolist(), metadata)
        == "new"
    )
    assert (
        new.add_template("duplicate", "company", "person", "uncovered", vector().tolist(), metadata)
        == "new"
    )
    assert len(new.templates("company")) == 1
    assert new.template_preview("company", "person", "new", "horizontal_flip") == "flip"
    assert new.template_preview("other-company", "person", "new") is None
    assert new.remove_template("company", "person", "new")
    assert not new.templates("company")


def test_cached_match_does_not_refresh_evidence_but_recent_lock_can_trigger():
    tracker = Tracker()

    def observation(cached=False):
        return {
            "box": [10, 10, 90, 200],
            "employee_id": "one",
            "method": "face_cached" if cached else "face",
            "score": 0.9,
            "face_vector": None if cached else vector(),
            "recognition_skipped": cached,
        }

    for at in (1, 1.1, 1.2):
        people = [observation()]
        tracker.associate(people, at)
        track = tracker.update(people, at)[0]
    assert track.hits == 3 and track.display_employee_id == "one"
    people = [observation(True)]
    tracker.associate(people, 1.3)
    cached = tracker.update(people, 1.3)[0]
    assert cached.hits == 3 and cached.verified_at == 1.2
    assert portal_ready(cached, {"trigger": "presence"}, (480, 640), 1.3, 3)
    assert not portal_ready(cached, {"trigger": "presence"}, (480, 640), 6.3, 3)
    people = [observation()]
    tracker.associate(people, 2)
    fresh = tracker.update(people, 2)[0]
    assert fresh.hits == 4 and fresh.verified_at == 2
    assert portal_ready(fresh, {"trigger": "presence"}, (480, 640), 2, 3)


def test_http_enrollment_build_review_and_delete(tmp_path):
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
        setup = client.post(
            "/api/v1/setup",
            json={
                "company": "Test",
                "site": "Office",
                "name": "Admin",
                "email": "test@example.test",
                "password": "test-only-password-123",
                "demo": False,
            },
        )
        assert setup.status_code == 201
        site = client.get("/api/v1/me").json()["sites"][0]["id"]
        _, encoded = cv2.imencode(".jpg", np.zeros((100, 100, 3), np.uint8))
        thumbnail = base64.b64encode(encoded.tobytes()).decode()

        def enroll(image, augment_masked=False):
            variants = [
                {"kind": "horizontal_flip", "vector": vector().tolist(), "thumbnail": thumbnail}
            ]
            if augment_masked:
                variants.append(
                    {"kind": "synthetic_mask", "vector": vector().tolist(), "thumbnail": thumbnail}
                )
            return {
                "vector": vector(),
                "quality": 123,
                "detection_score": 0.79,
                "augmentations": variants,
            }

        app.state.vision.load_model = lambda: SimpleNamespace(enrollment=enroll)
        profile = {
            "site_id": site,
            "name": "Person",
            "code": "ONE",
            "nickname": "Person",
            "designation": "Engineer",
            "department": "Office",
            "monthly_salary": "25000",
            "overtime_rate": "100",
        }
        registered = client.post(
            "/api/v1/local/employees/register",
            data={"profile": json.dumps(profile)},
            files={"image": ("photo.jpg", encoded.tobytes())},
        )
        assert registered.status_code == 201, registered.text
        employee = registered.json()["id"]
        base = f"/api/v1/local/employees/{employee}"
        result = client.post(
            base + "/enroll",
            data={"augment_masked": "true"},
            files={"image": ("photo.jpg", encoded.tobytes())},
        )
        assert result.status_code == 200, result.text
        assert result.json()["duplicate"] and result.json()["gallery"]["embeddings"] == 3
        sample = result.json()["sample_id"]
        listing = client.get("/api/v1/local/enrollments", params={"site_id": site})
        assert "vector" not in listing.text
        assert len(listing.json()[0]["samples"][0]["augmentations"]) == 2
        preview = client.get(
            base + f"/enrollment/{sample}/image", params={"augmentation": "synthetic_mask"}
        )
        assert preview.status_code == 200 and preview.headers["content-type"] == "image/jpeg"
        removed = client.delete(base + f"/enrollment/{sample}")
        assert removed.status_code == 200 and removed.json()["gallery"]["embeddings"] == 0
        assert client.get(base + f"/enrollment/{sample}/image").status_code == 404
