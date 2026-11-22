"""Decode the nine-output det_10g SCRFD network using ONNX Runtime CPU."""

import cv2
import numpy as np


class SCRFD:
    def __init__(self, path, session_options, input_size=640):
        import onnxruntime as ort

        self.size = input_size
        self.model = ort.InferenceSession(
            str(path), sess_options=session_options, providers=["CPUExecutionProvider"]
        )
        self.input_name = self.model.get_inputs()[0].name
        if len(self.model.get_outputs()) != 9:
            raise ValueError("det_10g must expose three score, box and landmark outputs")

    def detect(self, frame, threshold=0.80):
        height, width = frame.shape[:2]
        scale = min(self.size / width, self.size / height)
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        canvas = np.zeros((self.size, self.size, 3), dtype=np.uint8)
        canvas[: size[1], : size[0]] = cv2.resize(frame, size)
        blob = cv2.dnn.blobFromImage(
            canvas, 1 / 128, (self.size, self.size), (127.5,) * 3, swapRB=True
        )
        output = self.model.run(None, {self.input_name: blob})
        candidates = []
        for level, stride in enumerate((8, 16, 32)):
            scores = output[level].reshape(-1)
            distances = output[level + 3].reshape(-1, 4) * stride
            offsets = output[level + 6].reshape(-1, 5, 2) * stride
            grid = np.stack(
                np.meshgrid(np.arange(self.size // stride), np.arange(self.size // stride)), -1
            )
            centers = np.repeat(grid.reshape(-1, 2) * stride, 2, axis=0)
            if len(scores) != len(centers):
                raise ValueError("Unexpected SCRFD anchor layout")
            for index in np.flatnonzero(scores >= threshold):
                center = centers[index]
                distance = distances[index]
                box = np.r_[center - distance[:2], center + distance[2:]] / scale
                landmarks = (offsets[index] + center) / scale
                if np.isfinite(box).all() and np.isfinite(landmarks).all():
                    candidates.append((box, float(scores[index]), landmarks))
        # Non-maximum suppression in original frame coordinates.
        result = []
        candidates.sort(key=lambda item: item[1], reverse=True)
        while candidates:
            current = candidates.pop(0)
            result.append(current)
            box = current[0]
            remaining = []
            for candidate in candidates:
                other = candidate[0]
                overlap = np.maximum(
                    0, np.minimum(box[2:], other[2:]) - np.maximum(box[:2], other[:2])
                )
                intersection = float(np.prod(overlap))
                union = float(
                    np.prod(box[2:] - box[:2]) + np.prod(other[2:] - other[:2]) - intersection
                )
                if intersection / max(union, 1e-8) <= 0.4:
                    remaining.append(candidate)
            candidates = remaining
        return result
