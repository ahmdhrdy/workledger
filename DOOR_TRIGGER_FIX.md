# Doorway crossing and monitoring correction — 2026-11-24

## What was observed, and what cannot be reconstructed

Read-only inspection of the local database found Cam 64 configured as `entry`, active door geometry version 1, selected anchor `foot`, and zero `door_crossings` rows. Camera mode is not a prerequisite in DoorCrossingEngine/Service: active door geometry operates on entry, exit and interior cameras. Tests now verify all three modes.

The screenshot shows body-height polygons. The saved blue A polygon ends at normalized Y approximately 0.787 at its lowest corner; if the person's bottom-center foot marker is below that edge, the person can visibly overlap the polygon while their selected anchor is outside it. A full A–Transition–B or reverse stable traversal is required; touching one rectangle is insufficient. The previous application did not retain per-frame doorway diagnostics, so the exact condition that rejected the historical walk cannot be reconstructed from this screenshot alone. Saved polygons and anchor selection were not changed automatically.

## Code defects found and corrected

1. Preview IDs are provisional (negative) in early detector publication, then become authoritative after matching/tracker updates. `PreviewTracker.door_observations` discarded all negative IDs. Moving-person detector refreshes could therefore interrupt an otherwise continuous doorway path or change its state key. Doorway observations now carry a separate stable geometry trajectory ID through same-frame revisions and unambiguous, mutually unique current-body IoU associations. Matching requires IoU >= 0.5 and a 0.15 separation from alternatives; different known employees cannot share a trajectory. This continuity does not transfer face labels, vectors or attendance proof. Ambiguous geometry receives a new trajectory rather than guessing.
2. Body-geometry reliability and face-association reliability were conflated. A geometric traversal could disappear before generating even a pending/review row. Observed body anchors now drive geometry independently; ambiguous identity during a traversal creates a review record, never confirmed attendance. Face-only rectangles cannot supply reliable foot/torso anchors. Three fresh confirmations, attendance-qualified face evidence, the five-second evidence age limit, re-verification requirements and the saved debounce settings remain enforced.
3. The UI swallowed status/log polling failures, making an API error indistinguishable from an empty database. Errors are now visible. A dedicated polling log component is separate from the drawing form and also appears under the expanded live preview.
4. Missing/outside/unstable anchors, incomplete sequences and identity gates had no operator-facing explanation. Live diagnostics now expose region, normalized anchor, origin/transition progress, trigger reason, verification reason, camera mode, enabled state, processing age and worker errors. Stale track diagnostics expire from the status display. Geometry changes clear old diagnostic state.

## Persistence and logs

Valid complete crossings enter the existing asynchronous outbox. A pending record is retained when identity evidence is not yet sufficient, can become confirmed within the configured verification window, and otherwise becomes review. Identity ambiguity across a traversal is review-only. Confirmed events create one SourceEvent with the crossing UUID, preserving capture time and existing retry idempotency. No legacy one-hour duplicate suppression is added. Employee/site validation remains required.

Crossing JSON now includes employee ID/name, camera, saved door name, capture timestamp, status, match cosine and reason. Existing rows without those JSON details remain readable. No schema migration is required.

`uvicorn.error.workledger.door.crossing` and `.storage` emit INFO for stable region transitions, emitted crossings and successful persistence; DEBUG reports reason changes, and WARNING reports safe error types for writer retries. Preview geometry failures also log their error type once until recovery. Logs omit images, embeddings, credentials and employee names. Launch uvicorn with `--log-level debug` to see reason changes; the UI diagnostics are available without changing logging level.

## Operator steps for the supplied geometry

1. Restart the updated API and hard-refresh the browser once. Zone edits afterward still apply without restart.
2. Open Cam 64 -> Door zones. Live anchor testing starts enabled.
3. For the body-height rectangles shown, consider selecting Torso center and save; alternatively keep Foot and redraw polygons along the observed foot-marker path. Watch the marker pass through A, Transition and B. Choose based on the actual marker path; do not use the face rectangle to decide zone presence.
4. Confirm enabled state, matching saved/applied versions and recent processing frames. `outside zones` means geometry/anchor mismatch; `zone dwell pending` means the configured time/observation minimum has not been reached; `waiting for transition/destination` means the sequence is incomplete; `body anchor unreliable` means the system cannot validate body geometry.
5. Walk through slowly once in each direction. Recent doorway time logs refresh every two seconds. A completed crossing with insufficient identity evidence should show pending/review instead of silently vanishing. Only confirmed records contribute to attendance. Scores displayed as Match are cosine similarities, not calibrated confidence percentages.

## Source changes

| File | Function | Current lines |
|---|---|---|
| `src/workledger/vision/preview.py` | `_seed` | 50–103 |
| `src/workledger/vision/preview.py` | `_update_same_frame_metadata` | 105–140 |
| `src/workledger/vision/preview.py` | `_carry_door_ids` | 503–538 |
| `src/workledger/vision/preview.py` | `door_observations` | 540–571 |
| `src/workledger/vision/presentation.py` | `detection_overlay` | 8–114 |
| `src/workledger/vision/runtime.py` | `preview_loop` | 105–166 |
| `src/workledger/vision/runtime.py` | `loop` | 486–971 |
| `src/workledger/vision/door_crossing.py` | `note` | 98–108 |
| `src/workledger/vision/door_crossing.py` | `verification_reason` | 111–124 |
| `src/workledger/vision/door_crossing.py` | `observe` | 126–325 |
| `src/workledger/vision/door_crossing.py` | `verified` | 328–337 |
| `src/workledger/vision/door_service.py` | `status` | 64–86 |
| `src/workledger/vision/door_service.py` | `observe` | 88–108 |
| `src/workledger/vision/door_service.py` | `persist` | 110–188 |
| `src/workledger/vision/door_service.py` | `run` | 210–231 |
| `src/workledger/vision/door_routes.py` | `status` | 118–134 |
| `src/workledger/vision/door_routes.py` | `crossings` | 137–170 |

Frontend files: `apps/web/src/DoorZoneEditor.tsx` (diagnostics, visible errors, live anchors, separate log panel), new `DoorwayTimeLogs.tsx` (two-second non-overlapping polling, employee/direction/camera/zone/millisecond timestamp/status/match/reason table), `LocalVision.tsx` (panel under expanded live preview), `vision.css` (separate cards, scrollable table, responsive spacing). `apps/web/dist/` was regenerated.

Regression file: new `tests/test_door_trigger_pipeline.py` covers provisional/authoritative ID continuity, no face-only foot evidence, ambiguous-pair rejection, explicit outside-zone diagnostics, pending-to-confirmed promotion, movement through real PreviewTracker to DoorCrossingEngine, and API/DB persistence in all camera roles. This document is `DOOR_TRIGGER_FIX.md`.

Recognition detector/alignment/embedding/matcher/identity-lock logic was not changed. No model downloads or hardcoded production zones were added. The live camera has not been restarted or a new physical traversal observed by the agent during this task; operators must verify the saved anchor path as described above.
