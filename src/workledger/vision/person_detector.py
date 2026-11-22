"""COCO person detection using the official YOLOX-Nano ONNX export."""

import cv2
import numpy as np
import onnxruntime as ort


class PersonDetector:
    def __init__(self, path, threads=2, threshold=0.3):
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.input = self.session.get_inputs()[0].name
        self.height, self.width = self.session.get_inputs()[0].shape[2:]
        self.threshold = threshold
        grids, strides = [], []
        for stride in (8, 16, 32):
            grid = np.stack(
                np.meshgrid(np.arange(self.width // stride), np.arange(self.height // stride)), -1
            )
            grids.append(grid.reshape(-1, 2))
            strides.append(np.full((len(grids[-1]), 1), stride))
        self.grid = np.concatenate(grids)
        self.stride = np.concatenate(strides)

    def detect(self, frame):
        height, width = frame.shape[:2]
        scale = min(self.width / width, self.height / height)
        resized = cv2.resize(frame, (int(width * scale), int(height * scale)))
        padded = np.full((self.height, self.width, 3), 114, dtype=np.uint8)
        padded[: resized.shape[0], : resized.shape[1]] = resized
        # Official YOLOX export consumes BGR 0..255 float32 NCHW.
        blob = np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype=np.float32)
        output = self.session.run(None, {self.input: blob})[0][0]
        scores = output[:, 4] * output[:, 5]  # COCO class zero is person.
        indices = np.flatnonzero((scores >= self.threshold) & (output[:, 5:].argmax(axis=1) == 0))
        if not len(indices):
            return []
        values = output[indices]
        centers = (values[:, :2] + self.grid[indices]) * self.stride[indices]
        sizes = np.exp(values[:, 2:4]) * self.stride[indices]
        boxes = np.concatenate((centers - sizes / 2, centers + sizes / 2), axis=1) / scale
        boxes = boxes.clip([0, 0, 0, 0], [width, height, width, height])
        candidates = sorted(zip(boxes, scores[indices]), key=lambda item: item[1], reverse=True)
        result = []
        while candidates:
            box, score = candidates.pop(0)
            if min(box[2:] - box[:2]) < 10:
                continue
            result.append((box.tolist(), float(score)))
            remaining = []
            for other, confidence in candidates:
                intersection = float(
                    np.prod(
                        np.maximum(
                            0, np.minimum(box[2:], other[2:]) - np.maximum(box[:2], other[:2])
                        )
                    )
                )
                union = float(
                    np.prod(box[2:] - box[:2]) + np.prod(other[2:] - other[:2]) - intersection
                )
                if intersection / max(union, 1e-8) <= 0.45:
                    remaining.append((other, confidence))
            candidates = remaining
        return result
