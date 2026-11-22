import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path

root = Path(".local/rtsp-test-tools")
root.mkdir(parents=True, exist_ok=True)
release = json.load(
    urllib.request.urlopen("https://api.github.com/repos/bluenviron/mediamtx/releases/tags/v1.21.2")
)
asset = next(asset for asset in release["assets"] if asset["name"].endswith("windows_amd64.zip"))
data = urllib.request.urlopen(asset["browser_download_url"], timeout=60).read()
if asset.get("digest"):
    assert asset["digest"] == "sha256:" + hashlib.sha256(data).hexdigest()
with zipfile.ZipFile(io.BytesIO(data)) as archive:
    (root / "mediamtx.exe").write_bytes(archive.read("mediamtx.exe"))
for filename in ["lena.jpg", "vtest.avi"]:
    target = root / filename
    if not target.exists():
        target.write_bytes(
            urllib.request.urlopen(
                "https://raw.githubusercontent.com/opencv/opencv/master/samples/data/" + filename,
                timeout=60,
            ).read()
        )
print("Local RTSP test tools and official OpenCV sample media ready")
