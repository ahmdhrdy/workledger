import threading
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import cv2
import numpy as np

from .inference import normalized, rank


def day_window(at, timezone):
    local = datetime.fromtimestamp(at, UTC).astimezone(ZoneInfo(timezone))
    midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), local.tzinfo)
    return local.date().isoformat(), midnight.timestamp()


def color_name(bgr):
    pixel = np.uint8([[bgr]])
    hue, saturation, value = [int(v) for v in cv2.cvtColor(pixel, cv2.COLOR_BGR2HSV)[0, 0]]
    if value < 45:
        return "black"
    if saturation < 35:
        return "white" if value > 195 else "gray"
    if hue < 10 or hue >= 170:
        return "red"
    if hue < 23:
        return "brown" if value < 160 else "orange"
    if hue < 36:
        return "yellow"
    if hue < 84:
        return "green"
    if hue < 101:
        return "cyan"
    if hue < 133:
        return "blue"
    if hue < 155:
        return "purple"
    return "pink"


def clothing_colors(frame, box):
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in box]
    x1, x2 = max(0, x1), min(w, x2)
    y1, y2 = max(0, y1), min(h, y2)
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return {"upper": "unknown", "lower": "unknown", "method": "dominant-color estimate"}
    height, width = crop.shape[:2]

    def sample(top, bottom):
        patch = crop[
            int(height * top) : max(int(height * bottom), int(height * top) + 1),
            int(width * 0.25) : max(int(width * 0.75), int(width * 0.25) + 1),
        ]
        return (
            color_name(np.median(patch.reshape(-1, 3), axis=0).astype(np.uint8))
            if patch.size
            else "unknown"
        )

    return {
        "upper": sample(0.25, 0.55),
        "lower": sample(0.6, 0.9),
        "method": "dominant-color estimate; not garment classification",
    }


class DailyGallery:
    """Encrypted daily body signatures, seeded only by confirmed face or HR review."""

    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.cache = {}

    def profiles(self, site, at):
        with self.lock:
            profiles = self.cache.get(site)
            if profiles is None:
                profiles = self.store.daily_profiles(site, at)
            profiles = [p for p in profiles if p["expires_at"] > at]
            self.cache[site] = profiles
            return profiles

    def add(self, site, employee, camera, vector, at, timezone="Asia/Dhaka", details=None):
        if vector is None:
            return
        day, expires = day_window(at, timezone)
        with self.lock:
            profiles = self.profiles(site, at)
            existing = next(
                (p for p in profiles if p["employee_id"] == employee and p["day"] == day), None
            )
            if existing and at - existing["updated_at"] < 30 and camera in existing["cameras"]:
                return
            profile = existing or {
                "employee_id": employee,
                "day": day,
                "expires_at": expires,
                "created_at": at,
                "vectors": [],
                "cameras": [],
            }
            profile.update(details or {})
            profile["updated_at"] = at
            if camera not in profile["cameras"]:
                profile["cameras"].append(camera)
            profile["vectors"] = (profile["vectors"] + [normalized(vector).tolist()])[-12:]
            self.store.put_daily_profile(site, employee, day, expires, profile)
            if existing is None:
                profiles.append(profile)

    def match(self, site, vector, at, threshold, margin):
        if vector is None:
            return None, 0
        with self.lock:
            templates = [
                {"employee_id": p["employee_id"], "vector": v}
                for p in self.profiles(site, at)
                for v in p["vectors"]
            ]
            return rank(vector, templates, threshold, margin)

    def remove(self, site, employee):
        with self.lock:
            self.store.remove_daily_profile(site, employee)
            self.cache.pop(site, None)
