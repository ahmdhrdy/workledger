import pytest
from fastapi.testclient import TestClient

from workledger.config import Settings
from workledger.main import create_app


@pytest.fixture
def client():
    app = create_app(Settings(database_url="sqlite:///:memory:", mode="test", local_ai=False))
    with TestClient(app, headers={"x-workledger-request": "1"}) as client:
        response = client.post(
            "/api/v1/setup",
            json={
                "company": "Test Office",
                "site": "Dhaka",
                "name": "Test Admin",
                "email": "admin@example.test",
                "password": "test-only-password-123",
                "demo": True,
            },
        )
        assert response.status_code == 201, response.text
        yield client


@pytest.fixture
def workspace(client):
    return client.get("/api/v1/me").json()
