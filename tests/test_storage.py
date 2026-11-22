from fastapi.testclient import TestClient
from test_api import event, provision, rows, scope

from workledger.config import Settings
from workledger.main import create_app
from workledger.models import SourceEvent


def test_offset_event_timestamp_round_trip_is_utc(client, workspace):
    payload = event(client, workspace)
    payload["occurred_at"] = workspace["default_date"] + "T09:01:00+06:00"
    result = client.post(
        "/agent/v1/events:batch", headers=provision(client, workspace), json={"events": [payload]}
    )
    assert result.status_code == 200, result.text
    with client.app.state.session_factory() as db:
        at = db.get(SourceEvent, payload["event_id"]).occurred_at
        assert at.hour == 3 and at.minute == 1 and at.utcoffset().total_seconds() == 0


def test_empty_setup_has_no_synthetic_employees_or_camera_events():
    app = create_app(Settings(database_url="sqlite:///:memory:", mode="test", local_ai=False))
    with TestClient(app, headers={"x-workledger-request": "1"}) as client:
        assert client.get("/api/v1/setup").json()["required"]
        response = client.post(
            "/api/v1/setup",
            json={
                "company": "Real office",
                "site": "Dhaka",
                "name": "Admin",
                "email": "owner@example.test",
                "password": "test-only-password-123",
                "demo": False,
            },
        )
        assert response.status_code == 201
        workspace = client.get("/api/v1/me").json()
        assert not workspace["company"]["demo"]
        assert rows(client, workspace) == []
        assert (
            client.get("/api/v1/cameras", params={"site_id": workspace["sites"][0]["id"]}).json()
            == []
        )
        assert client.get("/api/v1/events", params=scope(workspace)).json() == []
