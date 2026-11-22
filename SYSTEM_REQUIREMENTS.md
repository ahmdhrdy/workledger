# Standalone CCTV attendance product: requirements and open decisions

Updated: 15 October 2026. Source: user specification and subsequent answers.

18 October correction: new installations start with empty employee/camera lists. Onboarding distinguishes attendance entry/exit and workplace use and immediately displays named live views. Employee registration includes face, name/ID, salary/designation and overlay nickname. Daily appearance matrices expire at site-local midnight independently of permanent face enrollment. Polygons support individual/shared workplaces, meetings and configurable break areas/time. HR maintains holidays and reviews unknown identities as employees, visitors (with visit reason) or false detections. The user authorized sensible configurable payroll defaults. Current implementation and operational limits are recorded in DEVELOPMENT_STATUS.md.

The proposed complete engineering design is recorded separately in [SYSTEM_DESIGN.md](D:/Frig_Next/SYSTEM_DESIGN.md). Its defaults, sizing estimates and acceptance targets remain proposals until validated; the confirmed requirements below remain the source of user decisions.

## Confirmed scope

- Build an independent commercial product. Home Assistant and Frigate are architectural references, not runtime dependencies or required customer installations.
- Initial office: approximately 15 employees, one dedicated entrance camera, one dedicated exit camera, and approximately ten internal cameras.
- Support variable tenant/site/employee/camera counts, with capacity depending on hardware and actual stream/model benchmarks.
- Bangladesh is the initial deployment market.
- All user-facing administration is through a web application.
- Prefer low-cost cloud management and reporting; fully offline/on-premise deployment must also be supported by the shared application core.
- Minimize customer-site hardware cost and resource consumption as a primary design constraint. Cloud-managed sites run a lightweight capture/AI agent rather than the full payroll/business stack. Hardware requirements must be established by benchmarks, not assumed from the initial conservative estimates.
- Only events may leave the customer premises. Video, face crops, reference images, and biometric/appearance embeddings must not be uploaded. No remote live video viewing is permitted; live video and name overlays are local-webapp features only.
- Configurable retention: approximately three months to one year, according to customer preferences. Exact retention scope by data category remains to be defined.
- Web application login with username/password, tenant/site permissions, and administrative audit records.
- Employee enrollment, face-profile management, recognition, name overlays, entry/exit history, first-IN/last-OUT, away intervals, workspace intervals, health and reports.
- Identification must be camera-only, including employees whose mouths are covered. RFID, QR, PIN and other employee credential fallbacks are excluded. Mouth-covered recognition is a required acceptance test under actual site conditions, not a demonstrated capability or a zero-error guarantee.
- Use person appearance/re-identification to support identity continuity across rooms after an initial confirmed identity. This is a desired capability, not a validated accuracy claim.
- Employee/camera distance is expected to be roughly six to ten feet. Entrance, exit, and office coverage are intended to be complete. Actual optics, resolution, face visibility, overlaps and transition paths require inspection.
- Dedicated entry and exit cameras create attendance movement events; internal cameras track workspace presence.
- Shifts, overnight boundaries, grace periods, breaks, lunches, outside lunch, meetings, holidays, exceptions and other attendance policies vary by company and must be configured through the web application.
- Support multiple assigned workspaces, shared desks, meeting rooms and other zone types.
- Show missing events explicitly. Provide HR review and approval before classifying missing records as official absence. Approved leave, camera outages and missing observations must be distinguishable from confirmed absence. Corrections and approvals require an audit trail.
- Small tracking errors may be acceptable; thresholds have not been quantified. Attendance errors are unacceptable to the user, requiring a conservative confirmation, review and correction design.
- Include traditional attendance features plus CCTV presence analytics and built-in HR/payroll. Calculate salaries, overtime payments and deductions within this product. Do not depend on or integrate with external HR/payroll software. Site-specific formulas, components, calendars, approval policies and salary examples still need configuration and validation.
- Report export is required. Additional notifications are not requested.
- Ignore the development-stage sequence in Section 16 of the supplied specification; retain the functional requirements.

## Architecture implications, not additional user commitments

- The events-only boundary requires local video ingestion, person detection, face matching, re-identification and biometric storage. Cloud management does not eliminate the need for site compute.
- Cloud services can process event metadata, business rules, summaries, health and reporting. Employee metadata and management-command fields allowed through that connection need a precise schema.
- An offline edition needs local rules, storage, authentication, web UI, reporting and model/update import, without mandatory cloud calls.
- Site agents need an outbound authenticated connection, durable local event buffering, stable event IDs, capture timestamps and deduplication when reconnecting. Disconnection must not lose or double-count attendance events.
- Workspace intervals need confidence/coverage and explicit unknown states. A missing camera observation is not proof of physical absence or a valid exit.
- Appearance embeddings support candidate matching and handoff but are not unique permanent employee identifiers. Similar clothing, occlusion and camera changes can create identity switches.
- Camera-only face identification, especially with occlusion, cannot be promised to produce zero errors. The design must retain unresolved observations for review instead of forcing identities, and surface incomplete attendance. This exception-handling behavior does not add another employee identification credential.
- Cloud authentication must not provide a video relay, frame/crop upload or biometric export. Local enrollment and evidence review must preserve the same boundary.
- Payroll inputs should distinguish confirmed attendance, approved leave, approved absence and pending exceptions. Proposed control: block finalization for unresolved attendance affecting pay until an authorized user resolves it. This exact finalization behavior remains a design proposal.
- Workspace-presence percentages must remain separate from salary and deduction inputs unless an explicit, approved business policy is later provided; no such policy has been specified.

## Remaining configuration and validation inputs

1. Site evidence for sizing and recognition validation: camera models/streams, resolution, optics, layout, transition paths and available local compute. No camera-count/hardware or recognition guarantee has been established.
2. Office payroll examples: salary period and components, overtime calculation, leave/pay treatment, approved absence, late/early policy, deductions, rounding, effective dates and payroll approval roles. These are configurable business inputs, not reasons to re-open the confirmed standalone payroll decision.
3. Precise cloud event/management schema, roles and per-category retention. Usernames, employee metadata, payroll records and configuration require an explicit permitted-data schema; biometric data and video remain local.
4. Numerical recognition/tracking acceptance criteria and the review workflow for camera-only ambiguity. Do not resolve this by inventing an identity or promising perfect recognition.

## Proposed attendance baseline for confirmation during design

Employee records and local face enrollment; company/site/departments; configurable shifts and rosters; weekly days off and holiday calendars; leave/approved duty exceptions; late/early/overtime rules; entry/exit/away history; incomplete-record review and audited corrections; employee/day/month summaries; configurable workspace eligibility; tenant/site access control; health and outage history; salary structures and effective dates; payroll calculation, review and approval; payslips and payroll reports; CSV/XLSX/PDF reports.

These proposed details refine the user's request for traditional attendance features. They are not evidence that every payroll policy or report format has already been specified.

## Technical evidence consulted

- NIST mask-effect evaluations distinguish false matches and missed matches; their published results do not certify this site's cameras or candidate model: https://pages.nist.gov/frvt/html/frvt_facemask.html
- Person re-identification research documents camera variation, occlusion and similar-appearance ambiguity: https://openaccess.thecvf.com/content/ACCV2024/html/Nguyen_CrossViT-ReID_Cross-Attention_Vision_Transformer_for_Occluded_Cloth-Changing_Person_Re-Identification_ACCV_2024_paper.html

No application implementation, enrollment, biometric processing, salary decision or deployment was performed in this requirements pass.
