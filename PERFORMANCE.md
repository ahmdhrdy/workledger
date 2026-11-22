# Local latency measurements · 18 October 2026

Laptop: Intel i7-8665U, CPU inference, two inference threads. These are development measurements with standard OpenCV fixtures; no real employee footage was used.

| Measurement | Median | 95th percentile |
|---|---:|---:|
| Original full model refresh, one face, 512×512 fixture | 111.07 ms | 124.45 ms |
| Updated full model refresh, same fixture | 61.02 ms | 79.04 ms |
| Original OpenCV SFace embedding stage | 45.66 ms | 59.55 ms |
| OpenVINO SFace embedding stage | 14.59 ms | 17.08 ms |
| Optical flow only, between model refreshes | 2.59 ms | 3.21 ms |

The embedding outputs had cosine similarity 1.0 on the test fixture. The change preserves SFace preprocessing and the existing enrollment vectors. OpenCV still performs landmark alignment; OpenVINO executes the embedding graph.

Fast flow updates move existing boxes. They do not perform new person detection, face recognition, body embedding extraction, or attendance confirmation. Three actual matching face-inference observations are still required by default. Full refresh rates remain configurable and constrained by the camera role and shared CPU load.

The one-face fixture contains no detected full body, so its full refresh does not represent the cost of a crowded office image. RTSP decode, network/camera buffering, JPEG encoding, local storage, browser rendering and concurrent workloads add latency. The app displays full processing time, tracking time and locally measured frame age separately. Frame age begins at local decode receipt, not the camera sensor; it does not measure camera/network delay.

Actual synthetic RTSP verification passed for face enrollment, confirmed IN/OUT events, local live views, workplace observations and HR approval. A separate real body-model test passed for unknown evidence → HR identity assignment → encrypted daily profile → tentative matching on a second identical-image stream → assigned-polygon time. Neither test establishes masked-face or cross-view accuracy.

No 10 ms full recognition target or twelve-camera hardware capacity has been established. Validate representative simultaneous streams and employee movements on the intended mini PC before recommending its capacity.

## Recognition response follow-up

Full recognition refreshes now schedule from processing start instead of processing completion. At a configured 4 FPS, a 100 ms model refresh previously added that processing time to the 250 ms interval before the next recognition attempt. Three matching face observations remain required for attendance.

Capture now retains a publication deadline across source frames. Regression tests verify a 12 or 25 FPS source can publish the configured 10 FPS; restarting the wait after every published frame previously reduced a 12 FPS source to about 6 FPS. Stalls resume without a catch-up burst. FFmpeg decoding uses one thread to reduce decoder buffering and competing CPU threads. Streams are still continuously drained into a bounded latest-frame queue.

Visible live previews request their next frame after 120 ms rather than 200 ms for up to four cameras; larger walls use 250 ms to limit aggregate requests. This is a polling delay after each response, not a guaranteed displayed frame rate. Hidden-page polling remains throttled.

Actual synthetic RTSP enrollment, confirmed IN/OUT, workplace observations and HR approval passed after these changes. Full processing readings varied from 101 to 147 ms during that run; these scheduling and viewing changes do not establish faster neural inference under concurrent CPU load.

## Preview and counting verification, 21 November 2026

A 1280x720 frame was read from the configured camera. Four people were visible. The previous retail person detector returned five regions, including split/duplicate regions while missing a back-facing person. YOLOX-Nano returned four correctly placed person regions on that frame. This single-frame check does not establish counting accuracy for all occlusion and movement cases.

On five repeated processing passes of that captured frame with an empty gallery, excluding the first pass, the previous pipeline median was 1492.9 ms. The updated pipeline median was 602.1 ms. The updated run skipped unnecessary face embeddings and reused body embeddings on stable tracks. These measurements describe processing of a repeated frame, not camera-to-browser latency or performance with a populated recognition gallery.

Frame replay tests cover delayed detections, independent head movement, brief detector gaps, expired identity labels and people with no visible face. Preview tracking does not create attendance observations. Video age starts at local decode receipt and excludes camera sensor/encoder and upstream RTSP buffering.

The current decoder uses two FFmpeg decode threads and converts only frames selected for publication. In an eight-second configured-camera probe, the source reported 1920x1080 at 25 FPS. The one-thread run reported 22 decoded FPS and the two-thread run 25.8 FPS over the decoder rolling window. This short probe is not an end-to-end latency measurement. Preview polling now targets a 67 ms cadence including request/decode time, with duplicate wall requests paused while a camera is expanded.


## Sticky identity verification

The persistent tracker introduces no new ML model. It uses a small Kalman state, global assignment and bounded in-memory lost-track records. Additional body embeddings are requested when association is uncertain or recovering, while locked interior face embeddings are scheduled approximately every five seconds on acceptable frontal observations.

A repeated captured four-person frame with a temporary in-memory face gallery acquired a lock after three observations, kept its track ID, and retained its identity after face detections were deliberately removed. Refresh times in that run were 1214.5, 920.6, 1755.3, 921.7 and 1970.8 ms under concurrent test/model load. This verifies model integration, not a speed improvement, live recognition accuracy or camera-to-browser latency. Real-room evaluation must include similarly dressed employees, overlapping people, seat replacement and genuine entry/exit crossings. The original phone recording is a recording of a monitor and is unsuitable as pixel-accurate recognition ground truth.


## Local RetinaFace verification, 22 November 2026

The supplied RetinaFace-ResNet50 ONNX exposes dynamic input and 16800 raw anchors at 640x640. Only CPU/Azure providers are installed, not CUDA. On the stored four-person camera frame, RetinaFace located three visible faces; the fourth person faced away. At 480 input the two detection passes took 1147/1243 ms with face scores 0.945/0.856/0.727. At 640 they took 2167/2073 ms with scores 0.990/0.981/0.932. These measurements include host load and are not recognition accuracy or camera-to-browser latency measurements. Default detector resolution remains 640 for far-field detail; 480 is configurable but trades spatial detail for processing time. Optical flow and EMA can improve displayed continuity; they do not make delayed neural results current.


## Continuous-tracking regression

The 25.8-second phone recording `stream_output.mp4` showed whole-overlay/count disappearance when analysis age crossed 5000 ms. The former preview guard was reproduced with one visible locked fixture: age 4.9 seconds returned a label/count; 5.1 seconds returned no labels and unavailable count. That neural-age guard is removed for reliably tracked entities. A deterministic 7-second replay with no new inference now retains one locked moving face continuously across the five-second boundary, while loss-of-feature/blank-scene tests drop geometry rather than inventing it. Delayed source-time geometry and score drops 0.45/0.44/0.20/0 are regression-tested separately. These tests verify behavior, not live identification accuracy or zero latency.

A read-only configured-camera probe after this change completed six neural refreshes and 74 sampled preview frames, with count 1 in every sample and no visual-worker errors. It used an empty in-memory test gallery and wrote no attendance events. No sample exceeded five seconds of analysis age in that short run; crossing the former cutoff is covered by the separate seven-second deterministic replay. Installed-model smoke tests also passed. These observations do not establish recognition accuracy or a displayed FPS guarantee.


## First-result display verification

The responsiveness correction stops starting the experimental dedicated visual worker, restores the prior between-refresh path and removes association's dependency on that worker. Raw detector boxes publish before quality gates, body appearance extraction and recognition. Per-face match progress and final results carry revisions for the same source timestamp, so labels need not wait for another frame to update. A first qualifying face match acquires preview identity immediately, while attendance retains three fresh observations independently. Thresholds and side-view preprocessing remain unchanged.

On the existing clear-photo fixture, installed models published one face box at 3267 ms, its first qualifying name at 4077 ms, and completed the full pass at 4599 ms under concurrent test load. This demonstrates stage ordering and saved post-detection waiting; it is not an end-to-end live benchmark or a promise of instant CPU inference. No database attendance event was written by that fixture. Regression tests also verify publication while an embedding call is deliberately blocked and visible boxes after quality rejection.
