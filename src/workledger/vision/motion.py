"""Time-based box prediction and globally optimal one-to-one assignment."""

import cv2
import numpy as np


def assignment(cost):
    """Hungarian assignment, with dummy unmatched nodes supplied by caller."""
    cost = np.asarray(cost, dtype=float)
    n, m = cost.shape
    if not n or not m:
        return []
    if n > m:
        return [(j, i) for i, j in assignment(cost.T)]
    u, v = np.zeros(n + 1), np.zeros(m + 1)
    p, way = np.zeros(m + 1, int), np.zeros(m + 1, int)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minimum, used = np.full(m + 1, np.inf), np.zeros(m + 1, bool)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta, j1 = np.inf, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1, j - 1] - u[i0] - v[j]
                    if cur < minimum[j]:
                        minimum[j], way[j] = cur, j0
                    if minimum[j] < delta:
                        delta, j1 = minimum[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minimum[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    return [(p[j] - 1, j - 1) for j in range(1, m + 1) if p[j]]


class BoxMotion:
    def __init__(self, box, at):
        self.filter = cv2.KalmanFilter(8, 4)
        self.filter.measurementMatrix = np.c_[np.eye(4), np.zeros((4, 4))].astype(np.float32)
        self.filter.measurementNoiseCov = np.eye(4, dtype=np.float32) * 4
        self.filter.errorCovPost = np.eye(8, dtype=np.float32) * 25
        self.filter.statePost = np.r_[box, [0] * 4].astype(np.float32).reshape(8, 1)
        self.filter.statePre = self.filter.statePost.copy()
        self.filter.errorCovPre = self.filter.errorCovPost.copy()
        self.at = at

    def predict(self, at):
        dt = max(0, at - self.at)
        self.filter.transitionMatrix = np.eye(8, dtype=np.float32)
        self.filter.transitionMatrix[:4, 4:] = np.eye(4) * min(dt, 2)
        self.filter.processNoiseCov = np.eye(8, dtype=np.float32) * max(0.01, dt * 2)
        self.at = at
        return self.filter.predict()[:4, 0].astype(float)

    def correct(self, box):
        self.filter.correct(np.asarray(box, np.float32).reshape(4, 1))
