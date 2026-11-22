"""Optional synthetic RTSP test of HR-seeded daily body profiles and polygon presence.

Uses official OpenCV fixtures prepared by prepare-rtsp-fixtures.py. This verifies
plumbing with the same person image on two streams, not cross-view accuracy.
"""

import argparse
import json
import socket
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import cv2
import imageio_ffmpeg
import numpy as np
from fastapi.testclient import TestClient

from workledger.config import Settings
from workledger.main import create_app
from workledger.vision.inference import Inference


def wait_for(fn, seconds=35):
    end = time.time() + seconds
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.25)
    raise AssertionError("Timed out waiting for synthetic pipeline result")


def main(host):
    root = Path(".local/rtsp-test-tools").resolve()
    engine = Inference(Settings())
    capture = cv2.VideoCapture(str(root / "vtest.avi"))
    _, frame = capture.read()
    capture.release()
    candidates = engine.analyze(frame, [])
    assert candidates, "OpenCV pedestrian fixture must contain a person"
    person = max(
        candidates, key=lambda p: (p["box"][2] - p["box"][0]) * (p["box"][3] - p["box"][1])
    )
    x1, y1, x2, y2 = person["box"]
    crop = frame[max(0, y1 - 5) : y2 + 5, max(0, x1 - 5) : x2 + 5]
    scale = min(440 / crop.shape[0], 600 / crop.shape[1])
    crop = cv2.resize(crop, (round(crop.shape[1] * scale), round(crop.shape[0] * scale)))
    canvas = np.zeros((480, 640, 3), np.uint8)
    y = (480 - crop.shape[0]) // 2
    x = (640 - crop.shape[1]) // 2
    canvas[y : y + crop.shape[0], x : x + crop.shape[1]] = crop
    people = engine.analyze(canvas, [])
    assert len(people) == 1 and people[0].get("body") is not None, "Fixture must isolate one body"
    assert people[0].get("face_vector") is None, "This fixture must require HR identity review"
    writer = cv2.VideoWriter(
        str(root / "body.avi"), cv2.VideoWriter_fourcc(*"MJPG"), 12, (640, 480)
    )
    for _ in range(36):
        writer.write(canvas)
    writer.release()
    port = 18555
    config = root / "body-test.yml"
    config.write_text(
        f"logLevel: warn\nrtspAddress: {host}:{port}\nrtspTransports: [tcp]\nrtmp: no\nhls: no\nwebrtc: no\nsrt: no\nmoq: no\npaths:\n  body:\n    source: publisher\n"
    )
    children = []
    with open(root / "body-test.log", "w") as log:
        try:
            children.append(
                subprocess.Popen(
                    [str(root / "mediamtx.exe"), str(config)],
                    stdout=log,
                    stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            )

            def listening():
                try:
                    with socket.create_connection((host, port), timeout=0.3):
                        return True
                except OSError:
                    return False

            wait_for(listening, 10)
            children.append(
                subprocess.Popen(
                    [
                        imageio_ffmpeg.get_ffmpeg_exe(),
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-re",
                        "-stream_loop",
                        "-1",
                        "-i",
                        str(root / "body.avi"),
                        "-an",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "ultrafast",
                        "-tune",
                        "zerolatency",
                        "-pix_fmt",
                        "yuv420p",
                        "-g",
                        "12",
                        "-f",
                        "rtsp",
                        "-rtsp_transport",
                        "tcp",
                        f"rtsp://{host}:{port}/body",
                    ],
                    stdout=log,
                    stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            )
            time.sleep(1)
            identifier = uuid4().hex[:8]
            app = create_app(
                Settings(
                    database_url=f"sqlite:///.local/workplace-test-{identifier}.db",
                    local_data_dir=f".local/workplace-vault-{identifier}",
                    mode="test",
                    local_ai=True,
                )
            )
            with TestClient(app, headers={"X-Workledger-Request": "1"}) as client:
                runtime = app.state.vision
                runtime.model = engine
                runtime.model_state = "ready"
                runtime.start()
                try:
                    assert (
                        client.post(
                            "/api/v1/setup",
                            json={
                                "company": "Synthetic body test",
                                "site": "Office",
                                "name": "Test owner",
                                "email": "body@example.test",
                                "password": "synthetic-body-test-password",
                            },
                        ).status_code
                        == 201
                    )
                    workspace = client.get("/api/v1/me").json()
                    site = workspace["sites"][0]["id"]
                    day = workspace["default_date"]
                    profile = {
                        "site_id": site,
                        "code": "BODY-1",
                        "name": "Synthetic fixture employee",
                        "nickname": "Fixture Nick",
                        "department": "Tests",
                        "designation": "Fixture",
                        "monthly_salary": "0",
                        "overtime_rate": "0",
                    }
                    response = client.post(
                        "/api/v1/local/employees/register",
                        data={"profile": json.dumps(profile)},
                        files={
                            "image": ("sample.jpg", (root / "lena.jpg").read_bytes(), "image/jpeg")
                        },
                    )
                    assert response.status_code == 201, response.text
                    employee = response.json()["id"]

                    def onboard(role):
                        response = client.post(
                            "/api/v1/local/cameras",
                            json={
                                "site_id": site,
                                "name": f"Body test {role}",
                                "role": role,
                                "host": host,
                                "username": "",
                                "connection": "manual",
                                "path": "/body",
                                "rtsp_port": port,
                                "target_fps": 4,
                            },
                        )
                        assert response.status_code == 201, response.text
                        return response.json()["id"]

                    portal = onboard("entry")

                    def unknown():
                        events = client.get(
                            "/api/v1/local/identity-reviews", params={"site_id": site, "day": day}
                        ).json()
                        return next((e for e in events if e["outcome"] == "pending"), None)

                    event = wait_for(unknown)
                    response = client.post(
                        f"/api/v1/local/events/{event['id']}/resolve",
                        json={
                            "classification": "employee",
                            "employee_id": employee,
                            "reason": "Synthetic same-image body plumbing test, not identity accuracy evidence",
                        },
                    )
                    assert response.status_code == 200, response.text
                    profiles = client.get(
                        "/api/v1/local/daily-profiles", params={"site_id": site}
                    ).json()
                    assert (
                        len(profiles) == 1
                        and profiles[0]["source"] == "hr_review"
                        and profiles[0]["clothing"]["upper"]
                    )
                    assert (
                        profiles[0]["nickname"] == "Fixture Nick" and "vectors" not in profiles[0]
                    )
                    client.post(f"/api/v1/local/cameras/{portal}/action", json={"action": "stop"})
                    workplace = onboard("interior")
                    assert (
                        client.put(
                            f"/api/v1/local/cameras/{workplace}/zones",
                            json={
                                "zones": [
                                    {
                                        "id": "shared",
                                        "name": "Shared fixture desk",
                                        "kind": "workstation",
                                        "points": [[0, 0], [1, 0], [1, 1], [0, 1]],
                                        "employees": [employee],
                                        "counts_as_work": True,
                                    }
                                ]
                            },
                        ).status_code
                        == 200
                    )

                    def observed():
                        result = client.get(
                            "/api/v1/local/presence", params={"site_id": site, "day": day}
                        ).json()["items"][0]
                        return result if result["samples"] >= 3 else None

                    result = wait_for(observed)
                    assert (
                        result["tentative_seconds"] > 0 and result["work_tentative_seconds"] > 0
                    ), result
                    assert result["observed_seconds"] == 0, result
                    assert "Shared fixture desk" in result["zones"]
                    assert (
                        client.delete(f"/api/v1/local/daily-profiles/{employee}").status_code == 200
                    )
                    assert (
                        client.get("/api/v1/local/daily-profiles", params={"site_id": site}).json()
                        == []
                    )
                    assert (
                        len(
                            client.get(
                                "/api/v1/local/enrollments", params={"site_id": site}
                            ).json()[0]["samples"]
                        )
                        == 1
                    )
                    report = {
                        "status": "passed",
                        "scope": "Real body model → unknown entry evidence → HR assignment → encrypted daily appearance profile → second camera tentative re-ID → shared polygon work time → daily reset preserving face enrollment",
                        "notice": "Same pedestrian fixture on two streams; not cross-view, masked-face, or employee identification accuracy validation",
                        "observations": result,
                    }
                    (root / "workplace-integration-result.json").write_text(
                        json.dumps(report, indent=2)
                    )
                    print(json.dumps(report), flush=True)
                finally:
                    runtime.shutdown()
        finally:
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        child.kill()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="This laptop private LAN IPv4 address")
    main(parser.parse_args().host)
