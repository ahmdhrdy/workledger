"""Union observations across cameras so overlapping views never double-count time."""

from collections import defaultdict


def summarize(records, start, end, break_allowance_seconds=0):
    by_camera = defaultdict(list)
    for record in records:
        by_camera[record["camera_id"]].append(record)
    intervals = []
    for rows in by_camera.values():
        rows.sort(key=lambda row: row["at"])
        for previous, current in zip(rows, rows[1:]):
            if 0 < current["at"] - previous["at"] <= 4 and current["zone"] == previous["zone"]:
                left, right = max(start, previous["at"]), min(end, current["at"])
                if right > left:
                    intervals.append(
                        (
                            left,
                            right,
                            previous["zone"],
                            bool(previous["counts_work"] and current["counts_work"]),
                            previous["method"] == current["method"] == "face",
                            previous.get("zone_kind")
                            if previous.get("zone_kind") == current.get("zone_kind")
                            else "unassigned",
                        )
                    )
    changes = defaultdict(list)
    for index, item in enumerate(intervals):
        changes[item[0]].append((index, True))
        changes[item[1]].append((index, False))
    active = set()
    totals = defaultdict(float)
    zones = defaultdict(float)
    previous = None
    for at, events in sorted(changes.items()):
        if previous is not None and active:
            # Work membership takes precedence in overlapping views; face wins ties.
            item = max(
                (intervals[index] for index in active),
                key=lambda value: (value[3], value[4], value[2]),
            )
            seconds = at - previous
            totals["observed_seconds" if item[4] else "tentative_seconds"] += seconds
            if item[3]:
                totals["work_confirmed_seconds" if item[4] else "work_tentative_seconds"] += seconds
            elif item[5] == "break":
                totals["break_seconds"] += seconds
            zones[item[2]] += seconds
        for index, add in events:
            if add:
                active.add(index)
            else:
                active.discard(index)
        previous = at
    observed = totals["observed_seconds"] + totals["tentative_seconds"]
    work = totals["work_confirmed_seconds"] + totals["work_tentative_seconds"]
    approved_break = min(totals["break_seconds"], max(0, break_allowance_seconds))
    eligible = max(0, observed - approved_break)
    window = max(0, end - start)
    return {
        **{
            key: round(totals[key])
            for key in (
                "observed_seconds",
                "tentative_seconds",
                "work_confirmed_seconds",
                "work_tentative_seconds",
            )
        },
        "window_seconds": round(window),
        "unknown_seconds": round(max(0, window - observed)),
        "coverage_percent": round(100 * observed / window, 1) if window else None,
        "work_percent_of_observed": round(100 * work / eligible, 1) if eligible else None,
        "approved_break_seconds": round(approved_break),
        "excess_break_seconds": round(max(0, totals["break_seconds"] - approved_break)),
        "eligible_observed_seconds": round(eligible),
        "zones": {key: round(value) for key, value in zones.items()},
        "samples": len(records),
        "last_seen": max((r["at"] for r in records), default=None),
    }
