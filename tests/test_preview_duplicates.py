"""Display-only duplicate suppression must not change recognition or tracking state."""

from copy import deepcopy

from test_preview import analysis, frame, scene

from workledger.vision.preview import PreviewTracker


def label(identifier, x=0.2, y=0.2, width=0.1, height=0.15, **extra):
    return {
        "track_id": identifier,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "name": "Checking",
        "locked": False,
        "confirmed": False,
        **extra,
    }


def test_overlapping_face_boxes_draw_once_preferring_locked_name():
    preview = PreviewTracker()
    labels = [label(1), label(2, x=0.21, y=0.22, name="Employee", locked=True, employee_id="one")]
    assert preview._visible_labels(labels) == [labels[1]]


def test_three_old_new_provisional_overlays_render_one_face():
    preview = PreviewTracker()
    preview.entities = {
        1: {"carried": True},
        2: {"carried": False, "detected_at": 5},
        -1: {"carried": True},
    }
    labels = [label(1, x=0.21), label(2, x=0.22), label(-1, x=0.2)]
    assert preview._visible_labels(labels) == [labels[1]]


def test_separate_nearby_faces_and_different_locked_employees_are_retained():
    preview = PreviewTracker()
    labels = [label(1), label(2, x=0.31)]
    assert preview._visible_labels(labels) == labels
    labels = [
        label(1, locked=True, employee_id="one"),
        label(2, x=0.21, locked=True, employee_id="two"),
    ]
    assert preview._visible_labels(labels) == labels


def test_body_overlap_does_not_suppress_separate_faces():
    preview = PreviewTracker()
    preview.entities = {1: {"box": [1, 1, 100, 200]}, 2: {"box": [1, 1, 100, 200]}}
    labels = [label(1), label(2, x=0.4)]
    assert preview._visible_labels(labels) == labels


def test_nested_duplicate_and_carried_moving_duplicate():
    preview = PreviewTracker()
    labels = [label(1), label(2, x=0.21, y=0.21, width=0.08, height=0.13)]
    assert len(preview._visible_labels(labels)) == 1
    preview.entities = {1: {"carried": True}, 2: {"carried": False}}
    labels = [label(1), label(2, x=0.25)]
    assert preview._visible_labels(labels) == [labels[1]]


def test_preview_filter_does_not_modify_tracks_scores_or_count():
    preview = PreviewTracker()
    source = frame(scene(), 10)
    persons = [
        {
            "id": i,
            "box": [100, 60, 220, 260],
            "face_box": [120 + i, 80, 190 + i, 150],
            "label": {"name": "Employee", "locked": True, "employee_id": "one", "score": 0.58},
        }
        for i in (1, 2)
    ]
    metadata = analysis(source, persons)
    original = deepcopy(metadata)
    result = preview.render(source, metadata)
    assert len(result["labels"]) == 1
    assert set(preview.entities) == {1, 2} and set(preview.hints_at(10)) == {1, 2}
    assert result["people"] == 2 and result["labels"][0]["score"] == 0.58
    assert metadata == original


def test_name_anchor_without_face_rectangle_is_not_spatially_suppressed():
    preview = PreviewTracker()
    labels = [label(1, width=0, height=0), label(2)]
    assert preview._visible_labels(labels) == labels
