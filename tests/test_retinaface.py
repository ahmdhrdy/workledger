"""Known-coordinate decoder tests independent of ONNX inference."""

import numpy as np
import pytest

from workledger.vision.pgvector_gallery import PgVectorMatcher
from workledger.vision.pose_matcher import PoseAdaptiveMatcher
from workledger.vision.retinaface import RetinaFaceONNX
from workledger.vision.tracking import Tracker


def detector():
    model = RetinaFaceONNX.__new__(RetinaFaceONNX)
    model.width = model.height = 640
    model.priors = model.generate_priors(640, 640)
    return model


def outputs(model):
    n = len(model.priors)
    loc = np.zeros((1, n, 4), np.float32)
    conf = np.zeros((1, n, 2), np.float32)
    conf[:, :, 0] = 1
    land = np.zeros((1, n, 10), np.float32)
    return loc, conf, land


def test_retinaface_priors_and_native_bgr_mean():
    model = detector()
    assert model.priors.shape == (16800, 4)
    image = np.empty((720, 1280, 3), np.uint8)
    image[:] = [104, 117, 123]
    blob, scales = model.preprocess(image)
    assert blob.shape == (1, 3, 640, 640) and blob.dtype == np.float32
    assert np.array_equal(blob[0, :, 0, 0], [0, 0, 0])
    assert np.array_equal(blob[0, :, -1, -1], [-104, -117, -123])
    assert scales == (0.5, 0.5)


def test_zero_deltas_decode_exact_anchor_and_landmarks():
    model = detector()
    loc, conf, kps = outputs(model)
    index = (30 * 80 + 40) * 2
    conf[0, index] = [0.1, 0.9]
    faces = model.postprocess(loc, conf, kps, (0.5, 0.5), (720, 1280, 3), 0.45)
    assert len(faces) == 1
    center = model.priors[index, :2] * 1280
    assert np.allclose(faces[0][0], np.r_[center - 16, center + 16])
    assert np.allclose(faces[0][2], np.tile(center, (5, 1)))


def test_nms_suppresses_overlapping_xyxy_boxes_correctly():
    model = detector()
    loc, conf, kps = outputs(model)
    index = (30 * 80 + 40) * 2
    conf[0, index] = [0.1, 0.9]
    conf[0, index + 1] = [0.2, 0.8]
    # Make the larger anchor produce the same 16px box as the first.
    loc[0, index + 1, 2:] = np.log(0.5) / 0.2
    assert len(model.postprocess(loc, conf, kps, (1, 1), (640, 640, 3), 0.45)) == 1


def test_wrong_anchor_count_and_logits_fail_clearly():
    model = detector()
    loc, conf, kps = outputs(model)
    with pytest.raises(ValueError, match="anchor"):
        model.postprocess(loc[:, :3], conf, kps, (1, 1), (640, 640, 3))
    conf[0, 0] = [2, -1]
    with pytest.raises(ValueError, match="probabilities"):
        model.postprocess(loc, conf, kps, (1, 1), (640, 640, 3))


def test_no_faces_and_padding_only_proposals():
    model = detector()
    loc, conf, kps = outputs(model)
    assert model.postprocess(loc, conf, kps, (0.5, 0.5), (720, 1280, 3)) == []
    conf[0, (70 * 80 + 40) * 2] = [0.01, 0.99]
    assert model.postprocess(loc, conf, kps, (0.5, 0.5), (720, 1280, 3)) == []


def test_ema_smooths_same_track_without_changing_recognition_geometry():
    tracker = Tracker()
    a = {"box": [100, 50, 220, 260], "face_box": [120, 70, 180, 130]}
    tracker.update([a], 1)
    b = {"box": [100, 50, 220, 260], "face_box": [130, 70, 190, 130]}
    track = tracker.update([b], 1.1)[0]
    assert track.latest["face_box"] == [130, 70, 190, 130]
    assert np.allclose(track.latest["preview_face_box"], [127, 70, 187, 130])


def test_pgvector_refuses_sqlite_before_export():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(create_engine("sqlite:///:memory:"))
    local = PoseAdaptiveMatcher([], np.empty((0, 512)))
    with pytest.raises(ValueError, match="PostgreSQL"):
        PgVectorMatcher(factory, "company", "site", "model", "generation", local, [])


def test_pgvector_query_is_scoped_and_margin_uses_distinct_employees():
    from types import SimpleNamespace

    calls = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, params):
            calls.append((str(sql), params))
            return SimpleNamespace(all=lambda: [("one", 0.6), ("two", 0.59)])

    matcher = PgVectorMatcher.__new__(PgVectorMatcher)
    matcher.factory = Session
    matcher.company, matcher.site, matcher.model_id, matcher.fingerprint = (
        "company",
        "site",
        "model",
        "version",
    )
    matcher.local = PoseAdaptiveMatcher([], np.empty((0, 512)), 0.42, 0.08, 0.42, 0.08)
    vector = np.ones(512)
    assert matcher.match(vector, 0.9)[0] is None
    sql, params = calls[0]
    assert "GROUP BY employee_id" in sql and "LIMIT 2" in sql
    assert (
        params["company"] == "company" and params["site"] == "site" and params["model"] == "model"
    )
    assert params["version"] == "version"
    import json

    assert np.isclose(np.linalg.norm(json.loads(params["vector"])), 1)


def test_pgvector_export_keeps_unaveraged_pose_samples_and_flips():
    from types import SimpleNamespace

    calls = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def execute(self, sql, params):
            calls.append((str(sql), params))

        def commit(self):
            calls.append(("commit", None))

    local = PoseAdaptiveMatcher(["one", "one"], np.ones((2, 512)))
    PgVectorMatcher(
        Session,
        "company",
        "site",
        "model",
        "version",
        local,
        [
            {"sample_id": "photo", "kind": "original", "pose_type": "left_30"},
            {"sample_id": "photo", "kind": "horizontal_flip", "pose_type": "right_30"},
        ],
    )
    rows = calls[1][1]
    assert len(rows) == 2 and rows[0]["pose"] == "left_30" and rows[1]["pose"] == "right_30"
    assert rows[0]["variant"] != rows[1]["variant"] and calls[-1][0] == "commit"
