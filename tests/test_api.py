from datetime import datetime
from uuid import uuid4

from sqlalchemy import select

from workledger.models import Company, Employee, PayrollRun, Site, SourceEvent, User


def scope(workspace):
    return {"site_id": workspace["sites"][0]["id"], "day": workspace["default_date"]}


def rows(client, workspace):
    response = client.get("/api/v1/attendance", params=scope(workspace))
    assert response.status_code == 200, response.text
    return response.json()["rows"]


def approval(row, workspace, outcome="present"):
    return {
        "employee_id": row["employee"]["id"],
        "business_date": workspace["default_date"],
        "outcome": outcome,
        "paid_leave": outcome == "leave",
        "overtime_minutes": 0,
        "reason": "Verified by HR against local evidence",
    }


def provision(client, workspace):
    result = client.post(
        "/api/v1/agents", json={"site_id": workspace["sites"][0]["id"], "name": "Office edge"}
    )
    assert result.status_code == 201, result.text
    return {"Authorization": "Bearer " + result.json()["token"]}


def event(client, workspace, employee_id=None, kind="entry", sequence=1):
    cameras = client.get("/api/v1/cameras", params={"site_id": scope(workspace)["site_id"]}).json()
    camera = next(c for c in cameras if c["role"] == kind)
    return {
        "event_id": str(uuid4()),
        "camera_id": camera["id"],
        "employee_id": employee_id,
        "kind": kind,
        "identity_status": "confirmed" if employee_id else "unknown",
        "occurred_at": workspace["default_date"] + "T03:01:00Z",
        "boot_id": "test-boot",
        "sequence": sequence,
    }


def test_explicit_sample_data_and_missing_days_need_review(client, workspace):
    assert workspace["company"]["demo"]
    attendance = rows(client, workspace)
    assert len(attendance) == 15
    first = next(r for r in attendance if r["employee"]["code"] == "EMP-001")
    expected = datetime.fromisoformat(workspace["default_date"] + "T03:00:00+00:00")
    assert datetime.fromisoformat(first["first_in"]) == expected
    assert first["late_minutes"] == 0
    missing = next(r for r in attendance if not r["event_count"] and not r["decision"])
    assert missing["status"] == "needs_review"
    assert (
        client.post("/api/v1/attendance/decisions", json=approval(missing, workspace)).status_code
        == 409
    )
    result = client.post("/api/v1/attendance/decisions", json=approval(missing, workspace, "leave"))
    assert result.status_code == 200, result.text
    assert (
        next(
            r for r in rows(client, workspace) if r["employee"]["id"] == missing["employee"]["id"]
        )["status"]
        == "leave"
    )
    events = client.get("/api/v1/events", params=scope(workspace)).json()
    assert events and all(e["source"] == "simulated" for e in events)


def test_presence_conflicts_with_absence_and_policy_invalidates_approval(client, workspace):
    complete = next(r for r in rows(client, workspace) if r["complete"])
    assert (
        client.post(
            "/api/v1/attendance/decisions", json=approval(complete, workspace, "absent")
        ).status_code
        == 409
    )
    assert (
        client.post("/api/v1/attendance/decisions", json=approval(complete, workspace)).status_code
        == 200
    )
    assert (
        next(
            r for r in rows(client, workspace) if r["employee"]["id"] == complete["employee"]["id"]
        )["status"]
        == "present"
    )
    site = workspace["sites"][0]
    policy = {
        key: site[key]
        for key in [
            "timezone",
            "shift_start",
            "shift_end",
            "weekdays",
            "grace_minutes",
            "break_minutes",
            "retention_days",
        ]
    }
    policy["grace_minutes"] += 1
    response = client.put(f"/api/v1/sites/{site['id']}/policy", json=policy)
    assert response.status_code == 200, response.text
    updated = next(
        r for r in rows(client, workspace) if r["employee"]["id"] == complete["employee"]["id"]
    )
    assert updated["approval_stale"] and updated["status"] == "needs_review"


def test_agent_dedup_conflict_and_batch_atomicity(client, workspace):
    headers = provision(client, workspace)
    first = event(client, workspace)
    endpoint = "/agent/v1/events:batch"
    assert client.post(endpoint, json={"events": [first]}).status_code == 401
    result = client.post(endpoint, headers=headers, json={"events": [first]})
    assert result.json()["accepted"] == [first["event_id"]]
    assert client.post(endpoint, headers=headers, json={"events": [first]}).json()[
        "duplicates"
    ] == [first["event_id"]]
    assert (
        client.post(
            endpoint, headers=headers, json={"events": [{**first, "sequence": 5}]}
        ).status_code
        == 409
    )
    second = event(client, workspace, sequence=2)
    bad = {**event(client, workspace, sequence=3), "camera_id": str(uuid4())}
    assert client.post(endpoint, headers=headers, json={"events": [second, bad]}).status_code == 422
    with client.app.state.session_factory() as db:
        assert db.get(SourceEvent, second["event_id"]) is None
    assert (
        client.post(
            endpoint, headers=headers, json={"events": [{**second, "face_crop": "forbidden"}]}
        ).status_code
        == 422
    )


def test_late_delivery_invalidates_previous_approval(client, workspace):
    complete = next(r for r in rows(client, workspace) if r["complete"])
    assert (
        client.post("/api/v1/attendance/decisions", json=approval(complete, workspace)).status_code
        == 200
    )
    headers = provision(client, workspace)
    late = event(client, workspace, complete["employee"]["id"])
    assert (
        client.post("/agent/v1/events:batch", headers=headers, json={"events": [late]}).status_code
        == 200
    )
    updated = next(
        r for r in rows(client, workspace) if r["employee"]["id"] == complete["employee"]["id"]
    )
    assert updated["approval_stale"] and not updated["complete"]


def test_tenant_isolation_and_role_permissions(client, workspace):
    with client.app.state.session_factory() as db:
        other = Company(name="Other tenant")
        db.add(other)
        db.flush()
        site = Site(company_id=other.id, name="Private site")
        db.add(site)
        db.flush()
        other_id = site.id
        db.commit()
    assert client.get("/api/v1/employees", params={"site_id": other_id}).status_code == 404
    assert (
        client.post("/api/v1/agents", json={"site_id": other_id, "name": "Not allowed"}).status_code
        == 404
    )
    with client.app.state.session_factory() as db:
        user = db.scalar(select(User))
        user.role = "viewer"
        db.commit()
    response = client.get("/api/v1/employees", params={"site_id": scope(workspace)["site_id"]})
    assert "monthly_salary" not in response.json()[0]
    assert (
        client.post(
            "/api/v1/payroll/preview",
            json={"site_id": scope(workspace)["site_id"], "month": workspace["default_date"][:7]},
        ).status_code
        == 403
    )
    assert client.get("/api/v1/audit").status_code == 403


def test_payroll_snapshot_preserves_inputs_and_blocks_missing_days(client, workspace):
    body = {"site_id": scope(workspace)["site_id"], "month": workspace["default_date"][:7]}
    preview = client.post("/api/v1/payroll/preview", json=body)
    assert preview.status_code == 200, preview.text
    assert not preview.json()["can_finalize"] and len(preview.json()["blockers"]) == 15
    result = client.post("/api/v1/payroll/runs", json=body)
    assert result.status_code == 201, result.text
    with client.app.state.session_factory() as db:
        run = db.get(PayrollRun, result.json()["id"])
        assert run.snapshot["input_revision"] == preview.json()["input_revision"]
        before = run.snapshot["total"]
        employee = db.scalar(select(Employee))
        employee.monthly_salary = 99999
        db.commit()
    with client.app.state.session_factory() as db:
        assert db.get(PayrollRun, result.json()["id"]).snapshot["total"] == before
    assert (
        client.post("/api/v1/payroll/preview", json=body).json()["input_revision"]
        != preview.json()["input_revision"]
    )
    unknown = event(client, workspace)
    client.post(
        "/agent/v1/events:batch", headers=provision(client, workspace), json={"events": [unknown]}
    )
    updated = client.post("/api/v1/payroll/preview", json=body).json()
    assert any(b["reason"] == "Local identity review required" for b in updated["blockers"])


def test_browser_boundary_logout_and_export(client, workspace):
    assert (
        client.post("/api/v1/auth/logout", headers={"origin": "https://other.test"}).status_code
        == 403
    )
    export = client.get("/api/v1/reports/attendance.csv", params=scope(workspace))
    assert export.status_code == 200
    assert "text/csv" in export.headers["content-type"]
    assert len(export.text.splitlines()) == 16
    assert "First IN (UTC)" in export.text
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/me").status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "admin@example.test", "password": "wrong"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.test", "password": "test-only-password-123"},
        ).status_code
        == 200
    )


def test_setup_is_once_only(client):
    assert (
        client.post(
            "/api/v1/setup",
            json={
                "company": "New office",
                "site": "Site",
                "name": "Admin",
                "email": "x@example.test",
                "password": "test-password-123",
            },
        ).status_code
        == 409
    )
