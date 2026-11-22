"""Pinned, checksum-verified upstream weights. No weights are fetched at inference time."""

import hashlib
import json
import re
from pathlib import Path
from urllib.request import Request, urlopen

OV_REV = "464159ff32fbd2469b9ebf4e388eb0ebb63db9ee"
FACE_DETECT = "retinaface-resnet50.onnx"
FACE_RECOGNIZE = "adaface_ir50_ms1mv2.onnx"
PERSON_ONNX = "yolox_nano.onnx"
REID = "person-reidentification-retail-0288"
REQUIRED = [
    FACE_DETECT,
    FACE_RECOGNIZE,
    PERSON_ONNX,
    REID + ".xml",
    REID + ".bin",
]


def fetch(url):
    with urlopen(
        Request(url, headers={"User-Agent": "WorkLedger-local-model-installer"}), timeout=60
    ) as response:
        return response.read()


def install(directory, progress=lambda message: None):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    specs = [
        {
            "name": PERSON_ONNX,
            "algorithm": "sha256",
            "checksum": "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d",
            "size": 3659407,
            "url": "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx?download=1",
        }
    ]
    for name in (FACE_DETECT, FACE_RECOGNIZE):
        if not (root / name).is_file():
            raise ValueError(f"Place the supplied {name} in {root} before installation")
        progress(f"Using supplied local model: {name}")
    for model in [REID]:
        manifest = fetch(
            f"https://raw.githubusercontent.com/openvinotoolkit/open_model_zoo/{OV_REV}/models/intel/{model}/model.yml"
        ).decode()
        for extension in ["xml", "bin"]:
            pattern = rf"name: FP16/{re.escape(model)}\.{extension}\s+size: (\d+)\s+checksum: ([a-f0-9]+)\s+source: (https://\S+)"
            match = re.search(pattern, manifest)
            if not match:
                raise ValueError("Pinned OpenVINO manifest format changed")
            specs.append(
                {
                    "name": model + "." + extension,
                    "size": int(match[1]),
                    "algorithm": "sha384",
                    "checksum": match[2],
                    "url": match[3],
                }
            )
    (root / "OpenVINO-LICENSE.txt").write_bytes(
        fetch(f"https://raw.githubusercontent.com/openvinotoolkit/open_model_zoo/{OV_REV}/LICENSE")
    )
    (root / "YOLOX-LICENSE.txt").write_bytes(
        fetch("https://raw.githubusercontent.com/Megvii-BaseDetection/YOLOX/0.1.1/LICENSE")
    )
    for spec in specs:
        target = root / spec["name"]
        if (
            target.exists()
            and hashlib.new(spec["algorithm"], target.read_bytes()).hexdigest() == spec["checksum"]
        ):
            continue
        progress("Downloading " + spec["name"])
        data = fetch(spec["url"])
        if (
            len(data) != spec["size"]
            or hashlib.new(spec["algorithm"], data).hexdigest() != spec["checksum"]
        ):
            raise ValueError("Downloaded model failed verification: " + spec["name"])
        temporary = target.with_suffix(target.suffix + ".part")
        temporary.write_bytes(data)
        temporary.replace(target)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "openvino_revision": OV_REV,
                "files": specs,
                "local_face_models": [
                    {"name": name, "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()}
                    for name in (FACE_DETECT, FACE_RECOGNIZE)
                ],
            },
            indent=2,
        )
    )
    progress("Local face models found; person/body model downloads verified")


def missing(directory):
    return [name for name in REQUIRED if not (Path(directory) / name).is_file()]


if __name__ == "__main__":
    from workledger.config import Settings

    install(Settings().model_dir, print)
