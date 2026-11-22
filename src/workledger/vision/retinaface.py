"""Decode the supplied Biubug6 RetinaFace loc/conf/landmark ONNX export."""

import math

import cv2
import numpy as np
import onnxruntime as ort


class RetinaFaceONNX:
    def __init__(self, path, options=None, size=640):
        self.model = ort.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        inputs = self.model.get_inputs()
        if (
            len(inputs) != 1
            or inputs[0].type != "tensor(float)"
            or len(inputs[0].shape) != 4
            or inputs[0].shape[1] != 3
        ):
            raise ValueError("RetinaFace requires float32 NCHW BGR input")
        self.input_name = inputs[0].name
        shape = inputs[0].shape
        self.width = shape[3] if isinstance(shape[3], int) and shape[3] > 0 else size
        self.height = shape[2] if isinstance(shape[2], int) and shape[2] > 0 else size
        self.output_names = {}
        for output in self.model.get_outputs():
            if len(output.shape) != 3 or output.shape[-1] not in (2, 4, 10):
                raise ValueError("Expected RetinaFace raw loc/conf/landmark outputs")
            self.output_names[output.shape[-1]] = output.name
        if set(self.output_names) != {2, 4, 10}:
            raise ValueError(
                "RetinaFace must expose three raw outputs, not an SCRFD/decoded export"
            )
        self.priors = self.generate_priors(self.width, self.height)

    @staticmethod
    def generate_priors(width, height):
        anchors = []
        for step, sizes in zip((8, 16, 32), ((16, 32), (64, 128), (256, 512)), strict=True):
            for i in range(math.ceil(height / step)):
                for j in range(math.ceil(width / step)):
                    for size in sizes:
                        anchors.append(
                            (
                                (j + 0.5) * step / width,
                                (i + 0.5) * step / height,
                                size / width,
                                size / height,
                            )
                        )
        return np.asarray(anchors, np.float32)

    def preprocess(self, frame):
        h, w = frame.shape[:2]
        scale = min(self.width / w, self.height / h)
        nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
        padded = np.zeros((self.height, self.width, 3), np.float32)
        padded[:nh, :nw] = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
        padded -= np.array([104, 117, 123], np.float32)
        return np.ascontiguousarray(padded.transpose(2, 0, 1)[None]), (nw / w, nh / h)

    def postprocess(self, loc, conf, landmarks, scales, original_shape, threshold=0.45):
        loc = np.asarray(loc).reshape(-1, 4)
        conf = np.asarray(conf).reshape(-1, 2)
        landmarks = np.asarray(landmarks).reshape(-1, 10)
        if not (len(loc) == len(conf) == len(landmarks) == len(self.priors)):
            raise ValueError("RetinaFace output count does not match generated anchors")
        if (
            not np.isfinite(conf).all()
            or np.any(conf < -1e-5)
            or np.any(conf > 1.00001)
            or not np.allclose(conf.sum(axis=1), 1, atol=0.01)
        ):
            raise ValueError("RetinaFace confidence output must contain softmax probabilities")
        valid = np.flatnonzero(
            (conf[:, 1] >= threshold)
            & np.isfinite(loc).all(axis=1)
            & np.isfinite(landmarks).all(axis=1)
        )
        # Limit pathological proposal volume before NMS, sorted by face probability.
        valid = valid[np.argsort(conf[valid, 1])[::-1][:5000]]
        prior = self.priors[valid]
        centers = prior[:, :2] + loc[valid, :2] * 0.1 * prior[:, 2:]
        sizes = prior[:, 2:] * np.exp(np.clip(loc[valid, 2:] * 0.2, -20, 20))
        factor = np.array([self.width / scales[0], self.height / scales[1]], np.float32)
        boxes = np.c_[centers - sizes / 2, centers + sizes / 2] * np.tile(factor, 2)
        kps = (
            prior[:, None, :2] + landmarks[valid].reshape(-1, 5, 2) * 0.1 * prior[:, None, 2:]
        ) * factor
        scores = conf[valid, 1]
        h, w = original_shape[:2]
        # Reject padding-only proposals. Keep raw geometry for boundary quality gates.
        keep = (
            (boxes[:, 0] < w)
            & (boxes[:, 1] < h)
            & (boxes[:, 2] > 0)
            & (boxes[:, 3] > 0)
            & np.all(boxes[:, 2:] > boxes[:, :2], axis=1)
        )
        boxes, kps, scores = boxes[keep], kps[keep], scores[keep]
        if not len(boxes):
            return []
        # OpenCV accepts x,y,width,height here, NOT x1,y1,x2,y2.
        xywh = np.c_[boxes[:, :2], boxes[:, 2:] - boxes[:, :2]]
        indices = cv2.dnn.NMSBoxes(xywh.tolist(), scores.tolist(), float(threshold), 0.4)
        return [(boxes[i], float(scores[i]), kps[i]) for i in np.asarray(indices).reshape(-1)[:100]]

    def detect(self, frame, threshold=0.45):
        blob, scales = self.preprocess(frame)
        loc, conf, kps = self.model.run(
            [self.output_names[4], self.output_names[2], self.output_names[10]],
            {self.input_name: blob},
        )
        return self.postprocess(loc, conf, kps, scales, frame.shape, threshold)
