"""Scheduling regressions: video does not wait for tracking replay or slow storage."""

import threading
import time
from types import SimpleNamespace

import numpy as np
from test_preview import analysis, frame, scene

from workledger.vision.background import BackgroundIO
from workledger.vision.preview import PreviewTracker
from workledger.vision.runtime import VisionRuntime


def test_repeated_preview_requests_do_not_decode_or_replay_again(monkeypatch):
    preview = PreviewTracker()
    source = frame(scene(), 1)
    metadata = analysis(source)
    result = preview.render(source, metadata)
    monkeypatch.setattr(
        preview, "_gray", lambda *_: (_ for _ in ()).throw(AssertionError("decode"))
    )
    monkeypatch.setattr(
        preview, "_advance", lambda *_: (_ for _ in ()).throw(AssertionError("flow"))
    )
    assert preview.render(source, metadata) is result
    changed = dict(metadata, revision=1)
    # Revision must still be consumed, rather than hidden by a frame-only cache.
    assert preview._update_same_frame_metadata(changed)
    assert preview.analysis_revision == 1


def test_detector_seed_reuses_grayscale_history(monkeypatch):
    preview = PreviewTracker()
    source = frame(scene(), 1)
    preview.render(source, None)
    monkeypatch.setattr(
        preview, "_gray", lambda *_: (_ for _ in ()).throw(AssertionError("decode"))
    )
    assert preview.render(source, analysis(source))["people"] == 1


def test_slow_storage_does_not_block_submit_and_preserves_order():
    io = BackgroundIO(capacity=2)
    started, release = threading.Event(), threading.Event()
    calls = []

    def first():
        started.set()
        release.wait(2)
        calls.append(1)

    io.start()
    try:
        assert io.submit(first)
        assert started.wait(1)
        assert io.submit(lambda: calls.append(2))
        assert io.submit(lambda: calls.append(3))
        assert not io.submit(lambda: calls.append(4))
        assert io.status()["storage_error"]
        assert calls == []
    finally:
        release.set()
        io.shutdown()
    assert calls == [1, 2, 3]


def test_storage_retries_failed_job_before_next_job():
    io = BackgroundIO()
    calls = []
    done = threading.Event()

    def flaky():
        calls.append("attempt")
        if len(calls) == 1:
            raise OSError("temporary test failure")

    io.submit(flaky)
    io.submit(done.set)
    io.start()
    try:
        assert done.wait(2)
    finally:
        io.shutdown()
    assert calls == ["attempt", "attempt"]


def test_event_queue_captures_track_before_it_changes():
    runtime = VisionRuntime.__new__(VisionRuntime)
    runtime.io = BackgroundIO()
    calls = []
    runtime.write_event = lambda *args: calls.append(args)
    track = SimpleNamespace(
        employee_id="original",
        score=0.8,
        latest={"body": np.array([1.0, 0.0]), "clothing": {"upper": "blue"}},
    )
    assert runtime.queue_event("camera", {"role": "entry"}, track, 123, b"jpeg")
    track.employee_id = "replacement"
    track.latest["body"][:] = 0
    runtime.io.start()
    runtime.io.shutdown()
    frozen = calls[0][2]
    assert frozen.employee_id == "original" and frozen.latest["body"][0] == 1
    assert calls[0][3] == 123 and calls[0][4] == b"jpeg"


def test_slow_appearance_write_does_not_hold_body_match_lock():
    from workledger.vision.appearance import DailyGallery

    started, release, matched = threading.Event(), threading.Event(), threading.Event()
    errors = []

    def slow_write(*args):
        started.set()
        release.wait(2)

    gallery = DailyGallery(
        SimpleNamespace(daily_profiles=lambda *args: [], put_daily_profile=slow_write)
    )
    gallery.cache["site"] = []
    vector = np.array([1.0, 0.0])
    writer = threading.Thread(
        target=gallery.add, args=("site", "person", "camera", vector, time.time())
    )
    writer.start()
    assert started.wait(1)

    def match():
        try:
            gallery.match("site", vector, time.time(), 0.8, 0.08)
            matched.set()
        except Exception as error:
            errors.append(error)

    reader = threading.Thread(target=match)
    reader.start()
    try:
        assert matched.wait(0.5), "Body matching waited for an appearance disk write"
    finally:
        release.set()
        writer.join(2)
        reader.join(2)
    assert not errors
