# WorkLedger · local AI build 0.3

Standalone CCTV attendance and workplace-presence application with real local CPU inference, camera onboarding, face enrollment, HR review, holiday calendars, configurable payroll, payslips and salary-sheet approval. Frigate and Home Assistant are references, not dependencies.

**The RTSP-to-attendance pipeline is implemented and tested with a synthetic stream. Your physical camera, real-office accuracy and masked-face performance still need validation. This is a local pilot, not a claim of perfect recognition or production certification.**

## Run on this laptop

The Python 3.12 inference runtime and four models are installed. Double-click **Start WorkLedger.cmd** in the project folder, or run `.\scripts\start-local.ps1`. This starts both services in hidden background windows, checks readiness and writes logs to `.local/logs`. Run it again after restarting the laptop; automatic Windows startup is not configured.

For separate foreground terminals instead:

```powershell
Set-Location D:\Frig_Next
.\scripts\start-api.ps1
```

```powershell
Set-Location D:\Frig_Next
.\scripts\start-web.ps1
```

Open <http://127.0.0.1:5173>. API documentation: <http://127.0.0.1:8000/docs>. Both servers bind only to this laptop. No cloud account, Docker, GPU or internet connection is needed after installation. The cameras must be reachable from the laptop; Wi-Fi client isolation or VLAN separation can prevent this even on the same SSID.

For another installation, install uv, Node 22.12+ and pnpm, then run `scripts/install-local.ps1`. This installs the locked Python 3.12 environment in `.venv-ai`, web dependencies, verified model weights and a model self-test. Model installation requires internet; inference does not.

## Connect your cameras

1. Create the administrator account on first run or sign in to your existing workspace. New workspaces start with no employees or cameras. Known unused generated records from the previous build are archived from the screens without deleting their history; their employee codes are released for your actual team.
2. Use **Employees → Add employee** to enter name, unique ID, nickname, designation, department, salary, overtime rate and optional employment dates, then upload or capture one clear face. Metadata and initial face enrollment are saved together. Add more varied face samples later.
3. Open **Cameras → Onboard camera** and choose **Entry attendance**, **Exit attendance**, or **Workplace monitoring**. Entry and exit cameras create separate IN/OUT events. Workplace cameras only measure observed zone presence.
4. Enter the private camera IP address, username and password. Select automatic ONVIF discovery and its HTTP port, usually 80. Save & connect.
5. If discovery fails, enable ONVIF or use **Manual RTSP stream path**. Presets cover common Hikvision, Dahua and Uniview paths; a custom path is supported. Put credentials in their dedicated fields.
6. Cameras appear immediately on the live wall under their chosen names. Nicknames are rendered above people at a readable browser size, including Unicode names. Expand a view when needed. Camera controls support editing, start/stop and removal from the wall while preserving history. Model refresh time and fast tracking time are displayed separately.
7. Use **Cameras → Face enrollment** to upload clear photos or capture the selected employee from an online camera. Exactly one face must be visible. Add at least three varied samples. A separate labelled masked set can also be enrolled and tested.
8. Test arrivals/exits and review Attendance. Unmatched entry, exit and workplace detections appear in **Cameras → Identity review**; assign the employee HR recognizes, record a visitor with their visit reason, or dismiss a false detection. Reviewed classifications remain visible in the history. Reviews are audited and original source events remain intact.

IP/user/password discovery works when the camera supports and enables ONVIF media discovery. Some devices require an ONVIF account, clock synchronization or a vendor-specific RTSP path. Proprietary cloud-only cameras cannot universally be connected by IP. Compatibility with your camera is unverified until connected.

Use a clear frontal entrance view with sufficient face resolution. Frames are limited to 1280 pixels wide; live faces use a configurable 32-pixel minimum and enrollment uses a 65-pixel minimum. Both pass confidence, alignment and 64x64 sharpness checks. Distance alone does not establish usable recognition quality.

## Implemented local pipeline

- SCRFD `det_10g.onnx` detection and AdaFace `adaface_ir50_ms1mv2.onnx` BGR embeddings run through ONNX Runtime CPU. YOLOX-Nano runs person detection through ONNX Runtime. OpenVINO runs body re-identification. All four model weights are installed locally; see [MODEL_SOURCES.md](MODEL_SOURCES.md).
- Shared CPU models, two inference threads, configurable sampling, latest-frame buffers of size one, killable RTSP/TCP decoder processes, bounded open/read timeouts and reconnect backoff. Entry/exit cameras receive inference priority (up to 5 FPS); workplace sampling is capped at 2 FPS by default. Achieved rates depend on available CPU and decode load.
- Pose-adaptive matching defaults to score 0.45/margin 0.08 for frontal views and score 0.36/margin 0.06 for side views. Attendance additionally requires fresh score 0.45/margin 0.08 and three face confirmations. Calibrate these initial values using held-out employee and unenrolled-person footage.
- Stable face identification or an explicit HR identity review seeds an encrypted, site-scoped daily appearance matrix. It records employee identity, nickname, up to twelve body signatures, observed cameras and estimated upper/lower clothing colors. It survives an app restart during that day, expires at site-local midnight and is recreated from new observations the next day. Expired rows and short-lived review context are purged when running/startup; permanent face templates remain separate. Body matches stay tentative and cannot automatically create confirmed attendance.
- Dedicated portal cameras use stable face detection or configurable directional line crossing. Same-direction duplicates are suppressed using tracks and the most recent confirmed event.
- **Cameras → workstation target icon** opens a visual polygon editor. Draw individual/shared desks, meeting rooms, dining/break or transit areas; assign multiple employees or everyone. Configure work eligibility, overlap priority and optional allowed time windows in the site timezone. Meeting areas count as work by default. Bottom-center person-box membership determines the area.
- Workplace reports union overlapping camera intervals, separate confirmed/tentative identity, show work time, approved break credit, coverage and unknown time. Approved break-zone time is excluded up to the office break allowance from eligible observed time. Coverage is measured over elapsed scheduled shift time, not asserted attendance. Presence is not a proven productivity score.
- Optical flow moves boxes between model refreshes. Track association happens before face embedding extraction. Confirmed tracks reuse their display identity between scheduled verifications. Cached matches retain their own status and contribute no fresh attendance confirmations.
- Credentials, enrollment vectors and review thumbnails are encrypted in the local vault. Each approved source photo produces original and flipped AdaFace embeddings. Synthetic masked variants are opt-in and reviewable. Model-tagged NumPy gallery generations are written under `.local/vision/adaface/<company>/<site>` within the protected vault directory. The `.npy` exports contain unencrypted vectors. Keep the vault key with backups.

The supplied recognizer is a general pretrained model, not a custom model trained and validated on your employees with masks. Enrolling masked samples can be tested; uncertain matches remain unresolved. **Masked-face accuracy and zero errors are not guaranteed. Liveness/anti-spoofing is not implemented.** Photo/replay attacks are not addressed by this pilot.

## Business integration and remaining work

Employee records, salaries/overtime rates, camera metadata, single-shift/weekday/grace settings, confirmed IN/OUT intervals, re-entry/away time, missing-event flags, HR approval/leave/absence, CSV export, role checks, company-scoped queries and audits are implemented. Missing observations stay pending; changed events/policies invalidate approval.

**Office calendar** supports paid holidays, unpaid closures and working-day overrides. A paid holiday on a scheduled day preserves salary and needs no attendance approval. Work observed on an off day still requires HR review. HR approves regular overtime or holiday-work minutes separately; the webapp configures multipliers and an optional holiday hourly rate.

Payroll uses Decimal arithmetic: monthly base × approved paid units / scheduled units, plus approved overtime, holiday work and explicit allowances, less explicit deductions. Joining/leaving dates limit employment eligibility. Missing or stale approvals, unmatched portal events, inconsistent calendar inputs, unconfigured extra-work rates and negative net pay block finalization. Save a draft, review its salary sheet, then finalize after the month ends. Only one finalized run is allowed per site/month. Finalized figures are immutable; later input changes flag the snapshot rather than replacing it. CSV salary sheets and printable payslips (browser Save as PDF) are available from each saved run.

HR can add verified missing IN/OUT movements and void incorrect movements with reasons. Original source events remain in the audit trail; affected attendance must be reviewed again. Administrators can create HR, payroll, administrator and viewer logins, reset passwords and disable access. Role/password changes revoke sessions; the last administrator cannot be demoted.

Current limits: one site shift/weekly schedule, full-day leave decisions, current employee salary rates for unfinalized calculations, and a business-day boundary four hours before shift start. Effective-dated salary histories, complex rotating rosters/leave accrual, automatic statutory tax/benefit calculations, scheduled encrypted backups, business-record deletion and production cloud provisioning are not implemented. Configure real office pay rules and verify sample salary sheets before operational use. Workplace metrics never automatically reduce salary.

## Storage and privacy

- Business database: `.local/workledger.db`.
- Local camera/biometric vault: `.local/vision/local.db`; encryption key: `.local/vision/vault.key`.
- Model weights, upstream licenses and download manifest: `.local/models`.
- Live frame buffers stay in memory. Daily appearance matrices are encrypted locally and expire daily. Attendance/unmatched events retain a single encrypted local evidence image for HR review; evidence and workplace observations expire according to the site’s 90/365-day preference, checked hourly. Business/payroll/audit history is retained separately; its deletion policy still needs configuration. Continuous recording and evidence video clips are not implemented. Encrypted source review thumbnails and aligned augmentation previews are retained with approved samples.
- Video, enrollment and camera-control endpoints reject non-loopback clients. No remote live-view endpoint is provided. Keep development servers private and do not proxy local camera routes to the internet.
- A metadata ingestion endpoint and durable SQLite outbox support a future site/cloud split. This laptop build writes attendance directly to its local business database; automatic cloud synchronization is not configured.

Never commit `.local`, `.env`, camera credentials, photos or vault keys. Setup has no default password, is loopback-only and once per database. Production settings require PostgreSQL/HTTPS and disable development setup/local AI services; production provisioning and deployment validation remain pending.

## AdaFace enrollment migration

After restarting the API, use Cameras > Face enrollment to upload approved original photos again. Earlier ArcFace samples remain reviewable and are excluded from the AdaFace gallery. The model identity includes the ONNX file hash and BGR preprocessing version. Review saved augmentations and the active gallery version in Model status.

Gallery generation can be rebuilt from current approved AdaFace records using:

```powershell
.\.venv-ai\Scripts\python.exe scripts/rebuild-face-gallery.py
```

Frontal/side thresholds, live/enrollment detector thresholds, detector size, sharpness cutoff and verification intervals use `WORKLEDGER_` settings from `src/workledger/config.py`. The 64x64 sharpness cutoff is a new measurement scale and needs camera-specific validation.

## Verify and configure

Copy `.env.example` to `.env` to customize `WORKLEDGER_` settings. Relevant values: `LOCAL_AI`, `MODEL_DIR`, `LOCAL_DATA_DIR`, `INFERENCE_THREADS`, `INFERENCE_FPS`, `FACE_THRESHOLD`, `FACE_MARGIN`, `CONFIRMATION_FRAMES`, `REID_THRESHOLD`, `MAX_ACTIVE_CAMERAS`, `LIVE_FPS`. Do not lower thresholds without measuring false matches.

```powershell
.\.venv-ai\Scripts\python.exe -m workledger.vision.selftest
.\.venv-ai\Scripts\pytest.exe -q
.\.venv-ai\Scripts\ruff.exe check src tests migrations
pnpm --dir apps/web build
```

Automated tests cover attendance/payroll rules, holiday treatment, immutable finalization, adjustments, role/session boundaries, source-preserving corrections, blank setup, enrollment, daily expiry/restart/site scope, polygon membership, shared areas, break credit/time windows, observation union, Unicode labels, vault/credential handling, migration backup and sample archival. Browser checks exercised actual face enrollment, inline live views, polygon drawing, calendar edits, payroll approval/exports, visitor/employee reviews and manual corrections without uncaught page errors.

Optional developer reproduction of the synthetic RTSP integration (uses sample images, isolated databases and a temporary local MediaMTX server; not customer camera footage): install `imageio-ffmpeg` into `.venv-ai`, run `.venv-ai\Scripts\python.exe scripts/prepare-rtsp-fixtures.py`, then `.venv-ai\Scripts\python.exe scripts/verify-rtsp-pipeline.py --host <this-laptop-private-LAN-IP>`. Fixture preparation downloads official sample media and a MediaMTX release; it is not part of normal application startup.

An optional second test, `scripts/verify-workplace-pipeline.py --host <this-laptop-private-LAN-IP>`, exercises real body embeddings, HR assignment, encrypted daily profiles, tentative identity on a second synthetic stream, polygon work time and profile reset preserving enrollment. It uses the same pedestrian image on both streams; it does not validate real cross-camera accuracy.

Historical SFace benchmark on an i7-8665U/CPU with two inference threads: a one-face 512×512 fixture measured 61 ms median for a full model refresh (previously 111 ms), and optical flow alone measured 2.6 ms median. These are separate measurements, not 10 ms face recognition or total camera-to-screen latency. Full RTSP processing and multi-person/multi-camera load add work. Twelve-camera capacity remains unmeasured. See PERFORMANCE.md.

Local development startup now runs versioned migrations. A recognized legacy 0.2 unversioned schema is checked, backed up under `.local/backups`, adopted and upgraded; an unrecognized schema is rejected. Existing user employee/camera records are preserved. Production databases still require a separately reviewed Alembic deployment. PostgreSQL deployment remains unvalidated.

Sources: `src/workledger/vision` capture/discovery/inference/tracking/vault/local routes; `src/workledger` business/auth/outbox; `apps/web` integrated UI and new visual-monitoring logo; `tests`, `migrations`, `scripts` verification/schema/install/launch. Target scope is in SYSTEM_REQUIREMENTS.md and SYSTEM_DESIGN.md; current status is in DEVELOPMENT_STATUS.md.

## Preview timing update, 21 November 2026

Preview geometry is tracked on the displayed frames using a bounded in-memory grayscale history, independent of neural inference. Face tracking uses a face-only correlation search plus optical flow. The footer reports current tracked people, locally measured video age and neural analysis age. Frames and label metadata are applied together after browser image decoding. The camera wall pauses its duplicate request loop while that camera is expanded.

The preview publication cap defaults to 15 FPS. Workplace body embeddings are sampled per stable track every two seconds. Repeated unknown-face attempts use a one-second retry interval and retry sooner for changed pose or substantially improved quality. An empty AdaFace gallery skips face embedding extraction. Source capture still runs independently of these decisions.

Person counting now uses the pinned YOLOX-Nano export. Check desk-zone membership after changing the person detector because body boxes can differ from earlier retail-detector boxes.


## Persistent identity tracking

A person receives a camera-local preview identity on the first quality-qualified match satisfying cosine/margin and association checks. Attendance still requires three fresh qualifying observations. Invalid-quality and missing-face frames do not erase a lock. Fresh failed comparisons age candidate votes out, but cannot downgrade an established identity. The lock lasts for the reliably associated track lifetime, not across process restarts or camera/gallery resets.

Person association uses Kalman prediction and global one-to-one motion/appearance assignment. Ambiguous assignments withhold identity attribution. Lost tracks are buffered for five seconds, with an eight-second appearance/verified-face recovery memory measured from the last detection. Recovery preserves event state. Position alone never restores an identity. Recovery thresholds are conservative starting values, not measured office-wide guarantees.

Interior locked tracks request a new quality-qualified verification about every five seconds; usable side views can verify the same identity. Door cameras keep their shorter verification interval. Four sustained strong frontal votes (score >= 0.65 and runner-up margin >= 0.15) are required to switch a locked identity. A conflict suspends attendance attribution until resolved. Green means a locked, associated identity; "Verified" is its stored score, not a probability or a new measurement on every video frame. Hover for latest attempted score and verification age.

A face box follows detected/tracked face geometry. If that geometry becomes unreliable, a locked name can remain anchored above the person without drawing a fabricated face rectangle. Corner bracket styling is preserved. The preview footer is the single visible people-count source; lost-memory entries are excluded from that count.

Attendance uses locked identity plus reliable observed crossing geometry and qualifying face evidence no more than five seconds old. Recovery requires renewed face verification for attendance. No crossing is inferred across an observation gap exceeding 1.5 seconds. Tracking-only desk observations remain tentative. The existing database same-direction duplicate suppression (up to one hour) remains in force. A first lock or recovery does not itself mean an entry/exit event.

After updating, restart the API with `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-api.ps1` from the project root, then hard-refresh the browser. No face-model or enrollment-gallery conversion is required for this tracking update.


## RetinaFace + AdaFace deployment update

The active face detector is the supplied `.local/models/retinaface-resnet50.onnx`. No download is needed. The installed file accepts dynamic float32 NCHW input and exposes `bbox [1,N,4]`, `confidence [1,N,2]`, `landmark [1,N,10]`. The adapter resolves outputs by their dimensions, letterboxes in BGR, subtracts [104,117,123] without dividing by 255, generates ceil-sized stride 8/16/32 priors, and decodes variances [0.1,0.2]. NMS receives x,y,width,height, while downstream boxes remain x1,y1,x2,y2. Original frame boundary geometry is preserved for quality rejection.

Enrollment and live recognition use the same five-point 112x112 BGR similarity alignment and AdaFace [-1,1] normalization. Quality checks include minimum native face size, boundaries, eye-aligned landmark geometry, landmark-fit/symmetry and Laplacian variance on a normalized 64x64 crop (minimum 60). A pose ratio is not a calibrated degree measurement. The configured acquisition threshold is 0.42 with a margin of 0.08 between DIFFERENT employees; attendance retains its stricter independent threshold. There is no automatic mask classifier or unvalidated 0.35 mask shortcut. Low-quality side/profile observations cannot unlock an established identity.

The existing killable RTSP decoding process continues draining frames into a bounded latest-frame queue. Person tracking retains its Kalman/global assignment, appearance recovery and sticky identity. Display-only face EMA is 0.7 current plus 0.3 prior geometry projected with body movement; raw landmarks/boxes remain untouched for recognition and attendance. Preview replay history is bounded to five seconds to accommodate the heavier CPU detector. No face rectangle is fabricated after reliable head tracking is lost. This is SORT-style custom tracking, not the ByteTrack package.

Select Front, Left approximately 30 degrees, Right approximately 30 degrees or Masked when enrolling batches. Each accepted image remains an independent embedding; flips retain their own entries and reverse left/right pose tags. Actual masked photos are preferable to assuming synthetic masks establish accuracy. The recognizer weights remain pretrained, not retrained by uploads. Existing AdaFace samples remain usable, though adding representative RetinaFace-aligned references is advisable for evaluation.

### Optional PostgreSQL / pgvector gallery

The default `WORKLEDGER_GALLERY_BACKEND=local` continues working with SQLite and the encrypted local sample vault. To use pgvector, supply an existing PostgreSQL server with the pgvector extension available, configure `WORKLEDGER_DATABASE_URL`, run `uv run alembic upgrade head`, then set `WORKLEDGER_GALLERY_BACKEND=pgvector` and restart the API. PostgreSQL extension creation requires database permission. No PostgreSQL server, extension or package is downloaded/installed by this update, and the current SQLite database is not converted.

Migration `a91f7302c114` adds scope/model/generation/sample/pose fields and a vector(512) column, a scope index and HNSW index on PostgreSQL only. Gallery updates atomically export normalized, unaveraged per-sample embeddings. Matching is scoped to company/site/model/generation and groups by employee before choosing the top two. The default exact grouped query deliberately does not claim HNSW acceleration or guaranteed sub-10ms latency. A raw LIMIT 2 over sample rows is incorrect when both rows belong to the same employee. The optional PostgreSQL backend requires validation against your actual server before production use.

Restart the API and hard-refresh Chrome after this change. Initial locks still require suitable face pixels; detection does not imply recognition. Masked, extreme-profile and room-wide accuracy are not guaranteed. A CPU RetinaFace-ResNet50 detector is substantially heavier than SCRFD and cannot deliver zero inference delay on this machine.


### Side-view quality correction

A nose outside the horizontal eye span is no longer an automatic enrollment/live rejection. The shared gate checks finite five-point geometry, usable eye/mouth separation, landmark positions relative to the face bounds, and plausible nose/mouth ordering in a coordinate system rotated with the eye line. Five-point alignment residual (112px template), minimum symmetry, blur, resolution and frame-edge checks remain enforced. Nose offset is a normalized diagnostic, not a measured yaw angle.

Use Front, Left approximately 30 degrees and Right approximately 30 degrees to add separate accepted reference images. Quality-qualified side views may acquire or reverify the same identity using the existing cosine/margin/voting policy. A locked identity cannot switch based solely on strong side-view scores: switching also requires bounded eye-relative nose offset, the existing frontal-symmetry condition, strong margin and sustained votes. This correction does not lower matching thresholds, retrain AdaFace, accept hidden/collapsed landmarks or guarantee recognition from an extreme profile. Restart the API before retrying previously rejected photos.


## Continuous visual tracking and low match scores

A dedicated `visual-tracking` worker advances per-camera geometry at a target of 10 FPS (`WORKLEDGER_VISUAL_TRACKING_FPS`) without waiting for neural inference or browser requests. The frame route uses the same serialized preview tracker. RetinaFace/AdaFace still operate on source frames independently; detector association receives past-only visual geometry pinned to each source timestamp. A continuous visual history can keep an established track across slow detector refreshes without treating processing delay as disappearance. Ambiguous geometry or actual loss still uses guarded recovery.

The five-second neural-analysis age is now diagnostic, not a blanket instruction to clear overlays or the count. Reliable frame-to-frame visual tracking keeps boxes/locked labels alive past that age. Missing visual evidence expires geometry instead; unobserved gaps over 0.75 seconds do not count as continuity. Head-flow failure uses at most 1.5 seconds of bounded body-relative head prediction before retaining only the known person's name anchor. A genuinely hidden face is not assigned a fabricated persistent rectangle. Flow features are replenished after validated updates, and flow keeps the earlier three-point minimum with forward/backward consistency checks.

Low scores such as 0.45, 0.44 or 0.20 do not unlock an established reliably tracked identity. Scores are cosine similarities, not calibrated confidence percentages. Matching thresholds still apply when acquiring a new identity. A duplicate face claim suspends attendance evidence without clearing a geometrically reliable lock. A new candidate cannot steal a currently visible locked identity. Sustained strong contradictory identity evidence and ambiguous/lost tracks retain the existing safeguards.

Hover a preview label to see the track ID, geometry state, transition reason (`IDENTITY_LOCKED`, `VISUAL_CONTINUITY`, `LOCK_HELD_LOW_MATCH`, `RECOVERED`, etc.), last visual update age and latest attempted face score. `Verified` remains the last accepted score. These diagnostics distinguish a failed match from a new/lost track. Tracking never increases fresh face-confirmation votes or fabricates doorway crossings across unobserved detector gaps.

Restart the API and hard-refresh the browser to activate this change. It corrects identity/overlay timing but does not reduce RetinaFace-ResNet50 neural processing time or establish measured office-wide recognition accuracy.


### Detector correction of flow drift

A newly detected face, replayed into the displayed frame's time domain, now takes precedence over the prior optical-flow face estimate. Body continuity can still preserve the track ID and locked identity, but cannot suppress a fresh face-position correction. The experimental 50-percent surviving torso-point rule was reverted to the earlier three-consistent-point rule. Side-view preprocessing, recognition thresholds, identity memory and bracket styling are unaffected by this correction.


## Current responsiveness policy: first-result preview

The camera card's FPS requested value is a scheduling request for full inference, not video FPS. The effective configured ceiling is min(camera target, 2 FPS for interior or 5 FPS for entry/exit by default). Actual full-refresh throughput is also bounded by processing time; a 3065 ms pass can sustain approximately 0.33 full refreshes per second, even at a 4 FPS request. The card now exposes the existing measured full-refresh rate instead of "Tracking pending" when no separate tracking timing exists. Raising the requested FPS cannot make that model faster.

New detections are published through an inference progress callback before landmark quality gates, recognition embeddings or body appearance extraction. Each completed face match can update that same source frame's preview immediately. Overlay revisions distinguish detection/match/completed stages even when the source timestamp is unchanged. A detected rectangle therefore does not require CANDIDATE/LOCKED or a successful face-quality check. Detection confidence still applies to finding a face in the first place.

The first valid match displays a sticky identity immediately, using unchanged cosine/margin and side-view quality settings. Later failed matches/blur/side transitions do not downgrade that established track's displayed identity. Three fresh observations remain required independently for attendance, and body-only similarities cannot acquire a face identity. Recognition is not cached before those independent fresh observations have been gathered. "Single-frame" here means no additional confirmation-frame wait for display; it does not mean zero model execution time or guaranteed correct identification from one image.

The experimental dedicated visual worker is no longer started, and model association no longer depends on its snapshot/hint path. Existing request-driven preview flow and between-refresh tracking are used. The 0.75-second blanket geometry reset is removed; forward/backward flow and correlation still validate a resumed update. Long gaps restart continuity evidence rather than clearing every rectangle merely because a timer elapsed. The five-second neural-age overlay cutoff remains removed for reliably tracked entities. Fresh detections correct older flow positions. No new detector, alignment template, model weights, yaw threshold or blur threshold is introduced by this correction.

Restart the backend and hard-refresh the frontend. On first use, wait for model loading and the first RetinaFace result. Accurate face geometry cannot be invented during complete occlusion; head prediction remains bounded. Historical continuous-worker and multi-frame-preview descriptions above describe superseded iterations; this section states the current policy.

Unidentified faces are evaluated on every available full-inference observation; the failed-match retry timer cannot suppress their next usable observation. Failed verification retries for already locked identities may still be throttled without erasing their displayed name. Same-source match metadata updates reuse current geometry rather than replaying video again.


## User-drawn door attendance zones

Restart the API once after installing this update so Alembic creates `camera_zones`, `camera_zone_versions` and `door_crossings`. Existing cameras are unchanged until an administrator saves an active door configuration. Subsequent geometry saves require no restart or camera reconnect.

Open Cameras, expand a camera preview and choose **Door zones / Entry–Exit**. Name and draw three separate polygons: A (one side), Transition (doorway), B (other side). Click vertices; click the first vertex to close. Drag vertices or the whole polygon, or enter precise normalized coordinates. No default geometry is created. Keep polygon interiors separate and make the transition wide enough for several observed frames. Flip the direction so the displayed ENTRY arrow points into the room. Select foot anchors for a visible lower body; use torso anchors only with polygons calibrated to torso positions when the lower body is occluded. Preview live anchor points, then save. Saved/applied versions and writer errors are shown in the editor, with recent crossing records below it.

A stable A–Transition–B traversal records ENTRY; its reverse records EXIT (reversed when the direction is flipped). Every completed trip is retained, including multiple trips per day. Daily attendance uses **first ENTRY and last EXIT**, preserving intermediate event times and inside/away intervals. An incomplete traversal, turnback, unreliable predicted anchor or excessive observation gap cannot create confirmed attendance. A locked preview label alone is insufficient: three fresh qualifying face observations are still required. Crossings awaiting that evidence remain pending, then become review records if the verification window expires. Review records do not become confirmed payroll attendance automatically.

Coordinates are ratios in [0, 1]; resizing the same field of view preserves geometry. Moving the camera, changing its crop or rotating the image requires redrawing zones. Saving resets in-progress trajectories so an old path cannot trigger against new geometry. Existing legacy attendance is bypassed only while that camera has an active door configuration. Workstation polygons remain separate.

Authenticated local endpoints: GET/PUT `/api/v1/local/cameras/{id}/door-zone`, GET `/door-zone/status`, GET `/door-crossings`. Saves require the last `expected_version`; conflicting edits return 409. Configuration is committed before being installed under a runtime lock; a one-second database refresh recovers missed local updates. A separate writer persists UUID-idempotent crossing/source events without the legacy one-hour same-direction suppression. Monitor writer errors and pending writes. The in-memory queue is not a durable message broker; a process crash before persistence can lose queued work. Validate the actual doorway/camera timing before operational use.

Employee enrollment is unchanged: the default local backend stores encrypted sample data in `vision/store.py` and normalized per-sample NumPy gallery generations in `vision/gallery_index.py`; `vision/pose_matcher.py` matches them. It does not train AdaFace weights. `vision/pgvector_gallery.py` is the optional PostgreSQL vector backend and is used only when `WORKLEDGER_GALLERY_BACKEND=pgvector` and PostgreSQL are configured.
