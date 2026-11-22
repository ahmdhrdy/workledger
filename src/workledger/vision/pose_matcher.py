"""Vectorized employee matching with explicit pose thresholds and margins."""

import numpy as np


class PoseAdaptiveMatcher:
    def __init__(
        self,
        ids,
        matrix,
        frontal_threshold=0.45,
        frontal_margin=0.08,
        side_threshold=0.36,
        side_margin=0.06,
    ):
        self.ids = np.asarray(ids, dtype=str)
        self.employee_ids = frozenset(self.ids.tolist())
        self.matrix = np.asarray(matrix, dtype=np.float32).reshape(-1, 512)
        if len(self.ids) != len(self.matrix) or not np.isfinite(self.matrix).all():
            raise ValueError("Invalid employee gallery")
        if len(self.matrix):
            norms = np.linalg.norm(self.matrix, axis=1, keepdims=True)
            if np.any(norms < 1e-8):
                raise ValueError("Gallery contains an empty embedding")
            self.matrix = self.matrix / norms
        self.frontal_threshold, self.frontal_margin = frontal_threshold, frontal_margin
        self.side_threshold, self.side_margin = side_threshold, side_margin

    def match(self, embedding, yaw_ratio):
        vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
        norm = np.linalg.norm(vector)
        if vector.shape != (512,) or not np.isfinite(norm) or norm < 1e-8:
            raise ValueError("Expected a finite 512-D embedding")
        if not len(self.ids):
            return None, 0.0, 0.0
        similarities = self.matrix @ (vector / norm)
        scores = sorted(
            (
                (float(similarities[self.ids == employee].max()), employee)
                for employee in np.unique(self.ids)
            ),
            reverse=True,
        )
        best, employee = scores[0]
        gap = best - (scores[1][0] if len(scores) > 1 else 0.0)
        threshold, margin = (
            (self.frontal_threshold, self.frontal_margin)
            if yaw_ratio >= 0.5
            else (self.side_threshold, self.side_margin)
        )
        return (str(employee) if best >= threshold and gap >= margin else None), best, gap
