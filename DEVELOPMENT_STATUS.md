# WorkLedger local build 0.3

Updated 18 October 2026 in D:/Frig_Next.

Follow-up: a labelled top-right Log out button stays visible on desktop and mobile, revokes the server session, clears workspace state and reports failures with a retry. Browser checks passed at 1440px and 390px, including old-cookie rejection and persistence after reload. Recognition scheduling, source-frame cadence, decoder threading and live preview polling were improved; confidence thresholds and attendance confirmation requirements remain unchanged. All 51 automated tests, lint and the production build pass. See PERFORMANCE.md for details.

The requested corrections are integrated in the local webapp:

- Blank new employee/camera lists. Known unused generated records are archived while retaining history and releasing employee codes. Configured cameras and enrolled/observed people are preserved.
- IP/username/password onboarding with ONVIF/manual RTSP, attendance entry/exit or workplace purpose, chosen names and automatic live wall. Edit, start/stop, remove and expand controls.
- Combined employee metadata and initial face enrollment, salary, designation, nickname and employment dates. Editable profiles and additional photo/camera/masked samples. Readable Unicode nicknames above people.
- Encrypted daily appearance profiles: identity, clothing-color estimates and multiple body signatures. Confirmed-face/HR seeding, same-day restart persistence, site-local midnight expiry and independent permanent face enrollment.
- OpenVINO SFace acceleration and optical flow between model refreshes. Flow never confirms attendance. Conflicting identity assignments remain uncertain.
- Visual polygon creation/editing/removal, individual/shared assignments, multiple areas/cameras, meeting/break/transit areas, allowed time windows and overlap priority.
- Workplace time unions overlapping cameras, separates confirmed/tentative identity and unknown gaps, and excludes approved observed break credit up to the office allowance. It is not an automatic salary penalty or demonstrated productivity measure.
- Unmatched entry, exit and workplace face review: HR employee correction, visitor name/reason, false detections, preserved source evidence and classification history.
- Verified missing-movement corrections, voiding incorrect events, reviewed leave/absence and approval invalidation. Empty holidays/off days need no absence approval.
- Paid-holiday/unpaid-closure/working-day calendar, overtime/holiday pay policy, allowances/deductions, employment eligibility, stale-input blockers, completed-month finalization, one finalized run per site/month, CSV salary sheets and printable payslips.
- Administrator-managed HR/payroll/viewer accounts, password reset, disabled access, session revocation and last-administrator protection.
- Checked legacy database adoption with backup. Migration rehearsal preserved every existing row and passed foreign-key checks.

Verification: 47 automated tests pass; backend lint and frontend production build pass. Browser checks passed for actual enrollment, inline live views/nicknames, polygon drawing, calendar edits, salary approval/CSV, visitor/employee review, timezone-aware movement corrections and HR approval. Two synthetic RTSP suites passed: face -> IN/OUT -> HR approval; and body embedding -> unknown evidence -> HR assignment -> daily gallery -> tentative matching on a second identical-image stream -> assigned-polygon time -> daily reset retaining permanent face enrollment.

Performance: i7-8665U/CPU, two inference threads, one-face 512x512 fixture: full model refresh median 61.02 ms versus 111.07 ms before; optical flow alone median 2.59 ms. These do not establish 10 ms full recognition, total viewing latency, real cross-view accuracy or twelve-camera capacity. See PERFORMANCE.md.

Operational limits: real camera/mask validation, liveness, camera-topology calibration, rotating shift rosters, leave accrual, effective-dated salary history, automatic statutory payroll calculations, scheduled backups, business-record deletion and production cloud provisioning remain outside this local pilot. Office pay rules/rates must be configured and checked against approved salary examples. No zero-error recognition or production certification is claimed.

Runtime: .venv-ai (Python 3.12); weights/licenses .local/models; business database .local/workledger.db; encrypted vault .local/vision. Synthetic tests use separate databases/vaults under .local and do not enroll test people into the user workspace.
