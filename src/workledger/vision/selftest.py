"""Exercise actual installed models locally, without a connected camera."""

import json
import time

import cv2
import numpy as np

from workledger.config import Settings

from .inference import Inference


def main():
    started = time.perf_counter()
    engine = Inference(Settings())
    loaded = time.perf_counter() - started
    frame = np.zeros((480, 640, 3), np.uint8)
    started = time.perf_counter()
    people = engine.analyze(frame, [])
    # Exercise the re-identification head even though a blank frame has no people.
    features = engine.reid_model([np.zeros((1, 3, 256, 128), np.float32)])[
        engine.reid_model.output(0)
    ]
    assert np.isfinite(features).all() and features.size > 0
    face_features = engine.engine.get_embedding(np.zeros((112, 112, 3), np.uint8))
    assert face_features.shape == (512,) and np.isfinite(face_features).all()
    assert people == []
    print(
        json.dumps(
            {
                "status": "passed",
                "load_seconds": round(loaded, 2),
                "blank_frame_ms": round((time.perf_counter() - started) * 1000, 1),
                "reid_dimensions": int(features.size),
                "recognizer": "adaface_ir50_ms1mv2.onnx",
                "face_dimensions": int(face_features.size),
                "face_runtime": "ONNX Runtime CPU",
                "model_id": engine.engine.model_id,
                "opencv": cv2.__version__,
                "notice": "Smoke test only; recognition accuracy and camera capacity not measured",
            }
        )
    )


if __name__ == "__main__":
    main()
