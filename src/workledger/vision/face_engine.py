"""RetinaFace and AdaFace inference. Enrollment and live video share BGR preprocessing."""

import hashlib
import threading
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from . import face_align

MODEL_NAME = "adaface_ir50_ms1mv2.onnx"
MODEL_REVISION = "adaface_ir50_ms1mv2:bgr112:127.5:v1"


def model_identity(directory):
    path = Path(directory) / MODEL_NAME
    return MODEL_REVISION + ":" + hashlib.sha256(path.read_bytes()).hexdigest()


class FaceEngine:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.RLock()
        self.model_id = model_identity(settings.model_dir)
        options = ort.SessionOptions()
        options.intra_op_num_threads = settings.inference_threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.rec_session = ort.InferenceSession(
            str(Path(settings.model_dir) / MODEL_NAME),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        input_info = self.rec_session.get_inputs()[0]
        self.rec_input_name = input_info.name
        if input_info.type != "tensor(float)" or input_info.shape[1:] != [3, 112, 112]:
            raise ValueError("AdaFace must accept float32 NCHW 112x112 input")
        if self.rec_session.get_outputs()[0].shape[-1] != 512:
            raise ValueError("AdaFace must output 512-D embeddings")
        from .retinaface import RetinaFaceONNX

        self.detector = RetinaFaceONNX(
            Path(settings.model_dir) / "retinaface-resnet50.onnx", options, settings.detector_size
        )

    def detect_faces(self, frame, threshold=None):
        with self.lock:
            return self.detector.detect(
                frame,
                threshold=self.settings.live_detection_threshold
                if threshold is None
                else threshold,
            )

    def preprocess_and_align(self, frame, box, kps, minimum):
        h, w = frame.shape[:2]
        kps = np.asarray(kps, dtype=float)
        box = np.asarray(box, dtype=float)
        if kps.shape != (5, 2) or box.shape != (4,) or not np.isfinite(box).all():
            raise ValueError("Face landmarks: invalid coordinates")
        x1, y1, x2, y2 = box
        width, height = x2 - x1, y2 - y1
        if min(width, height) < minimum:
            raise ValueError(f"Face too small ({min(width, height):.0f}px < {minimum}px)")
        edge = self.settings.face_edge_margin
        if (
            not np.isfinite(kps).all()
            or x1 < edge
            or y1 < edge
            or x2 > w - edge
            or y2 > h - edge
            or not 0.4 < width / height < 1.8
            or np.any(kps < 0)
            or np.any(kps > [w - 1, h - 1])
        ):
            raise ValueError("Partial face ")
        geometry = face_align.landmark_geometry(kps, box)
        yaw_ratio = geometry["yaw_ratio"]
        _, fit = face_align.estimate_norm(kps)
        if fit > 18 or yaw_ratio < self.settings.face_yaw_min:
            raise ValueError(
                f"Face alignment unreliable (fit {fit:.1f}px, symmetry {yaw_ratio:.2f})"
            )
        crop = frame[int(np.floor(y1)) : int(np.ceil(y2)), int(np.floor(x1)) : int(np.ceil(x2))]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        native = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        standard = cv2.resize(gray, (64, 64), interpolation=cv2.INTER_AREA)
        sharpness = float(cv2.Laplacian(standard, cv2.CV_64F).var())
        if sharpness < self.settings.face_blur_min:
            raise ValueError(
                f"Face too blurry (64px sharpness {sharpness:.1f} < {self.settings.face_blur_min})"
            )
        return face_align.norm_crop(frame, landmark=kps, image_size=112), {
            "quality": sharpness,
            "native_sharpness": native,
            "yaw_ratio": yaw_ratio,
            "alignment_error": fit,
            "nose_offset": geometry["nose_offset"],
        }

    @staticmethod
    def input_blob(aligned_bgr):
        if aligned_bgr.shape != (112, 112, 3):
            raise ValueError("Expected a 112x112 BGR face")
        return np.ascontiguousarray(
            ((aligned_bgr.astype(np.float32) - 127.5) / 127.5).transpose(2, 0, 1)[None]
        )

    def get_embedding(self, aligned_bgr):
        with self.lock:
            output = self.rec_session.run(
                None, {self.rec_input_name: self.input_blob(aligned_bgr)}
            )[0]
        vector = output.reshape(-1).astype(np.float32)
        norm = np.linalg.norm(vector)
        if vector.shape != (512,) or not np.isfinite(norm) or norm < 1e-8:
            raise ValueError("AdaFace produced an invalid embedding")
        return vector / norm

    def augmentation_crops(self, aligned, masked=False):
        crops = [("horizontal_flip", cv2.flip(aligned, 1))]
        if self.settings.masked_augmentation or masked:
            masked = aligned.copy()
            cv2.fillConvexPoly(
                masked, np.array([[20, 65], [92, 65], [87, 103], [25, 103]]), (160, 160, 160)
            )
            crops.extend([("synthetic_mask", masked), ("synthetic_mask_flip", cv2.flip(masked, 1))])
        return crops
