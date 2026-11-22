import math
from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def area(points):
    return (
        abs(
            sum(
                points[i][0] * points[(i + 1) % len(points)][1]
                - points[(i + 1) % len(points)][0] * points[i][1]
                for i in range(len(points))
            )
        )
        / 2
    )


def orientation(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def validate_polygon(points):
    if not 3 <= len(points) <= 32 or any(
        len(point) != 2 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in point)
        for point in points
    ):
        raise ValueError("A zone needs 3–32 normalized points between 0 and 1")
    if area(points) < 0.0005:
        raise ValueError("Polygon is too small or has no area")
    count = len(points)
    for i in range(count):
        a, b = points[i], points[(i + 1) % count]
        if a == b:
            raise ValueError("Duplicate polygon corner")
        for j in range(i + 1, count):
            if j == i + 1 or i == 0 and j == count - 1:
                continue
            c, d = points[j], points[(j + 1) % count]
            if (
                orientation(a, b, c) * orientation(a, b, d) <= 0
                and orientation(c, d, a) * orientation(c, d, b) <= 0
            ):
                if max(min(a[0], b[0]), min(c[0], d[0])) <= min(
                    max(a[0], b[0]), max(c[0], d[0])
                ) and max(min(a[1], b[1]), min(c[1], d[1])) <= min(
                    max(a[1], b[1]), max(c[1], d[1])
                ):
                    raise ValueError("Polygon edges cannot cross or touch")
    return points


def inside(point, points):
    x, y = point
    contained = False
    for i, a in enumerate(points):
        b = points[(i + 1) % len(points)]
        if (
            abs(orientation(a, b, point)) < 1e-9
            and min(a[0], b[0]) - 1e-9 <= x <= max(a[0], b[0]) + 1e-9
            and min(a[1], b[1]) - 1e-9 <= y <= max(a[1], b[1]) + 1e-9
        ):
            return True
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
            contained = not contained
    return contained


def membership(box, shape, zones, employee_id):
    height, width = shape[:2]
    point = ((box[0] + box[2]) / 2 / width, box[3] / height)
    matches = [
        zone
        for zone in zones
        if inside(point, zone["points"])
        and (zone.get("applies_all") or employee_id in zone.get("employees", []))
    ]
    if not matches:
        return {
            "id": "",
            "name": "Outside assigned workstations",
            "kind": "unassigned",
            "counts_as_work": False,
        }
    return sorted(
        matches, key=lambda zone: (-zone.get("priority", 0), area(zone["points"]), zone["id"])
    )[0]


def during_window(zone, at, timezone):
    start, end = zone.get("allowed_from"), zone.get("allowed_until")
    if not start or not end:
        return True
    local = datetime.fromtimestamp(at, UTC).astimezone(ZoneInfo(timezone)).strftime("%H:%M")
    return start <= local < end if start < end else local >= start or local < end
