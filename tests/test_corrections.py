import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text

from workledger.config import Settings
from workledger.main import create_app
from workledger.migration import upgrade_local
from workledger.models import Camera, SourceEvent
from workledger.vision.appearance import DailyGallery, day_window
from workledger.vision.flow import Flow
from workledger.vision.presence import summarize
from workledger.vision.store import LocalStore
from workledger.vision.tracking import Tracker
from workledger.vision.zones import membership, validate_polygon


@pytest.fixture
def fresh(tmp_path):
    app = create_app(
        Settings(
            mode="test",
            database_url="sqlite:///:memory:",
            local_ai=True,
            local_data_dir=str(tmp_path / "vault"),
        )
    )
    with TestClient(app, headers={"X-Workledger-Request": "1"}) as client:
        assert (
            client.post(
                "/api/v1/setup",
                json={
                    "company": "Actual company",
                    "site": "Office",
                    "name": "Owner",
                    "email": "owner@example.test",
                    "password": "a-local-test-password",
                },
            ).status_code
            == 201
        )
        yield client


def scope(client):
    return client.get("/api/v1/me").json()["sites"][0]["id"]


def employee(client, code="EMP-1"):
    result = client.post(
        "/api/v1/employees",
        json={
            "site_id": scope(client),
            "name": "Employee One",
            "nickname": "Alex",
            "code": code,
            "designation": "Engineer",
            "department": "Product",
            "monthly_salary": "26000",
            "overtime_rate": "100",
        },
    )
    assert result.status_code == 201, result.text
    return result.json()["id"]


def test_blank_lists_and_profile_edit(fresh):
    site = scope(fresh)
    assert fresh.get("/api/v1/employees", params={"site_id": site}).json() == []
    assert fresh.get("/api/v1/local/cameras", params={"site_id": site}).json() == []
    identifier = employee(fresh)
    body = {
        "site_id": site,
        "name": "Employee One",
        "nickname": "New nickname",
        "code": "EMP-1",
        "designation": "Lead",
        "department": "Product",
        "monthly_salary": "28000",
        "overtime_rate": "110",
        "joined_on": "2026-01-01",
        "active": True,
    }
    result = fresh.put(f"/api/v1/employees/{identifier}", json=body)
    assert result.status_code == 200, result.text
    assert (
        result.json()["nickname"] == "New nickname" and result.json()["monthly_salary"] == "28000"
    )
    assert (
        fresh.post(
            "/api/v1/employees", json={**body, "joined_on": "2026-99-01", "active": True}
        ).status_code
        == 422
    )


def test_combined_enrollment_validates_before_creating_employee(fresh, monkeypatch):
    runtime = fresh.app.state.vision
    monkeypatch.setattr(
        runtime,
        "load_model",
        lambda: SimpleNamespace(
            enrollment=lambda image: {"vector": np.array([1.0] + [0.0] * 511), "quality": 123}
        ),
    )
    profile = {
        "site_id": scope(fresh),
        "name": "Enrolled Person",
        "nickname": "Nick",
        "code": "STAFF-1",
        "designation": "Engineer",
        "department": "Office",
        "monthly_salary": "25000",
        "overtime_rate": "100",
    }
    assert (
        fresh.post(
            "/api/v1/local/employees/register",
            data={"profile": json.dumps(profile)},
            files={"image": ("bad.jpg", b"invalid")},
        ).status_code
        == 422
    )
    assert fresh.get("/api/v1/employees", params={"site_id": scope(fresh)}).json() == []
    _, jpeg = cv2.imencode(".jpg", np.zeros((100, 100, 3), np.uint8))
    result = fresh.post(
        "/api/v1/local/employees/register",
        data={"profile": json.dumps(profile)},
        files={"image": ("face.jpg", jpeg.tobytes())},
    )
    assert result.status_code == 201, result.text
    assert (
        runtime.templates(fresh.get("/api/v1/me").json()["company"]["id"], scope(fresh))[0]["name"]
        == "Nick"
    )
    assert (
        fresh.post(
            "/api/v1/local/employees/register",
            data={"profile": json.dumps(profile)},
            files={"image": ("face.jpg", jpeg.tobytes())},
        ).status_code
        == 409
    )


def test_daily_signatures_expire_on_local_midnight_and_survive_restart(tmp_path):
    store = LocalStore(tmp_path)
    at = datetime(2026, 10, 17, 17, 59, 59, tzinfo=UTC).timestamp()  # 23:59:59 Dhaka
    day, expires = day_window(at, "Asia/Dhaka")
    assert day == "2026-10-17" and expires - at == 1
    gallery = DailyGallery(store)
    store.add_template("face", "company", "employee", "masked", [1, 0])
    gallery.add(
        "site",
        "employee",
        "camera",
        [1, 0],
        at,
        "Asia/Dhaka",
        {"name": "Employee", "nickname": "Nick", "clothing": {"upper": "blue"}},
    )
    assert DailyGallery(store).match("site", [1, 0], at + 0.1, 0.8, 0.08)[0] == "employee"
    assert gallery.match("other-site", [1, 0], at + 0.1, 0.8, 0.08)[0] is None
    assert gallery.match("site", [1, 0], expires, 0.8, 0.08)[0] is None
    store.purge_daily(expires)
    assert store.daily_profiles("site", at) == []
    assert len(store.templates("company")) == 1
    gallery.add(
        "site", "employee", "camera", [0, 1], expires + 1, "Asia/Dhaka", {"name": "Employee"}
    )
    assert gallery.profiles("site", expires + 1)[0]["day"] == "2026-10-18"


def test_polygon_assignments_shared_meetings_boundary_and_overlap():
    square = [[0, 0], [0.5, 0], [0.5, 1], [0, 1]]
    validate_polygon(square)
    zone = {
        "id": "shared",
        "name": "Shared desk",
        "points": square,
        "employees": ["one", "two"],
        "counts_as_work": True,
        "priority": 0,
    }
    assert membership([0, 0, 50, 50], (100, 100), [zone], "one")["id"] == "shared"
    assert membership([0, 0, 50, 50], (100, 100), [zone], "two")["counts_as_work"]
    assert not membership([0, 0, 50, 50], (100, 100), [zone], "three")["counts_as_work"]
    meeting = {**zone, "id": "meeting", "applies_all": True, "priority": 5, "name": "Meeting"}
    assert membership([0, 0, 100, 50], (100, 100), [zone, meeting], "three")["id"] == "meeting"
    with pytest.raises(ValueError):
        validate_polygon([[0, 0], [1, 1], [0, 1], [1, 0]])
    with pytest.raises(ValueError):
        validate_polygon([[0, 0], [float("nan"), 0], [0, 1]])


def test_cross_camera_presence_union_keeps_unknown_and_tentative_separate():
    rows = []
    for camera in ["one", "two"]:
        rows.extend(
            {
                "camera_id": camera,
                "at": float(at),
                "zone": "Shared",
                "counts_work": True,
                "method": "body_tentative",
            }
            for at in [10, 11, 12]
        )
    result = summarize(rows, 0, 20)
    assert result["tentative_seconds"] == 2 and result["work_tentative_seconds"] == 2
    assert result["unknown_seconds"] == 18 and result["coverage_percent"] == 10
    assert result["work_percent_of_observed"] == 100
    assert result["observed_seconds"] == 0


def test_break_credit_is_capped_and_excluded_from_work_ratio():
    records = []
    for at in range(11):
        records.append(
            {
                "camera_id": "one",
                "at": float(at),
                "zone": "Desk" if at <= 5 else "Dining",
                "counts_work": at <= 5,
                "method": "face",
                "zone_kind": "workstation" if at <= 5 else "break",
            }
        )
    result = summarize(records, 0, 10, 3)
    assert result["work_confirmed_seconds"] == 5
    assert result["approved_break_seconds"] == 3 and result["excess_break_seconds"] == 1
    assert result["eligible_observed_seconds"] == 6
    assert result["work_percent_of_observed"] == 83.3
    assert result["unknown_seconds"] == 1


def test_zone_allowed_time_uses_site_timezone_and_overnight_windows():
    from workledger.vision.zones import during_window

    noon = datetime(2026, 10, 18, 6, tzinfo=UTC).timestamp()
    assert during_window({"allowed_from": "12:00", "allowed_until": "13:00"}, noon, "Asia/Dhaka")
    assert not during_window(
        {"allowed_from": "12:00", "allowed_until": "13:00"}, noon + 3600, "Asia/Dhaka"
    )
    assert during_window(
        {"allowed_from": "23:00", "allowed_until": "02:00"}, noon + 12 * 3600, "Asia/Dhaka"
    )


def test_optical_flow_moves_boxes_without_confirming_identity():
    rng = np.random.default_rng(10)
    image = rng.integers(0, 255, (360, 640, 3), dtype=np.uint8)
    tracker = Tracker()
    track = tracker.update(
        [{"box": [100, 50, 300, 300], "employee_id": "one", "method": "face", "score": 0.9}], 1
    )[0]
    flow = Flow()
    flow.prime(image, [track], 1)
    moved = cv2.warpAffine(image, np.float32([[1, 0, 5], [0, 1, 2]]), (640, 360))
    results = flow.update(moved, tracker, 1.1)
    assert results and abs(results[0].box[0] - 105) < 1
    assert track.hits == 1 and not track.emitted
    assert flow.update(moved, tracker, 3) == []


def manual(client, identifier, day, kind, hour):
    result = client.post(
        "/api/v1/events/manual",
        json={
            "employee_id": identifier,
            "kind": kind,
            "occurred_at": f"{day}T{hour}:00:00+06:00",
            "reason": "Verified local evidence by HR",
        },
    )
    assert result.status_code == 201, result.text
    return result.json()["id"]


def test_manual_corrections_approval_and_void_preserve_original(fresh):
    identifier = employee(fresh)
    site = scope(fresh)
    day = "2026-09-01"
    first = manual(fresh, identifier, day, "entry", "09")
    manual(fresh, identifier, day, "exit", "18")

    def row():
        return fresh.get("/api/v1/attendance", params={"site_id": site, "day": day}).json()["rows"][
            0
        ]

    assert row()["complete"]
    assert (
        fresh.post(
            "/api/v1/attendance/decisions",
            json={
                "employee_id": identifier,
                "business_date": day,
                "outcome": "present",
                "overtime_minutes": 0,
                "reason": "All evidence verified",
            },
        ).status_code
        == 200
    )
    assert (
        fresh.post(
            f"/api/v1/events/{first}/void",
            json={"reason": "Incorrect entry time, use corrected record"},
        ).status_code
        == 200
    )
    assert row()["approval_stale"] and not row()["complete"]
    with fresh.app.state.session_factory() as db:
        assert db.get(SourceEvent, first).kind == "entry"
    assert fresh.get("/api/v1/cameras", params={"site_id": site}).json() == []


def test_paid_holidays_payroll_adjustments_staleness_and_finalization(fresh):
    site = scope(fresh)
    identifier = employee(fresh)
    month = "2026-09"
    # Mark every nominal weekday as a paid office holiday; none need absence review.
    for day in range(1, 31):
        value = datetime(2026, 9, day)
        if value.weekday() < 5:
            assert (
                fresh.post(
                    "/api/v1/calendar",
                    json={
                        "site_id": site,
                        "day": value.date().isoformat(),
                        "name": "Office paid holiday",
                        "kind": "paid_holiday",
                    },
                ).status_code
                == 200
            )

    def preview():
        return fresh.post("/api/v1/payroll/preview", json={"site_id": site, "month": month}).json()

    result = preview()
    assert result["can_finalize"] and result["items"][0]["earned_salary"] == "26000.00"
    assert (
        fresh.get("/api/v1/attendance", params={"site_id": site, "day": "2026-09-01"}).json()[
            "rows"
        ][0]["status"]
        == "holiday"
    )
    for kind, amount in [("allowance", "500"), ("deduction", "200")]:
        assert (
            fresh.post(
                "/api/v1/payroll/adjustments",
                json={
                    "employee_id": identifier,
                    "month": month,
                    "kind": kind,
                    "amount": amount,
                    "reason": "Approved monthly adjustment",
                },
            ).status_code
            == 201
        )
    assert preview()["total"] == "26300.00"
    run = fresh.post("/api/v1/payroll/runs", json={"site_id": site, "month": month}).json()["id"]
    assert (
        fresh.post(
            f"/api/v1/payroll/runs/{run}/finalize",
            json={"reason": "Salary sheet verified and approved"},
        ).status_code
        == 200
    )
    assert (
        fresh.post(
            f"/api/v1/payroll/runs/{run}/finalize", json={"reason": "Retry approval"}
        ).status_code
        == 409
    )
    assert "26300.00" in fresh.get(f"/api/v1/payroll/runs/{run}/payslip/{identifier}").text
    assert "holiday_pay" in fresh.get(f"/api/v1/payroll/runs/{run}/export.csv").text
    fresh.post(
        "/api/v1/payroll/adjustments",
        json={
            "employee_id": identifier,
            "month": month,
            "kind": "allowance",
            "amount": "1",
            "reason": "Late approved adjustment",
        },
    )
    saved = fresh.get(f"/api/v1/payroll/runs/{run}").json()
    assert saved["stale"] and saved["snapshot"]["total"] == "26300.00"


def test_holiday_work_requires_review_and_configurable_pay(fresh):
    identifier = employee(fresh)
    site = scope(fresh)
    day = "2026-09-05"  # Saturday weekly off
    manual(fresh, identifier, day, "entry", "09")
    manual(fresh, identifier, day, "exit", "12")
    assert (
        fresh.put(
            f"/api/v1/sites/{site}/payroll-policy",
            json={
                "holiday_multiplier": "2",
                "holiday_hourly_rate": "150",
                "overtime_multiplier": "1",
            },
        ).status_code
        == 200
    )
    result = fresh.post(
        "/api/v1/attendance/decisions",
        json={
            "employee_id": identifier,
            "business_date": day,
            "outcome": "present",
            "overtime_minutes": 0,
            "holiday_minutes": 180,
            "reason": "Approved Saturday office work",
        },
    )
    assert result.status_code == 200, result.text
    preview = fresh.post(
        "/api/v1/payroll/preview", json={"site_id": site, "month": "2026-09"}
    ).json()
    assert preview["items"][0]["holiday_pay"] == "900.00"
    assert not preview["can_finalize"]  # Other regular days still require review.


def test_interior_unknown_review_does_not_create_attendance(fresh):
    site = scope(fresh)
    identifier = employee(fresh)
    with fresh.app.state.session_factory() as db:
        camera = Camera(
            company_id=fresh.get("/api/v1/me").json()["company"]["id"],
            site_id=site,
            name="Desk camera",
            role="interior",
            target_fps=2,
        )
        db.add(camera)
        db.flush()
        source = SourceEvent(
            id=str(uuid4()),
            company_id=camera.company_id,
            site_id=site,
            camera_id=camera.id,
            kind="interior",
            identity_status="unknown",
            occurred_at=datetime(2026, 9, 1, 4, tzinfo=UTC),
            boot_id="local",
            sequence=1,
            fingerprint="original",
            simulated=False,
        )
        db.add(source)
        db.commit()
        event_id = source.id
    response = fresh.get(
        "/api/v1/local/identity-reviews", params={"site_id": site, "day": "2026-09-01"}
    )
    assert response.json()[0]["outcome"] == "pending"
    assert (
        fresh.post(
            f"/api/v1/local/events/{event_id}/resolve",
            json={
                "classification": "visitor",
                "visitor_name": "Supplier",
                "reason": "Delivery of office supplies",
            },
        ).status_code
        == 200
    )
    assert (
        fresh.get(
            "/api/v1/local/identity-reviews", params={"site_id": site, "day": "2026-09-01"}
        ).json()[0]["outcome"]
        == "visitor"
    )
    assert (
        fresh.post(
            f"/api/v1/local/events/{event_id}/resolve",
            json={
                "classification": "employee",
                "employee_id": identifier,
                "reason": "HR recognized employee from evidence",
            },
        ).status_code
        == 200
    )
    attendance = fresh.get(
        "/api/v1/attendance", params={"site_id": site, "day": "2026-09-01"}
    ).json()
    assert attendance["rows"][0]["event_count"] == 0
    assert attendance["unknown_events"] == 0


def test_versioned_database_migration_and_unrecognized_schema_guard(tmp_path):
    from alembic import command
    from alembic.config import Config

    path = tmp_path / "old.db"
    url = "sqlite:///" + path.as_posix()
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "dd46ee67ef13")
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(
            text("INSERT INTO companies(id,name,demo) VALUES ('kept','Preserved Company',0)")
        )
        db.execute(text("DROP TABLE alembic_version"))
    upgrade_local(engine, Settings(database_url=url))
    with engine.connect() as db:
        assert (
            db.execute(text("SELECT name FROM companies WHERE id='kept'")).scalar()
            == "Preserved Company"
        )
        assert (
            db.execute(text("SELECT version_num FROM alembic_version")).scalar() == "a91f7302c114"
        )
    assert list((tmp_path / "backups").glob("*.db"))
    engine.dispose()
    bad = create_engine("sqlite:///" + (tmp_path / "bad.db").as_posix())
    with bad.begin() as db:
        db.execute(text("CREATE TABLE unrelated(id TEXT)"))
    with pytest.raises(RuntimeError, match="Unrecognized"):
        upgrade_local(bad, Settings(database_url="sqlite:///" + (tmp_path / "bad.db").as_posix()))
    bad.dispose()


def test_hr_user_role_boundaries_and_session_revocation(fresh):
    from workledger.models import User

    identifier = employee(fresh)
    body = {
        "name": "HR Reviewer",
        "email": "hr@example.test",
        "role": "hr",
        "password": "another-local-test-password",
    }
    result = fresh.post("/api/v1/users", json=body)
    assert result.status_code == 201, result.text
    user_id = result.json()["id"]
    other = TestClient(fresh.app, headers={"X-Workledger-Request": "1"})
    assert (
        other.post(
            "/api/v1/auth/login", json={"email": body["email"], "password": body["password"]}
        ).status_code
        == 200
    )
    listed = other.get("/api/v1/employees", params={"site_id": scope(fresh)}).json()
    assert "monthly_salary" not in listed[0]
    assert other.get("/api/v1/users").status_code == 403
    assert (
        other.post(
            "/api/v1/payroll/preview", json={"site_id": scope(fresh), "month": "2026-09"}
        ).status_code
        == 403
    )
    assert (
        other.put(
            f"/api/v1/employees/{identifier}",
            json={
                "site_id": scope(fresh),
                "name": "Employee One",
                "nickname": "Reviewed nickname",
                "code": "EMP-1",
                "designation": "Engineer",
                "department": "Office",
                "active": True,
            },
        ).status_code
        == 200
    )
    assert (
        fresh.put(
            f"/api/v1/users/{user_id}", json={**body, "role": "disabled", "password": None}
        ).status_code
        == 200
    )
    assert other.get("/api/v1/me").status_code == 401
    assert (
        other.post(
            "/api/v1/auth/login", json={"email": body["email"], "password": body["password"]}
        ).status_code
        == 401
    )
    with fresh.app.state.session_factory() as db:
        admin = db.scalar(select(User).where(User.role == "admin"))
        admin_id, admin_email = admin.id, admin.email
    assert (
        fresh.put(
            f"/api/v1/users/{admin_id}",
            json={"name": "Owner", "email": admin_email, "role": "viewer"},
        ).status_code
        == 409
    )


def test_camera_zones_api_validates_roles_assignments_and_persists(fresh):
    site = scope(fresh)
    identifier = employee(fresh)
    with fresh.app.state.session_factory() as db:
        company = fresh.get("/api/v1/me").json()["company"]["id"]
        workplace = Camera(
            company_id=company, site_id=site, name="Office", role="interior", target_fps=2
        )
        portal = Camera(company_id=company, site_id=site, name="Entry", role="entry", target_fps=4)
        db.add_all([workplace, portal])
        db.commit()
        workplace_id, portal_id = workplace.id, portal.id
    zone = {
        "id": "shared",
        "name": "Shared desk",
        "employees": [identifier],
        "points": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
        "counts_as_work": True,
    }
    response = fresh.put(f"/api/v1/local/cameras/{workplace_id}/zones", json={"zones": [zone]})
    assert response.status_code == 200, response.text
    assert fresh.get(f"/api/v1/local/cameras/{workplace_id}/zones").json()[0]["employees"] == [
        identifier
    ]
    assert (
        fresh.put(f"/api/v1/local/cameras/{portal_id}/zones", json={"zones": [zone]}).status_code
        == 422
    )
    assert (
        fresh.put(
            f"/api/v1/local/cameras/{workplace_id}/zones",
            json={"zones": [{**zone, "employees": [str(uuid4())]}]},
        ).status_code
        == 404
    )
    assert (
        fresh.put(
            f"/api/v1/local/cameras/{workplace_id}/zones",
            json={"zones": [{**zone, "kind": "break"}]},
        ).status_code
        == 422
    )


def test_locked_identity_and_event_state_survive_unknown_then_one_conflicting_match():
    tracker = Tracker()
    person = {"box": [10, 10, 100, 200], "employee_id": "one", "method": "face", "score": 0.9}
    track = tracker.update([person], 1)[0]
    track.emitted = True
    tracker.update([{**person, "employee_id": None, "method": "unknown"}], 1.1)
    next_track = tracker.update([{**person, "employee_id": "two"}], 1.2)[0]
    assert next_track.emitted and next_track.employee_id == "one"
    assert next_track.is_locked and next_track.last_identity == "one"
    assert next_track.needs_reverification


def test_frame_unicode_labels_are_local_and_raw_capture_has_no_labels(fresh):
    import base64

    with fresh.app.state.session_factory() as db:
        camera = Camera(
            company_id=fresh.get("/api/v1/me").json()["company"]["id"],
            site_id=scope(fresh),
            name="Camera",
            role="entry",
            target_fps=4,
        )
        db.add(camera)
        db.commit()
        identifier = camera.id
    runtime = fresh.app.state.vision
    _, jpeg = cv2.imencode(".jpg", np.zeros((100, 100, 3), np.uint8))
    runtime.frames[identifier] = {"jpeg": jpeg.tobytes(), "at": time.time()}
    runtime.overlays[identifier] = {
        "jpeg": jpeg.tobytes(),
        "at": time.time(),
        "labels": [{"name": "আরিফ", "x": 0.2, "y": 0.2}],
    }
    response = fresh.get(f"/api/v1/local/cameras/{identifier}/frame")
    assert (
        json.loads(base64.b64decode(response.headers["X-Workledger-Labels"]))[0]["name"] == "আরিফ"
    )
    raw = fresh.get(f"/api/v1/local/cameras/{identifier}/frame?raw=true")
    assert json.loads(base64.b64decode(raw.headers["X-Workledger-Labels"])) == []


def test_archiving_generated_lists_preserves_history_and_releases_real_ids(tmp_path):
    from workledger.models import Employee
    from workledger.samples import archive_generated_lists

    app = create_app(
        Settings(
            mode="test",
            database_url="sqlite:///:memory:",
            local_ai=True,
            local_data_dir=str(tmp_path / "vault"),
        )
    )
    with TestClient(app, headers={"X-Workledger-Request": "1"}) as client:
        assert (
            client.post(
                "/api/v1/setup",
                json={
                    "company": "Legacy sample",
                    "site": "Office",
                    "name": "Owner",
                    "email": "legacy@example.test",
                    "password": "legacy-local-test-password",
                    "demo": True,
                },
            ).status_code
            == 201
        )
        site = scope(client)
        with app.state.session_factory() as db:
            before = db.scalars(select(SourceEvent)).all()
            count = len(before)
            kept = db.scalar(select(Employee).where(Employee.code == "EMP-002"))
            kept_id = kept.id
            app.state.vision.store.add_template(
                "sample", kept.company_id, kept.id, "uncovered", [1, 0]
            )
        archive_generated_lists(app.state.session_factory, app.state.vision.store)
        listed = client.get("/api/v1/employees", params={"site_id": site}).json()
        assert len(listed) == 1 and listed[0]["id"] == kept_id
        # A real employee may now use EMP-001; no hidden placeholder reserves it.
        assert employee(client, "EMP-001")
        with app.state.session_factory() as db:
            assert len(db.scalars(select(SourceEvent)).all()) == count
