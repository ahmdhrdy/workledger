from uuid import uuid4

import pytest
from pydantic import ValidationError

from workledger.outbox import Outbox, http_sender


def payload():
    return {
        "schema_version": 1,
        "event_id": str(uuid4()),
        "camera_id": str(uuid4()),
        "employee_id": str(uuid4()),
        "kind": "entry",
        "identity_status": "confirmed",
        "occurred_at": "2026-10-14T03:00:00Z",
        "boot_id": "boot-one",
        "sequence": 1,
    }


def test_crash_retry_and_partial_ack_keep_unacknowledged_events(tmp_path):
    path = tmp_path / "queue.db"
    queue = Outbox(path)
    first, second = payload(), payload()
    queue.enqueue(first)
    queue.enqueue(second)
    queue.enqueue(first)
    queue = Outbox(path)
    assert len(queue.pending()) == 2

    def unavailable(_):
        raise ConnectionError("offline")

    with pytest.raises(ConnectionError):
        queue.flush(unavailable)
    assert len(queue.pending()) == 2
    assert queue.flush(lambda _: {"duplicates": [first["event_id"]]}) == 1
    assert queue.pending()[0]["event_id"] == second["event_id"]


def test_no_biometrics_and_no_unknown_ack_deletion(tmp_path):
    queue = Outbox(tmp_path / "queue.db")
    event = payload()
    with pytest.raises(ValidationError):
        queue.enqueue({**event, "face_embedding": [1, 2, 3]})
    queue.enqueue(event)
    with pytest.raises(ValueError):
        queue.flush(lambda _: {"accepted": [str(uuid4())]})
    assert len(queue.pending()) == 1
    with pytest.raises(ValueError):
        queue.enqueue({**event, "sequence": 2})


def test_transport_requires_tls_except_loopback():
    with pytest.raises(ValueError):
        http_sender("http://example.test", "token")
    with pytest.raises(ValueError):
        http_sender("https://user:secret@example.test", "token")
    assert callable(http_sender("http://127.0.0.1:8000", "token"))
