"""Enrollment review metadata shared by HTTP and local rebuild tools."""

import base64
import hashlib

import cv2

from workledger.models import now


def sample_metadata(image, face, source_name, data=None):
    height, width = image.shape[:2]
    scale = min(1.0, 256 / max(height, width))
    thumbnail = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))))
    okay, encoded = cv2.imencode(".jpg", thumbnail, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not okay:
        raise ValueError("Unable to create enrollment review thumbnail")
    return {
        "source_name": (source_name or "Camera capture")[:255],
        "quality": round(face["quality"], 1),
        "detection_score": round(float(face.get("detection_score", 0)), 3),
        "created_at": now().isoformat(),
        "thumbnail": base64.b64encode(encoded.tobytes()).decode(),
        "augmentations": face.get("augmentations", []),
        "yaw_ratio": face.get("yaw_ratio"),
        "native_sharpness": face.get("native_sharpness"),
        "sha256": hashlib.sha256(data).hexdigest() if data is not None else None,
    }
