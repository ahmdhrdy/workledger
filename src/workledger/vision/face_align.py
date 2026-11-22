"""Five-landmark least-squares similarity alignment for the 112px ArcFace template."""

import cv2
import numpy as np


def estimate_norm(landmark, image_size=112):
    points = np.asarray(landmark, dtype=np.float64)
    if points.shape != (5, 2) or not np.isfinite(points).all() or image_size != 112:
        raise ValueError("Alignment requires five finite landmarks and a 112px output")
    target = np.array(
        [
            [38.2946, 51.6963],
            [73.5318, 51.5014],
            [56.0252, 71.7366],
            [41.5493, 92.3655],
            [70.7299, 92.2041],
        ]
    )
    # Solve x' = a*x - b*y + tx, y' = b*x + a*y + ty using all five points.
    design = np.zeros((10, 4))
    design[0::2, 0] = points[:, 0]
    design[0::2, 1] = -points[:, 1]
    design[0::2, 2] = 1
    design[1::2, 0] = points[:, 1]
    design[1::2, 1] = points[:, 0]
    design[1::2, 3] = 1
    solution, _, rank, _ = np.linalg.lstsq(design, target.reshape(-1), rcond=None)
    if rank != 4:
        raise ValueError("Degenerate face landmarks")
    a, b, tx, ty = solution
    matrix = np.array([[a, -b, tx], [b, a, ty]])
    projected = points @ matrix[:, :2].T + matrix[:, 2]
    error = float(np.sqrt(np.mean(np.sum((projected - target) ** 2, axis=1))))
    if not np.isfinite(matrix).all() or a * a + b * b < 1e-12:
        raise ValueError("Degenerate alignment transform")
    return matrix, error


def norm_crop(frame, landmark, image_size=112):
    matrix, _ = estimate_norm(landmark, image_size)
    return cv2.warpAffine(frame, matrix, (image_size, image_size), borderValue=0)


def landmark_geometry(landmark, box):
    """Check five-point geometry in an eye-aligned coordinate system, not image x."""
    points = np.asarray(landmark, dtype=np.float64)
    bounds = np.asarray(box, dtype=np.float64)
    if (
        points.shape != (5, 2)
        or bounds.shape != (4,)
        or not np.isfinite(points).all()
        or not np.isfinite(bounds).all()
    ):
        raise ValueError("Face landmarks unreliable: invalid coordinates")
    width, height = bounds[2:] - bounds[:2]
    eye = points[1] - points[0]
    eye_distance = float(np.linalg.norm(eye))
    if min(width, height) <= 0 or eye_distance < max(4.0, width * 0.08):
        raise ValueError("Face landmarks unreliable: eyes collapsed or hidden")
    axis = eye / eye_distance
    down = np.array([-axis[1], axis[0]])
    origin = (points[0] + points[1]) / 2
    local = np.column_stack(((points - origin) @ axis, (points - origin) @ down)) / eye_distance
    mouth_center = local[3:].mean(axis=0)
    mouth_width = float(np.linalg.norm(local[4] - local[3]))
    padding = np.array([width, height]) * 0.25
    if (
        np.any(points < bounds[:2] - padding)
        or np.any(points > bounds[2:] + padding)
        or not 0.12 < mouth_width < 2.5
        or not 0.2 < mouth_center[1] < 6
        or local[2, 1] < -0.5
        or local[2, 1] > mouth_center[1] + 0.75
        or abs(local[2, 0]) > 2.5
    ):
        raise ValueError("Face landmarks unreliable: inconsistent eye/nose/mouth geometry")
    left = np.linalg.norm(points[2] - points[0])
    right = np.linalg.norm(points[2] - points[1])
    return {
        "yaw_ratio": float(min(left, right) / max(left, right, 1e-8)),
        "nose_offset": float(local[2, 0]),
        "eye_distance": eye_distance,
    }
