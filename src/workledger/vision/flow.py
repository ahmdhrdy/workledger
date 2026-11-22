"""Short-lived optical flow between model refreshes. Never confirms attendance."""

import cv2
import numpy as np


class Flow:
    def __init__(self):
        self.gray = None
        self.at = 0
        self.points = {}

    def prime(self, image, tracks, at):
        self.gray = cv2.cvtColor(cv2.resize(image, (640, 360)), cv2.COLOR_BGR2GRAY)
        height, width = image.shape[:2]
        self.points = {}
        for track in tracks:
            box = np.array(track.box) * np.array([640 / width, 360 / height] * 2)
            x1, y1, x2, y2 = box.astype(int)
            mask = np.zeros_like(self.gray)
            mask[max(0, y1) : min(360, y2), max(0, x1) : min(640, x2)] = 255
            points = cv2.goodFeaturesToTrack(
                self.gray, maxCorners=24, qualityLevel=0.02, minDistance=4, mask=mask
            )
            if points is not None:
                self.points[track.id] = points
        self.at = at

    def update(self, image, tracker, at):
        if self.gray is None or at - self.at > 0.8:
            return []
        gray = cv2.cvtColor(cv2.resize(image, (640, 360)), cv2.COLOR_BGR2GRAY)
        height, width = image.shape[:2]
        result = []
        for identifier, points in list(self.points.items()):
            track = tracker.tracks.get(identifier)
            if track is None or len(points) < 4:
                self.points.pop(identifier, None)
                continue
            moved, status, _ = cv2.calcOpticalFlowPyrLK(
                self.gray, gray, points, None, winSize=(15, 15), maxLevel=2
            )
            back, reverse, _ = cv2.calcOpticalFlowPyrLK(
                gray, self.gray, moved, None, winSize=(15, 15), maxLevel=2
            )
            good = (
                (status.reshape(-1) == 1)
                & (reverse.reshape(-1) == 1)
                & (np.linalg.norm(points - back, axis=2).reshape(-1) < 1.5)
            )
            if good.sum() < 4:
                self.points.pop(identifier, None)
                continue
            delta = np.median(moved[good] - points[good], axis=0).reshape(2) * [
                width / 640,
                height / 360,
            ]
            if np.linalg.norm(delta) > max(width, height) * 0.12:
                self.points.pop(identifier, None)
                continue
            track.box = (
                (np.array(track.box) + np.tile(delta, 2))
                .clip([0, 0, 0, 0], [width, height, width, height])
                .tolist()
            )
            track.latest = dict(track.latest)
            face_box = track.latest.get("face_box")
            if face_box is not None:
                track.latest["face_box"] = (
                    (np.array(face_box) + np.tile(delta, 2))
                    .clip([0, 0, 0, 0], [width, height, width, height])
                    .tolist()
                )
            self.points[identifier] = moved[good].reshape(-1, 1, 2)
            result.append(track)
        self.gray, self.at = gray, at
        return result
