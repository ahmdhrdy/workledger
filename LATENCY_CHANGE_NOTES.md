# Live pipeline latency audit and change note — 2026-11-23

## Evidence and limits

Reviewed `C:/Users/User/Downloads/stream_output.mp4`: 18.61 seconds, 557 recording frames at 29.93 FPS, 848x478. Recording FPS is not camera/preview FPS. Sampled visible footers show approximately 154–231 ms video age and 2661–4173 ms analysis age. The recording cannot establish camera-to-PC latency or a calibrated end-to-end FPS result.

On this PC (8 logical CPUs), replaying one recorded frame through actual local models found four people. With an empty employee gallery (zero AdaFace calls), two initial full passes took 2420.0 and 2238.3 ms. Face-stage time was 1948.3–2204.8 ms. Isolated RetinaFace calls subsequently took 2004.2–2117.1 ms; warmed AdaFace on a synthetic aligned BGR input took 258.3–262.3 ms per call (first call 603.4 ms). This demonstrates heavy detector cost even without recognition; it is not an employee recognition accuracy test.

Controlled RetinaFace thread comparison on the same frame:

| Threads | Worker spinning | Times (ms) | Maximum decoded-output delta vs baseline |
|---|---|---|---|
| 2 | enabled | 1989.6, 2069.6, 1852.1 | 0 |
| 2 | disabled | 1861.1, 1851.3, 1851.7 | 0 |
| 4 | disabled | 1026.3, 1083.7, 1055.7 | 0 |
| 4 | enabled | 1007.5, 990.6, 1035.9 | 0 |

Only the thread budget default was changed. Session spinning behavior was left intact. Full model replay with the new effective four-thread budget took 1477.8 and 1592.5 ms for four people and zero face embeddings. These short local measurements are not a promise of production throughput; simultaneous application load affects them.

A four-person preview replay (60 source frames, three renders per frame) measured duplicate-render median 0.7343 ms without result memoization vs 0.0056 ms with it; P95 0.9682 vs 0.0119 ms. Total replay time was 6684 vs 6079 ms. Optical-flow math was unchanged. The comparison is a duplicate-work microbenchmark, not live stream FPS.

## Root causes and existing protections

- `Inference.analyze` already skips embeddings for reliably locked tracks after the required attendance confirmations, within the existing 5-second interior / 0.5-second portal verification windows. Unknown/new/ambiguous/recovered tracks still require real inference. Those conditions and timing policies are unchanged. Making five newly unidentified people cost the same as one would require batching or reduced recognition work, outside this change's accuracy-preservation scope.
- `capture.capture_process` already runs in a separate killable process and `Decoder` already has a maxsize=1 latest-frame queue. JPEG is encoded once by the decoder. No unbounded RTSP queue was found, so the capture implementation was not replaced.
- `routes.frame` previously ran `PreviewTracker.render`, including source-time replay and per-person optical flow, while holding the preview lock. Each browser card/modal competed for that work. The previous background geometry worker ran only for active door configurations.
- Legacy event/evidence/context writes, daily body profile seeds, workstation observations and retention cleanup ran inside `VisionRuntime.loop`. Costs scale with people/events and storage delays postpone the next neural pass.
- Model pools already have bounded intra-op threads and inter-op=1. Two inference threads underused this eight-logical-CPU machine. Four materially improved the controlled detector benchmark; adding unbounded pools or parallel model calls was not justified.

## Implementation and expected effects

1. `Settings.inference_threads`: CPU-aware default `min(4, max(1, logical_cpu_count // 2))`; an explicit `WORKLEDGER_INFERENCE_THREADS` still wins. `.env.example` now explains the optional override. This PC defaults to four; lower-core PCs receive fewer. Detector timings above improved about 45–50% in isolation; full replay improved about 29–39%. No detector size, input processing or thresholds changed.
2. `VisionRuntime.preview_loop`: now serves every connected camera, including cameras without door zones. Publishes one immutable `(JPEG frame, overlay, result, anchors)` tuple per camera. It consumes the newest decoder frame and targets the configured live/visual cadence (capped at 15 FPS by the existing live policy). No camera-frame task backlog is created. Frame/geometry remain paired.
3. `routes.frame`: running runtimes return that tuple without acquiring the optical-flow lock or running tracking. Raw capture remains available. Non-started test/embedded runtimes retain synchronous fallback. An unchanged frame/revision returns 204 when the client supplies `after`; new match revisions on the same source frame still return 200.
4. `PreviewTracker`: memoizes exact frame/analysis revisions; `_seed` reuses an existing grayscale source frame when available. All optical-flow, face correction, quality, count and label suppression rules remain intact.
5. `LocalVision` preview: sends the last revision, skips duplicate JPEG decoding on 204, and pauses the card while the same camera's expanded preview or zone editor is open. Image and metadata are updated together.
6. New `BackgroundIO`: bounded FIFO with a single ordered storage worker, retries and status counters. Legacy events capture immutable identity/body/time data and one fixed event UUID before enqueue; retry after commit cannot duplicate the event. Observation/emission markers advance only after queue acceptance. Queued event timestamps remain the original capture timestamps. Daily seeds, observations and retention disk writes move off inference. Workstation zone geometry is read once per model iteration, rather than once per employee plus overlay generation.

`storage_pending`, `storage_error` and `storage_completed` are exposed by camera runtime state. The queue is bounded and in memory, not a durable broker: an abrupt process/power loss before persistence can lose queued work. A full queue rejects new jobs explicitly; event/observation markers are not advanced for rejected submissions. A storage failure can delay all ordered storage jobs, but does not wait in the video or inference threads. Regular template refresh and zone-config reads remain synchronous; this update does not claim all disk reads were removed. Door-zone event writing already had its own asynchronous service and remains unchanged.

7. `DailyGallery.add`: retain the same profile contents and 30-second sampling policy, but perform persistence outside the body-matching lock. Publish the updated in-memory profile after successful persistence; failures can retry without the sampling interval incorrectly suppressing the write. A blocked-disk regression test verifies matching can continue.

## Exact source blocks changed

Line spans identify the containing function after formatting. Within the long runtime loop, changes are restricted to I/O submissions, single-pass metadata/zone reuse and queue acceptance markers; recognition/attendance qualification predicates are retained.

| File | Function/class | Current lines |
|---|---|---|
| `src/workledger/vision/appearance.py` | `DailyGallery.add` | 87–122 |
| `src/workledger/config.py` | `Settings` | 9–71 |
| `src/workledger/vision/background.py` | `BackgroundIO` | 8–61 |
| `src/workledger/vision/preview.py` | `__init__` | 14–26 |
| `src/workledger/vision/preview.py` | `_seed` | 49–100 |
| `src/workledger/vision/preview.py` | `render` | 277–427 |
| `src/workledger/vision/runtime.py` | `__init__` | 48–88 |
| `src/workledger/vision/runtime.py` | `start` | 90–103 |
| `src/workledger/vision/runtime.py` | `preview_loop` | 105–160 |
| `src/workledger/vision/runtime.py` | `disconnect` | 216–237 |
| `src/workledger/vision/runtime.py` | `shutdown` | 239–246 |
| `src/workledger/vision/runtime.py` | `state` | 270–292 |
| `src/workledger/vision/runtime.py` | `_refresh_gallery` | 329–349 |
| `src/workledger/vision/runtime.py` | `queue_event` | 351–371 |
| `src/workledger/vision/runtime.py` | `seed_appearance` | 373–385 |
| `src/workledger/vision/runtime.py` | `write_event` | 387–466 |
| `src/workledger/vision/runtime.py` | `purge_retention` | 468–478 |
| `src/workledger/vision/runtime.py` | `loop` | 480–964 |
| `src/workledger/vision/routes.py` | `frame` | 359–445 |

Frontend scheduling blocks: `apps/web/src/LocalVision.tsx`, lines 165, 186, 202, 1082 (revision tracking/204 handling/duplicate-view pause). Environment example: `.env.example`, lines 7–8. New regression coverage: `tests/test_pipeline_latency.py`; updated `tests/test_door_zones.py` covers cached HTTP frame pairs, same-image revision changes, autonomous previews and committed-event retry idempotency. Frontend `apps/web/dist/` was regenerated. This document is `LATENCY_CHANGE_NOTES.md`.

## Protected AI files

SHA-256 comparison against the start-of-task snapshot confirms these seven files are byte-for-byte unchanged: `face_engine.py`, `face_align.py`, `retinaface.py`, `inference.py`, `tracking.py`, `pose_matcher.py`, `person_detector.py`. AdaFace BGR normalization, five-point alignment, face-size/blur/pose gates, matching thresholds, sticky identity transitions and detector postprocessing were not edited. Model files were not downloaded or replaced.

## Activate and verify

Restart the API once and hard-refresh the browser. An old explicit environment setting of `WORKLEDGER_INFERENCE_THREADS=2` retains two threads; remove that override to use the measured CPU-aware default or deliberately set 4 on this PC. Keep camera resolution/detector size unchanged for a fair comparison.

Compare the same multi-person scene for 30–60 seconds. In browser developer tools Network, `/frame` should return 200 for new frame/revision pairs and 204 for repeats. A modal should pause its matching background card. Inspect `X-Frame-Age-Ms`, `X-Preview-Tracking-Ms` and `X-Analysis-Age-Ms` separately. Watch camera `model_stages_ms`, `embeddings_computed`, `embeddings_skipped` and storage status through the authenticated local status response. Slow model recognition updates can remain even with smoother video. Confirm repeated attendance trips retain capture timestamps and that `storage_error` remains null and pending work drains.

The changes were replay/unit/API/build tested; a post-restart live RTSP acceptance run has not been performed in this task. No zero-latency, fixed FPS or new recognition-accuracy guarantee is made.

## Final verification

151 tests passed (one existing Starlette/httpx deprecation warning). Ruff checks, TypeScript no-emit check and Vite production build passed. Explicit inference_threads=2 still overrides the automatic four-thread default on this PC. The seven protected AI files passed the final byte-for-byte hash comparison.

Additional changed test function blocks:

- `tests/test_pipeline_latency.py:15–30` — `test_repeated_preview_requests_do_not_decode_or_replay_again`
- `tests/test_pipeline_latency.py:33–40` — `test_detector_seed_reuses_grayscale_history`
- `tests/test_pipeline_latency.py:43–65` — `test_slow_storage_does_not_block_submit_and_preserves_order`
- `tests/test_pipeline_latency.py:68–85` — `test_storage_retries_failed_job_before_next_job`
- `tests/test_pipeline_latency.py:88–105` — `test_event_queue_captures_track_before_it_changes`
- `tests/test_pipeline_latency.py:108–144` — `test_slow_appearance_write_does_not_hold_body_match_lock`
- `tests/test_pipeline_latency.py:48–51` — `first`
- `tests/test_pipeline_latency.py:73–76` — `flaky`
- `tests/test_pipeline_latency.py:114–116` — `slow_write`
- `tests/test_pipeline_latency.py:129–134` — `match`
- `tests/test_door_zones.py:319–354` — `test_preview_worker_observes_without_browser_for_all_connected_cameras`
- `tests/test_door_zones.py:357–398` — `test_frame_http_uses_published_pair_without_waiting_for_optical_flow`
- `tests/test_door_zones.py:401–414` — `test_background_event_retry_cannot_duplicate_committed_unknown_event`
