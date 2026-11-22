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

parser = argparse.ArgumentParser(
    description="Optional real RTSP integration test using isolated synthetic media. Requires prepared MediaMTX/OpenCV fixtures in .local/rtsp-test-tools and imageio-ffmpeg."
)
parser.add_argument("--host", required=True, help="This laptop private LAN IPv4 address")
HOST = parser.parse_args().host
PORT = 18554
ROOT = Path(".local/rtsp-test-tools").resolve()


def main():
    image = cv2.imread(str(ROOT / "lena.jpg"))
    assert image is not None
    writer = cv2.VideoWriter(
        str(ROOT / "face.avi"), cv2.VideoWriter_fourcc(*"MJPG"), 12, (640, 480)
    )
    fixture = cv2.resize(image, (480, 480))
    canvas = np.zeros((480, 640, 3), np.uint8)
    canvas[:, 80:560] = fixture
    for _ in range(36):
        writer.write(canvas)
    writer.release()
    (ROOT / "test.yml").write_text(f"""logLevel: warn
rtspAddress: {HOST}:{PORT}
rtspTransports: [tcp]
rtmp: no
hls: no
webrtc: no
srt: no
moq: no
paths:
  test:
    source: publisher
""")
    children = []
    try:
        log = open(ROOT / "stream-test.log", "w")
        server = subprocess.Popen(
            [str(ROOT / "mediamtx.exe"), str(ROOT / "test.yml")],
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        children.append(server)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with socket.create_connection((HOST, PORT), timeout=0.3):
                    break
            except OSError:
                time.sleep(0.2)
        publisher = subprocess.Popen(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-re",
                "-stream_loop",
                "-1",
                "-i",
                str(ROOT / "face.avi"),
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
                f"rtsp://{HOST}:{PORT}/test",
            ],
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        children.append(publisher)
        time.sleep(2)
        identifier = uuid4().hex[:8]
        app = create_app(
            Settings(
                database_url=f"sqlite:///.local/rtsp-integration-{identifier}.db",
                mode="test",
                local_ai=True,
                local_data_dir=f".local/rtsp-vault-{identifier}",
                model_dir=".local/models",
            )
        )
        with TestClient(app, headers={"x-workledger-request": "1"}) as client:
            runtime = app.state.vision
            runtime.start()
            try:
                response = client.post(
                    "/api/v1/setup",
                    json={
                        "company": "Synthetic RTSP verification",
                        "site": "Local test",
                        "name": "Test Operator",
                        "email": "pipeline@example.test",
                        "password": uuid4().hex,
                        "demo": False,
                    },
                )
                assert response.status_code == 201, response.text
                workspace = client.get("/api/v1/me").json()
                site = workspace["sites"][0]["id"]
                employee = client.post(
                    "/api/v1/employees",
                    json={
                        "site_id": site,
                        "code": "TEST-001",
                        "name": "Synthetic test subject",
                        "department": "Testing",
                        "designation": "Fixture",
                        "monthly_salary": "0",
                        "overtime_rate": "0",
                    },
                ).json()
                response = client.post(
                    f"/api/v1/local/employees/{employee['id']}/enroll",
                    files={"image": ("sample.jpg", (ROOT / "lena.jpg").read_bytes(), "image/jpeg")},
                )
                assert response.status_code == 200, response.text
                print("Real face enrollment passed", flush=True)
                cameras = []
                for role in ["entry", "exit", "interior"]:
                    response = client.post(
                        "/api/v1/local/cameras",
                        json={
                            "site_id": site,
                            "name": f"RTSP {role}",
                            "role": role,
                            "host": HOST,
                            "username": "",
                            "connection": "manual",
                            "rtsp_port": PORT,
                            "path": "/test",
                            "target_fps": 4,
                            "zone_name": "Test workspace",
                        },
                    )
                    assert response.status_code == 201, response.text
                    camera = response.json()["id"]
                    cameras.append(camera)
                    deadline = time.time() + 35
                    observed = False
                    while time.time() < deadline:
                        time.sleep(0.4)
                        if role == "interior":
                            data = client.get(
                                "/api/v1/local/presence",
                                params={"site_id": site, "day": workspace["default_date"]},
                            ).json()
                            observed = any(item["samples"] >= 2 for item in data["items"])
                        else:
                            data = client.get(
                                "/api/v1/events",
                                params={"site_id": site, "day": workspace["default_date"]},
                            ).json()
                            observed = any(
                                item["kind"] == role
                                and item["employee_id"] == employee["id"]
                                and item["identity_status"] == "confirmed"
                                for item in data
                            )
                        if observed:
                            break
                    state = runtime.state(camera)
                    assert observed, json.dumps({"role": role, "state": state, "events": data})
                    frame = client.get(f"/api/v1/local/cameras/{camera}/frame")
                    assert frame.status_code == 200
                    if role in {"entry", "exit"}:
                        observed_event = next(
                            item
                            for item in data
                            if item["kind"] == role and item["employee_id"] == employee["id"]
                        )
                        retained = client.get(
                            f"/api/v1/local/events/{observed_event['id']}/evidence"
                        )
                        assert retained.status_code == 200, retained.text
                        assert retained.headers["content-type"] == "image/jpeg"
                    (ROOT / f"{role}-overlay.jpg").write_bytes(frame.content)
                    print(f"Real RTSP {role}: {json.dumps(state)}", flush=True)
                    assert (
                        client.post(
                            f"/api/v1/local/cameras/{camera}/action", json={"action": "stop"}
                        ).status_code
                        == 200
                    )
                result = client.get(
                    "/api/v1/attendance", params={"site_id": site, "day": workspace["default_date"]}
                ).json()["rows"][0]
                assert result["complete"] and result["status"] == "needs_review", result
                assert (
                    client.post(
                        "/api/v1/attendance/decisions",
                        json={
                            "employee_id": employee["id"],
                            "business_date": workspace["default_date"],
                            "outcome": "present",
                            "paid_leave": False,
                            "overtime_minutes": 0,
                            "reason": "Automated synthetic RTSP integration verification",
                        },
                    ).status_code
                    == 200
                )
                result = {
                    "status": "passed",
                    "scope": "Actual RTSP decode, face enrollment/recognition, IN/OUT events, interior observations, preview and HR approval",
                    "notice": "Standard OpenCV test image; no real camera or masked-face accuracy claim",
                }
                (ROOT / "integration-result.json").write_text(json.dumps(result, indent=2))
                print(json.dumps(result), flush=True)
            finally:
                runtime.shutdown()
    finally:
        for child in reversed(children):
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()


if __name__ == "__main__":
    main()
