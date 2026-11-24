import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  Camera,
  Check,
  CircleAlert,
  Cpu,
  Eye,
  Focus,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  ScanFace,
  ShieldCheck,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { api, post, minutes } from "./api";
import type { Employee } from "./api";
import CameraZones from "./CameraZones";
import DoorZoneEditor from "./DoorZoneEditor";
import DoorwayTimeLogs from "./DoorwayTimeLogs";
import { EnrollmentPhotos, SavedEnrollmentSamples } from "./EnrollmentPhotos";
import type { EnrollmentSample } from "./EnrollmentPhotos";
import DailyProfiles from "./DailyProfiles";
import IdentityReview from "./IdentityReview";
import type { IdentityEvent } from "./IdentityReview";

type LocalCamera = {
  id: string;
  name: string;
  role: "entry" | "exit" | "interior";
  target_fps: number;
  state: string;
  message?: string;
  host?: string;
  username?: string;
  connection?: string;
  onvif_port?: number;
  rtsp_port?: number;
  zone_name?: string;
  zone_type?: string;
  trigger?: string;
  line_axis?: string;
  line_position?: number;
  line_direction?: string;
  last_frame?: number;
  inference_ms?: number;
  tracking_ms?: number;
  full_refresh_fps?: number;
  frame_age_ms?: number;
  people?: number;
  recognized?: number;
  has_password?: boolean;
};
type ModelStatus = {
  model_state: string;
  message: string;
  missing_models: string[];
  device: string;
  models: string[];
  face_threshold: number;
  face_margin: number;
  side_face_threshold?: number;
  side_face_margin?: number;
  attendance_face_threshold?: number;
  confirmation_frames: number;
  inference_fps_limit: number;
  portal_fps_limit: number;
  model_id?: string;
  gallery_generations?: {
    site: string;
    version: string;
    samples: number;
    embeddings: number;
  }[];
  gallery_errors?: { site: string; error: string }[];
};
type Enrollment = {
  employee_id: string;
  name: string;
  samples: EnrollmentSample[];
  legacy_samples?: EnrollmentSample[];
};
type Presence = {
  items: {
    employee_id: string;
    name: string;
    observed_seconds: number;
    tentative_seconds: number;
    zones: Record<string, number>;
    samples: number;
    work_confirmed_seconds: number;
    work_tentative_seconds: number;
    unknown_seconds: number;
    coverage_percent: number | null;
    work_percent_of_observed: number | null;
    approved_break_seconds: number;
  }[];
  notice: string;
};
type Tab =
  | "Cameras"
  | "Face enrollment"
  | "Presence"
  | "Identity review"
  | "Model status"
  | "Daily matrices";

function Preview({
  camera,
  intervalMs = 67,
  paused = false,
}: {
  camera: LocalCamera;
  intervalMs?: number;
  paused?: boolean;
}) {
  const [source, setSource] = useState("");
  const [peopleCount, setPeopleCount] = useState<number | null>(null);
  const [analysisAge, setAnalysisAge] = useState<number | null>(null);
  const [videoAge, setVideoAge] = useState<number | null>(null);
  const [geometry, setGeometry] = useState<{
    zones?: { name: string; points: number[][] }[];
    line?: { axis: string; position: number } | null;
    door?: {
      version: number;
      entry_forward: boolean;
      zones: { role: string; name: string; points: number[][] }[];
    } | null;
    anchors?: { track_id: number; point: number[]; reliable: boolean }[];
  }>({});
  const [error, setError] = useState("");
  const [labels, setLabels] = useState<
    {
      name: string;
      x: number;
      y: number;
      confirmed: boolean;
      locked?: boolean;
      track_id?: number;
      tracking_state?: string;
      transition_reason?: string;
      last_visual_age_ms?: number;
      latest_match_score?: number;
      last_verified_age_ms?: number;
      method: string;
      score: number;
      detection_score?: number;
      identity_state?: string;
      quality_reason?: string;
      source_width?: number;
      source_height?: number;
      width?: number;
      height?: number;
    }[]
  >([]);
  useEffect(() => {
    let cancelled = false,
      current = "",
      timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    let pending = "";
    let revision = "";
    async function poll() {
      const began = performance.now();
      let interval = intervalMs;
      if (document.hidden || paused) {
        timer = setTimeout(poll, 500);
        return;
      }
      try {
        const response = await fetch(
          `/api/v1/local/cameras/${camera.id}/frame${revision ? `?after=${encodeURIComponent(revision)}` : ""}`,
          {
            credentials: "same-origin",
            cache: "no-store",
            signal: controller.signal,
          },
        );
        if (!response.ok)
          throw new Error(
            "Waiting for a recent frame. Check the camera connection status.",
          );
        if (response.status === 204) {
          if (!cancelled) timer = setTimeout(poll, interval);
          return;
        }
        const blob = await response.blob();
        if (cancelled) return;
        const next = URL.createObjectURL(blob);
        pending = next;
        const decoded = new Image();
        decoded.src = next;
        await decoded.decode();
        if (cancelled) {
          URL.revokeObjectURL(next);
          pending = "";
          return;
        }
        revision = response.headers.get("X-Preview-Revision") || "";
        const previous = current;
        current = next;
        pending = "";
        setSource(next);
        if (previous)
          requestAnimationFrame(() => URL.revokeObjectURL(previous));
        const frameAge = Number(response.headers.get("X-Frame-Age-Ms"));
        setVideoAge(
          Number.isFinite(frameAge) && frameAge >= 0 ? frameAge : null,
        );
        const age = Number(response.headers.get("X-Analysis-Age-Ms"));
        setAnalysisAge(Number.isFinite(age) && age >= 0 ? age : null);
        const count = Number(response.headers.get("X-People-Count"));
        setPeopleCount(Number.isFinite(count) && count >= 0 ? count : null);
        const geometryHeader = response.headers.get("X-Workledger-Geometry");
        setGeometry(
          geometryHeader
            ? JSON.parse(
                new TextDecoder().decode(
                  Uint8Array.from(atob(geometryHeader), (c) => c.charCodeAt(0)),
                ),
              )
            : {},
        );
        const metadata = response.headers.get("X-Workledger-Labels");
        setLabels(
          metadata
            ? JSON.parse(
                new TextDecoder().decode(
                  Uint8Array.from(atob(metadata), (c) => c.charCodeAt(0)),
                ),
              )
            : [],
        );
        setError("");
      } catch (err) {
        if (pending) {
          URL.revokeObjectURL(pending);
          pending = "";
        }
        interval = 1000;
        if (!cancelled) {
          setError((err as Error).message);
          setSource("");
          setLabels([]);
          setPeopleCount(null);
          setAnalysisAge(null);
          setVideoAge(null);
        }
      }
      if (!cancelled)
        timer = setTimeout(
          poll,
          Math.max(0, interval - (performance.now() - began)),
        );
    }
    void poll();
    return () => {
      cancelled = true;
      controller.abort();
      clearTimeout(timer);
      if (pending) URL.revokeObjectURL(pending);
      if (current) URL.revokeObjectURL(current);
    };
  }, [camera.id, intervalMs, paused]);
  return (
    <div className="vision-preview">
      {source ? (
        <div className="preview-image-wrap">
          <img src={source} alt={`Local live camera: ${camera.name}`} />
          <svg
            viewBox="0 0 1 1"
            preserveAspectRatio="none"
            aria-hidden="true"
            style={{
              position: "absolute",
              inset: 0,
              width: "100%",
              height: "100%",
              pointerEvents: "none",
            }}
          >
            {geometry.door?.zones.map((zone) => (
              <polygon
                key={`door-${zone.role}`}
                points={zone.points.map((p) => p.join(",")).join(" ")}
                fill={
                  zone.role === "transition"
                    ? "#e4a321"
                    : zone.role === "a"
                      ? "#2f80ed"
                      : "#2a9b62"
                }
                fillOpacity={0.15}
                stroke={zone.role === "transition" ? "#e4a321" : "#fff"}
                strokeWidth={0.003}
              />
            ))}
            {geometry.door &&
              (() => {
                const zone = geometry.door;
                const ordered = ["a", "transition", "b"].map((role) =>
                  zone.zones.find((z) => z.role === role),
                );
                if (ordered.some((z) => !z?.points.length)) return null;
                const centers = ordered.map((z) =>
                  z!.points.reduce(
                    (a, p) => [
                      a[0] + p[0] / z!.points.length,
                      a[1] + p[1] / z!.points.length,
                    ],
                    [0, 0],
                  ),
                );
                if (!zone.entry_forward) centers.reverse();
                return (
                  <g>
                    <defs>
                      <marker
                        id={`door-entry-${camera.id}`}
                        viewBox="0 0 10 10"
                        refX="9"
                        refY="5"
                        markerWidth="7"
                        markerHeight="7"
                        orient="auto"
                      >
                        <path d="M 0 0 L 10 5 L 0 10 z" fill="#fff" />
                      </marker>
                    </defs>
                    <polyline
                      points={centers.map((p) => p.join(",")).join(" ")}
                      fill="none"
                      stroke="#fff"
                      strokeWidth={0.003}
                      markerEnd={`url(#door-entry-${camera.id})`}
                    />
                    <text
                      x={centers[1][0]}
                      y={centers[1][1]}
                      fontSize={0.025}
                      fill="#fff"
                    >
                      ENTRY
                    </text>
                  </g>
                );
              })()}
            {geometry.anchors?.map((anchor) => (
              <circle
                key={`anchor-${anchor.track_id}`}
                cx={anchor.point[0]}
                cy={anchor.point[1]}
                r={0.005}
                fill={anchor.reliable ? "#fff" : "#aaa"}
              />
            ))}
            {geometry.zones?.map((zone, index) => (
              <g key={index}>
                <polygon
                  points={zone.points.map((point) => point.join(",")).join(" ")}
                  fill="none"
                  stroke="#41b9eb"
                  strokeWidth={0.002}
                />
                {zone.points[0] && (
                  <text
                    x={zone.points[0][0]}
                    y={zone.points[0][1]}
                    fill="#41b9eb"
                    fontSize={0.02}
                  >
                    {zone.name}
                  </text>
                )}
              </g>
            ))}
            {geometry.line && (
              <line
                x1={
                  geometry.line.axis === "horizontal"
                    ? 0
                    : geometry.line.position
                }
                x2={
                  geometry.line.axis === "horizontal"
                    ? 1
                    : geometry.line.position
                }
                y1={
                  geometry.line.axis === "horizontal"
                    ? geometry.line.position
                    : 0
                }
                y2={
                  geometry.line.axis === "horizontal"
                    ? geometry.line.position
                    : 1
                }
                stroke="#32b4f0"
                strokeWidth={0.003}
              />
            )}
          </svg>
          {labels
            .filter((label) => label.width && label.height)
            .map((label, index) => {
              const width = label.width! * (label.source_width ?? 1280);
              const height = label.height! * (label.source_height ?? 720);
              const length = Math.max(
                3,
                Math.min(14, Math.floor(width / 4), Math.floor(height / 4)),
              );
              return (
                <svg
                  key={`box-${index}`}
                  viewBox={`0 0 ${width} ${height}`}
                  preserveAspectRatio="none"
                  aria-hidden="true"
                  style={{
                    position: "absolute",
                    pointerEvents: "none",
                    overflow: "visible",
                    left: `${label.x * 100}%`,
                    top: `${label.y * 100}%`,
                    width: `${label.width! * 100}%`,
                    height: `${label.height! * 100}%`,
                    color:
                      label.locked || label.confirmed ? "#559b32" : "#e1a51e",
                  }}
                >
                  <rect
                    x={0}
                    y={0}
                    width={width}
                    height={height}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={1}
                  />
                  <path
                    d={[
                      `M ${length} 0 H 0 V ${length}`,
                      `M ${width - length} 0 H ${width} V ${length}`,
                      `M 0 ${height - length} V ${height} H ${length}`,
                      `M ${width - length} ${height} H ${width} V ${height - length}`,
                    ].join(" ")}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={2}
                    strokeLinecap="butt"
                    strokeLinejoin="miter"
                  />
                </svg>
              );
            })}
          {labels.map((label, index) => (
            <span
              key={index}
              className={`person-overlay ${label.locked || label.confirmed ? "confirmed" : "tentative"}`}
              style={{
                left: `${label.x * 100}%`,
                top: `max(25px,${label.y * 100}%)`,
              }}
              title={[
                label.method,
                `Track #${label.track_id ?? "—"}: ${label.tracking_state ?? "—"}`,
                label.transition_reason,
                `Visual age ${label.last_visual_age_ms ?? "—"} ms`,
                `Latest match: ${label.latest_match_score ?? "not attempted"}`,
                `Verified ${label.last_verified_age_ms ?? "—"} ms ago`,
                label.quality_reason,
              ]
                .filter(Boolean)
                .join(" | ")}
            >
              <strong>{label.name}</strong>
              <small>
                Det {((label.detection_score ?? 0) * 100).toFixed(1)}%
                {label.score > 0
                  ? `   ${label.locked ? "Verified" : "Match"} ${label.score.toFixed(3)}`
                  : ""}
              </small>
              <small>
                {label.identity_state === "tracking"
                  ? "tracking"
                  : label.method === "body_tentative"
                    ? "tentative"
                    : label.confirmed
                      ? "identified"
                      : "checking"}
              </small>
            </span>
          ))}
        </div>
      ) : (
        <div>
          <Camera size={35} />
          <p>{error || "Opening local preview…"}</p>
        </div>
      )}
      <span className="local-view-label">
        <ShieldCheck size={12} /> Local viewing only · {camera.name}
        {` | People detected: ${peopleCount ?? "—"}`}
        {videoAge !== null && ` | Video age ${videoAge} ms`}
        {analysisAge !== null && ` | Analysis ${analysisAge} ms behind video`}
      </span>
    </div>
  );
}

function Dialog({
  title,
  close,
  children,
}: {
  title: string;
  close: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
    const node = ref.current;
    return () => node?.close();
  }, []);
  return (
    <dialog ref={ref} className="vision-dialog" onCancel={close}>
      <div className="modal-heading">
        <h2>{title}</h2>
        <button
          className="icon-button"
          aria-label="Close dialog"
          onClick={close}
        >
          <X size={20} />
        </button>
      </div>
      {children}
    </dialog>
  );
}

function CameraForm({
  siteId,
  camera,
  close,
  saved,
}: {
  siteId: string;
  camera: LocalCamera | null;
  close: () => void;
  saved: () => Promise<void>;
}) {
  const [mode, setMode] = useState(camera?.connection || "auto"),
    [role, setRole] = useState(camera?.role || "entry"),
    [trigger, setTrigger] = useState(camera?.trigger || "presence");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    const password = String(form.get("password") || "");
    const payload = {
      site_id: siteId,
      name: form.get("name"),
      role,
      host: form.get("host"),
      username: form.get("username"),
      password: password || null,
      connection: mode,
      onvif_port: Number(form.get("onvif_port") || 80),
      rtsp_port: Number(form.get("rtsp_port") || 554),
      path: form.get("path") || "",
      target_fps: Number(form.get("fps")),
      zone_name: form.get("zone_name") || "Workplace",
      zone_type: form.get("zone_type") || "work",
      trigger,
      line_axis: form.get("line_axis") || "vertical",
      line_position: Number(form.get("line_position") || 0.5),
      line_direction: form.get("line_direction") || "positive",
    };
    try {
      await api(camera ? `/local/cameras/${camera.id}` : "/local/cameras", {
        method: camera ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      await saved();
      close();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      title={camera ? "Configure camera connection" : "Onboard an IP camera"}
      close={close}
    >
      <form className="modal-form" onSubmit={submit}>
        <div className="note">
          <ShieldCheck size={17} /> Credentials are encrypted in this laptop’s
          local vault. ONVIF/RTSP must be enabled on the camera.
        </div>
        <label className="field">
          <span>Camera name</span>
          <input
            name="name"
            defaultValue={camera?.name}
            placeholder="Main entrance"
            minLength={2}
            required
          />
        </label>
        <label className="field">
          <span>Camera purpose</span>
          <select
            aria-label="Camera purpose"
            value={role === "interior" ? "interior" : "attendance"}
            onChange={(event) =>
              setRole(event.target.value === "interior" ? "interior" : "entry")
            }
          >
            <option value="attendance">
              Attendance only — entry / exit logs
            </option>
            <option value="interior">
              Workplace monitoring — zones and presence only
            </option>
          </select>
        </label>
        {role !== "interior" && (
          <label className="field">
            <span>Attendance direction</span>
            <select
              aria-label="Attendance direction"
              value={role}
              onChange={(e) => setRole(e.target.value as LocalCamera["role"])}
            >
              <option value="entry">Entry camera — IN records</option>
              <option value="exit">Exit camera — OUT records</option>
            </select>
          </label>
        )}
        <div className="form-grid">
          <label className="field">
            <span>Camera IP address</span>
            <input
              name="host"
              placeholder="192.168.1.108"
              defaultValue={camera?.host || ""}
              required
              autoComplete="off"
            />
          </label>
          <label className="field">
            <span>AI sampling FPS target</span>
            <input
              name="fps"
              type="number"
              min={1}
              max={8}
              defaultValue={camera?.target_fps || (role === "interior" ? 2 : 4)}
              required
            />
          </label>
          <label className="field">
            <span>Camera username</span>
            <input
              name="username"
              defaultValue={camera?.username || ""}
              autoComplete="off"
            />
          </label>
          <label className="field">
            <span>Camera password</span>
            <input
              name="password"
              type="password"
              autoComplete="new-password"
              placeholder={
                camera?.has_password
                  ? "Leave blank to retain stored password"
                  : "Camera password"
              }
            />
          </label>
        </div>
        <label className="field">
          <span>Connection method</span>
          <select
            value={mode}
            onChange={(event) => setMode(event.target.value)}
          >
            <option value="auto">Automatic ONVIF discovery</option>
            <option value="manual">Manual RTSP stream path</option>
          </select>
        </label>
        {mode === "auto" ? (
          <label className="field">
            <span>ONVIF HTTP port</span>
            <input
              name="onvif_port"
              type="number"
              min={1}
              max={65535}
              defaultValue={camera?.onvif_port || 80}
            />
            <small>
              Some cameras need a separate ONVIF user or have ONVIF disabled by
              default.
            </small>
          </label>
        ) : (
          <>
            <div className="form-grid">
              <label className="field">
                <span>RTSP port</span>
                <input
                  name="rtsp_port"
                  type="number"
                  min={1}
                  max={65535}
                  defaultValue={camera?.rtsp_port || 554}
                />
              </label>
              <label className="field">
                <span>Common stream path</span>
                <select
                  onChange={(e) => {
                    const input =
                      document.querySelector<HTMLInputElement>(
                        'input[name="path"]',
                      );
                    if (input) input.value = e.target.value;
                  }}
                  defaultValue=""
                >
                  <option value="">Choose or type below</option>
                  <option value="/Streaming/Channels/102">
                    Hikvision substream
                  </option>
                  <option value="/cam/realmonitor?channel=1&subtype=1">
                    Dahua substream
                  </option>
                  <option value="/unicast/c2/s1/live">Uniview substream</option>
                  <option value="/stream1">Generic /stream1</option>
                  <option value="/live/ch00_1">Generic /live/ch00_1</option>
                </select>
              </label>
            </div>
            <label className="field">
              <span>RTSP path</span>
              <input
                name="path"
                placeholder="/Streaming/Channels/102"
                required={!camera}
              />
              <small>
                Enter only the path. IP and credentials are added securely.
                Editing: blank retains the stored path.
              </small>
            </label>
          </>
        )}
        {role === "interior" ? (
          <div className="form-grid">
            <label className="field">
              <span>Zone / room name</span>
              <input
                name="zone_name"
                defaultValue={camera?.zone_name || "Office workspace"}
                required
              />
            </label>
            <label className="field">
              <span>Zone type</span>
              <select
                name="zone_type"
                defaultValue={camera?.zone_type || "work"}
              >
                <option value="work">Work area</option>
                <option value="meeting">Meeting room</option>
                <option value="break">Break / dining</option>
                <option value="transit">Corridor / transit</option>
              </select>
            </label>
          </div>
        ) : (
          <>
            <label className="field">
              <span>Attendance trigger</span>
              <select
                value={trigger}
                onChange={(e) => setTrigger(e.target.value)}
              >
                <option value="presence">
                  Stable face detection in dedicated camera view
                </option>
                <option value="line">Directional line crossing</option>
              </select>
              <small>
                Use a dedicated entry/exit view, or a crossing line, to prevent
                people nearby creating unintended events.
              </small>
            </label>
            {trigger === "line" && (
              <div className="form-grid">
                <label className="field">
                  <span>Line orientation</span>
                  <select
                    name="line_axis"
                    defaultValue={camera?.line_axis || "vertical"}
                  >
                    <option value="vertical">Vertical</option>
                    <option value="horizontal">Horizontal</option>
                  </select>
                </label>
                <label className="field">
                  <span>Position (0.05 to 0.95)</span>
                  <input
                    name="line_position"
                    type="number"
                    min={0.05}
                    max={0.95}
                    step={0.05}
                    defaultValue={camera?.line_position || 0.5}
                  />
                </label>
                <label className="field">
                  <span>Crossing direction</span>
                  <select
                    name="line_direction"
                    defaultValue={camera?.line_direction || "positive"}
                  >
                    <option value="positive">
                      Left → right / top → bottom
                    </option>
                    <option value="negative">
                      Right → left / bottom → top
                    </option>
                  </select>
                </label>
              </div>
            )}
          </>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button className="button secondary" type="button" onClick={close}>
            Cancel
          </button>
          <button className="button primary" disabled={busy}>
            {busy ? "Saving…" : "Save & connect"}
            <Check size={15} />
          </button>
        </div>
      </form>
    </Dialog>
  );
}

export default function LocalVision({
  siteId,
  day,
  employees,
  canManage,
  canEnroll,
  changed,
}: {
  siteId: string;
  day: string;
  employees: Employee[];
  canManage: boolean;
  canEnroll: boolean;
  changed: () => Promise<void>;
}) {
  const [tab, setTab] = useState<Tab>("Cameras"),
    [cameras, setCameras] = useState<LocalCamera[]>([]),
    [status, setStatus] = useState<ModelStatus | null>(null),
    [enrollments, setEnrollments] = useState<Enrollment[]>([]),
    [presence, setPresence] = useState<Presence | null>(null),
    [events, setEvents] = useState<IdentityEvent[]>([]);
  const [zoneCamera, setZoneCamera] = useState<LocalCamera | null>(null);
  const [doorCamera, setDoorCamera] = useState<LocalCamera | null>(null);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [edit, setEdit] = useState<LocalCamera | null | undefined>(undefined),
    [selected, setSelected] = useState<LocalCamera | null>(null);
  const [employeeId, setEmployeeId] = useState(""),
    [variant, setVariant] = useState("uncovered"),
    [captureCamera, setCaptureCamera] = useState("");
  const query = `site_id=${encodeURIComponent(siteId)}`;
  async function load() {
    const [cameraData, statusData, enrollmentData, presenceData, eventData] =
      await Promise.all([
        api<LocalCamera[]>(`/local/cameras?${query}`),
        api<ModelStatus>("/local/status"),
        canEnroll
          ? api<Enrollment[]>(`/local/enrollments?${query}`)
          : Promise.resolve([]),
        api<Presence>(`/local/presence?${query}&day=${day}`),
        canEnroll
          ? api<IdentityEvent[]>(`/local/identity-reviews?${query}&day=${day}`)
          : Promise.resolve([]),
      ]);
    setCameras(cameraData);
    setStatus(statusData);
    setEnrollments(enrollmentData);
    setPresence(presenceData);
    setEvents(eventData);
  }
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      try {
        if (!cancelled) await load();
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
      }
      if (!cancelled) timer = setTimeout(tick, 3000);
    }
    void tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [siteId, day, canEnroll]);
  async function action(run: () => Promise<unknown>, success: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await run();
      setNotice(success);
      await load();
      await changed();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const previewCamera = selected
    ? cameras.find((camera) => camera.id === selected.id) || selected
    : null;
  const online = cameras.filter((camera) => camera.state === "online");
  const enrolled = enrollments.filter(
    (employee) => employee.samples.length > 0,
  ).length;
  return (
    <>
      <div className="vision-heading">
        <div>
          <h2>Local camera & AI workspace</h2>
          <p>
            Real video and biometric inference run on this laptop. Keep it on
            the camera’s Wi-Fi.
          </p>
        </div>
        {canManage && (
          <button className="button primary" onClick={() => setEdit(null)}>
            <Plus size={16} />
            Onboard camera
          </button>
        )}
      </div>
      <div
        className="vision-tabs"
        role="tablist"
        aria-label="Camera and AI tools"
      >
        {(
          [
            "Cameras",
            "Face enrollment",
            "Daily matrices",
            "Presence",
            "Identity review",
            "Model status",
          ] as Tab[]
        )
          .filter(
            (name) =>
              canEnroll ||
              ![
                "Face enrollment",
                "Identity review",
                "Daily matrices",
              ].includes(name),
          )
          .map((name) => (
            <button
              role="tab"
              aria-selected={tab === name}
              className={tab === name ? "active" : ""}
              key={name}
              onClick={() => {
                setTab(name);
                setError("");
                setNotice("");
              }}
            >
              {name}
            </button>
          ))}
      </div>
      {error && (
        <div className="alert error" role="alert">
          <CircleAlert size={17} />
          {error}
          <button
            className="icon-button"
            onClick={() => setError("")}
            aria-label="Dismiss camera error"
          >
            <X size={16} />
          </button>
        </div>
      )}
      {notice && (
        <div className="alert success" role="status">
          <Check size={17} />
          {notice}
        </div>
      )}
      <div className="vision-health">
        <span>
          <i className={status?.model_state === "ready" ? "online-dot" : ""} />
          {status?.model_state === "ready"
            ? "Four ML models ready · CPU"
            : `Models: ${status?.model_state || "checking"}`}
        </span>
        <span>
          {online.length} / {cameras.length} cameras receiving frames
        </span>
        <span>{enrolled} employees enrolled</span>
        <button
          className="text-button"
          onClick={() => {
            void load().catch((e) => setError(e.message));
          }}
        >
          <RefreshCw size={13} />
          Refresh
        </button>
      </div>
      {tab === "Cameras" && (
        <>
          <div className="note">
            <Camera size={17} />
            Entry and exit cameras create separate attendance events. Workplace
            cameras record observed zone presence; body identity stays
            tentative. Preview is local only.
          </div>
          <div className="camera-grid">
            {cameras.map((camera) => (
              <article className="camera-card" key={camera.id}>
                <div className="camera-wall-title">
                  <strong>{camera.name}</strong>
                  <span
                    className={`badge ${camera.state === "online" ? "green" : "amber"}`}
                  >
                    {camera.state.replaceAll("_", " ")}
                  </span>
                </div>
                <Preview
                  camera={camera}
                  paused={
                    selected?.id === camera.id ||
                    previewCamera?.id === camera.id ||
                    doorCamera?.id === camera.id
                  }
                  intervalMs={cameras.length > 4 ? 250 : 67}
                />
                <button
                  className="text-button camera-expand"
                  onClick={() => setSelected(camera)}
                >
                  <Eye size={14} /> Expand live view
                </button>
                <div className="camera-details">
                  <div>
                    <span className={`purpose-tag ${camera.role}`}>
                      {camera.role === "entry"
                        ? "ENTRY ATTENDANCE"
                        : camera.role === "exit"
                          ? "EXIT ATTENDANCE"
                          : "WORKPLACE MONITORING"}
                    </span>
                    <p>
                      {camera.role === "interior"
                        ? camera.zone_name || "Zone not configured"
                        : camera.trigger === "line"
                          ? "Directional line crossing"
                          : "Stable face detection"}
                    </p>
                  </div>
                  <div className="camera-controls">
                    {canEnroll && camera.role === "interior" && (
                      <button
                        className="icon-button"
                        title="Draw workstation polygons"
                        aria-label={`Workstation zones for ${camera.name}`}
                        onClick={() => setZoneCamera(camera)}
                      >
                        <Focus size={16} />
                      </button>
                    )}
                    {canManage && (
                      <>
                        <button
                          className="icon-button"
                          title="Edit connection"
                          aria-label={`Configure ${camera.name}`}
                          onClick={() => setEdit(camera)}
                        >
                          <Pencil size={15} />
                        </button>
                        <button
                          className="icon-button"
                          disabled={busy || !camera.host}
                          title={
                            camera.state === "online" ||
                            camera.state === "connecting"
                              ? "Stop camera"
                              : "Start camera"
                          }
                          aria-label={`Toggle ${camera.name}`}
                          onClick={() =>
                            action(
                              () =>
                                post(`/local/cameras/${camera.id}/action`, {
                                  action: [
                                    "online",
                                    "connecting",
                                    "reconnecting",
                                  ].includes(camera.state)
                                    ? "stop"
                                    : "start",
                                }),
                              "Camera action submitted.",
                            )
                          }
                        >
                          {["online", "connecting", "reconnecting"].includes(
                            camera.state,
                          ) ? (
                            <Square size={15} />
                          ) : (
                            <Play size={15} />
                          )}
                        </button>
                        <button
                          className="icon-button"
                          title="Remove camera"
                          aria-label={`Remove ${camera.name}`}
                          disabled={busy}
                          onClick={() => {
                            if (
                              confirm(
                                `Remove ${camera.name} from the camera wall? Existing attendance and evidence stay in the audit trail.`,
                              )
                            )
                              void action(
                                () =>
                                  api(`/local/cameras/${camera.id}`, {
                                    method: "DELETE",
                                  }),
                                "Camera removed from the wall. Existing records preserved.",
                              );
                          }}
                        >
                          <Trash2 size={15} />
                        </button>
                      </>
                    )}
                  </div>
                </div>
                {camera.message && (
                  <p className="camera-diagnostic">{camera.message}</p>
                )}
                <div className="camera-metrics">
                  <span
                    title={`Requested full-inference rate. Effective cap: ${Math.min(camera.target_fps, camera.role === "interior" ? (status?.inference_fps_limit ?? 2) : (status?.portal_fps_limit ?? 5))} FPS. Actual rate also depends on model processing time; this is not video playback FPS.`}
                  >
                    {camera.target_fps} FPS requested
                  </span>
                  <span>
                    {camera.inference_ms
                      ? `${camera.inference_ms} ms inference`
                      : "No inference yet"}
                  </span>
                  <span>
                    {camera.full_refresh_fps !== undefined
                      ? `${camera.full_refresh_fps.toFixed(2)} actual inference FPS`
                      : camera.tracking_ms
                        ? `${camera.tracking_ms} ms tracking`
                        : "Measuring inference rate"}
                  </span>
                  <span>{camera.people ?? 0} people</span>
                </div>
              </article>
            ))}
          </div>
          {!cameras.length && (
            <div className="panel">
              <div className="empty">
                <Camera size={32} />
                <h3>Add your first network camera</h3>
                <p>
                  Choose its purpose, enter the private IP and camera
                  credentials, then let ONVIF discover the stream.
                </p>
              </div>
            </div>
          )}
        </>
      )}
      {tab === "Face enrollment" && (
        <div className="vision-enrollment">
          <section className="panel">
            <div className="panel-heading">
              <div>
                <h2>Enroll an employee’s face</h2>
                <p>
                  Add clear samples from several angles, including a separate
                  masked set.
                </p>
              </div>
              <ScanFace size={22} />
            </div>
            <div className="padded">
              <div className="note amber">
                Recognition uses permanent local face samples. A masked sample
                can help, but masked-face accuracy is unverified. Low-confidence
                matches remain unknown. Anti-spoofing is not implemented.
              </div>
              <label className="field">
                <span>Employee</span>
                <select
                  aria-label="Enrollment employee"
                  value={employeeId}
                  disabled={busy}
                  onChange={(e) => setEmployeeId(e.target.value)}
                >
                  <option value="">Choose an employee</option>
                  {employees.map((employee) => (
                    <option key={employee.id} value={employee.id}>
                      {employee.name} · {employee.code}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Sample type</span>
                <select
                  aria-label="Enrollment sample type"
                  value={variant}
                  disabled={busy}
                  onChange={(e) => setVariant(e.target.value)}
                >
                  <option value="uncovered">Uncovered face</option>
                  <option value="front">Front-facing reference</option>
                  <option value="left_30">Left approximately 30 degrees</option>
                  <option value="right_30">
                    Right approximately 30 degrees
                  </option>
                  <option value="masked">Mouth covered / masked face</option>
                </select>
              </label>
              <EnrollmentPhotos
                key={employeeId}
                employeeId={employeeId}
                variant={variant}
                disabled={
                  busy || !employeeId || status?.model_state !== "ready"
                }
                onBusyChange={(value) => {
                  setBusy(value);
                  if (value) {
                    setError("");
                    setNotice("");
                  }
                }}
                onComplete={async () => {
                  await load();
                  await changed();
                }}
              />
              <div className="enroll-divider">
                or capture directly from a connected camera
              </div>
              <label className="field">
                <span>Enrollment camera</span>
                <select
                  aria-label="Enrollment camera"
                  value={captureCamera}
                  onChange={(e) => setCaptureCamera(e.target.value)}
                >
                  <option value="">Choose an online camera</option>
                  {online.map((camera) => (
                    <option value={camera.id} key={camera.id}>
                      {camera.name}
                    </option>
                  ))}
                </select>
              </label>
              {captureCamera && cameras.find((c) => c.id === captureCamera) && (
                <Preview
                  camera={cameras.find((c) => c.id === captureCamera)!}
                />
              )}
              <button
                className="button secondary"
                disabled={
                  busy ||
                  !employeeId ||
                  !captureCamera ||
                  status?.model_state !== "ready"
                }
                onClick={() =>
                  action(
                    () =>
                      post(
                        `/local/employees/${employeeId}/enroll-camera/${captureCamera}?variant=${variant}`,
                        {},
                      ),
                    "Camera face sample enrolled. Add different angles for better coverage.",
                  )
                }
              >
                <Focus size={16} />
                Capture one face sample
              </button>
              <p className="fineprint">
                Confirm the selected employee is the only person in the view.
                Face vectors and review thumbnails are encrypted locally. Use at
                least three clear, varied samples. Review saved photos in the
                gallery.
              </p>
            </div>
          </section>
          <section className="panel">
            <div className="panel-heading">
              <h2>Local enrollment gallery</h2>
              <ShieldCheck size={19} />
            </div>
            {enrollments.map((employee) => (
              <div key={employee.employee_id}>
                <div className="enrollment-row">
                  <div>
                    <strong>{employee.name}</strong>
                    <small>
                      {
                        employee.samples.filter(
                          (sample) => sample.variant !== "masked",
                        ).length
                      }{" "}
                      uncovered ·{" "}
                      {
                        employee.samples.filter(
                          (sample) => sample.variant === "masked",
                        ).length
                      }{" "}
                      masked samples
                    </small>
                  </div>
                  <span className="badge">
                    {employee.samples.length ? "Enrolled" : "Not enrolled"}
                  </span>
                  {employee.samples.length > 0 && canManage && (
                    <button
                      className="icon-button"
                      title="Delete face enrollment"
                      aria-label={`Delete enrollment for ${employee.name}`}
                      disabled={busy}
                      onClick={() => {
                        if (
                          confirm(
                            `Delete all local face templates for ${employee.name}?`,
                          )
                        )
                          void action(
                            () =>
                              api(
                                `/local/employees/${employee.employee_id}/enrollment`,
                                { method: "DELETE" },
                              ),
                            "Local enrollment deleted.",
                          );
                      }}
                    >
                      <Trash2 size={15} />
                    </button>
                  )}
                </div>
                {!!employee.legacy_samples?.length && (
                  <div style={{ padding: "0 20px" }}>
                    <p className="fineprint">
                      {employee.legacy_samples.length} earlier-model samples
                      need AdaFace re-enrollment.
                    </p>
                    <SavedEnrollmentSamples
                      employeeId={employee.employee_id}
                      samples={employee.legacy_samples}
                      disabled={busy || !canEnroll}
                      onRemove={(sampleId) =>
                        void action(
                          () =>
                            api(
                              `/local/employees/${employee.employee_id}/enrollment/${sampleId}`,
                              { method: "DELETE" },
                            ),
                          "Earlier-model sample removed.",
                        )
                      }
                    />
                  </div>
                )}
                <SavedEnrollmentSamples
                  employeeId={employee.employee_id}
                  samples={employee.samples}
                  disabled={busy || !canEnroll}
                  onRemove={(sampleId) =>
                    void action(
                      () =>
                        api(
                          `/local/employees/${employee.employee_id}/enrollment/${sampleId}`,
                          { method: "DELETE" },
                        ),
                      "Sample removed from employee recognition gallery.",
                    )
                  }
                />
              </div>
            ))}
            {!enrollments.length && (
              <p className="padded muted">
                Add employees in the employee directory first.
              </p>
            )}
          </section>
        </div>
      )}
      {tab === "Presence" && (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Observed workplace presence</h2>
              <p>
                {day} · Continuous gaps are unknown, not absence or low
                productivity.
              </p>
            </div>
            <Eye size={20} />
          </div>
          <div className="note">
            {presence?.notice || "Waiting for observations"}
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Employee</th>
                  <th>Face-confirmed time</th>
                  <th>Tentative body-linked time</th>
                  <th>Observed zones</th>
                  <th>Workplace time</th>
                  <th>Work / eligible observed</th>
                  <th>Approved break</th>
                  <th>Coverage</th>
                  <th>Unknown</th>
                </tr>
              </thead>
              <tbody>
                {presence?.items.map((item) => (
                  <tr key={item.employee_id}>
                    <td>
                      <strong>{item.name}</strong>
                    </td>
                    <td>{minutes(Math.floor(item.observed_seconds / 60))}</td>
                    <td>{minutes(Math.floor(item.tentative_seconds / 60))}</td>
                    <td>
                      {Object.entries(item.zones)
                        .map(
                          ([zone, seconds]) =>
                            `${zone}: ${minutes(Math.floor(seconds / 60))}`,
                        )
                        .join(" · ") || "Unknown"}
                    </td>
                    <td>
                      {minutes(Math.floor(item.work_confirmed_seconds / 60))}{" "}
                      confirmed
                      <br />
                      {minutes(
                        Math.floor(item.work_tentative_seconds / 60),
                      )}{" "}
                      tentative
                    </td>
                    <td>
                      {item.work_percent_of_observed === null
                        ? "—"
                        : `${item.work_percent_of_observed}%`}
                    </td>
                    <td>
                      {minutes(Math.floor(item.approved_break_seconds / 60))}
                    </td>
                    <td>
                      {item.coverage_percent === null
                        ? "—"
                        : `${item.coverage_percent}%`}
                    </td>
                    <td>{minutes(Math.floor(item.unknown_seconds / 60))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {tab === "Identity review" && (
        <IdentityReview
          events={events}
          employees={employees}
          changed={async () => {
            await load();
            await changed();
          }}
        />
      )}
      {tab === "Daily matrices" && <DailyProfiles siteId={siteId} />}
      {doorCamera && (
        <Dialog
          title={`Door zones · ${doorCamera.name}`}
          close={() => setDoorCamera(null)}
        >
          <DoorZoneEditor camera={doorCamera} />
        </Dialog>
      )}
      {zoneCamera && (
        <Dialog
          title={`Workstation areas · ${zoneCamera.name}`}
          close={() => setZoneCamera(null)}
        >
          <CameraZones
            camera={zoneCamera}
            employees={employees}
            saved={() =>
              setNotice(
                "Workstation polygons saved. New observations use these assignments.",
              )
            }
          />
        </Dialog>
      )}
      {tab === "Model status" && (
        <section className="panel model-panel">
          <div className="panel-heading">
            <div>
              <h2>Real inference, on your CPU</h2>
              <p>
                Face models use your local retinaface-resnet50.onnx and
                adaface_ir50_ms1mv2.onnx files. Re-enroll employees after the
                model change.
              </p>
            </div>
            <Cpu size={24} />
          </div>
          <p className="fineprint">
            Frontal match score at least {status?.face_threshold}, margin{" "}
            {status?.face_margin}. Side match score at least{" "}
            {status?.side_face_threshold}, margin {status?.side_face_margin}.
            Attendance score at least {status?.attendance_face_threshold}.
            Validate these settings on your site.
          </p>
          <p className="fineprint">
            Active recognizer: AdaFace IR50 BGR 112 112 normalized 512-D
            embeddings.
          </p>
          {status?.gallery_generations
            ?.filter((gallery) => gallery.site === siteId)
            .map((gallery) => (
              <p key={gallery.version} className="fineprint">
                Gallery {gallery.version.slice(0, 8)} {gallery.samples} approved
                photos {gallery.embeddings} embeddings
              </p>
            ))}
          {status?.gallery_errors
            ?.filter((gallery) => gallery.site === siteId)
            .map((gallery) => (
              <p key={gallery.site} role="alert">
                Gallery rebuild pending. Retry enrollment or run the rebuild
                script.
              </p>
            ))}
          <div className="model-cards">
            {status?.models.map((model, index) => (
              <article key={model}>
                <span>0{index + 1}</span>
                <strong>{model}</strong>
                <small>
                  {
                    [
                      "Find faces and landmarks",
                      "Compare enrolled face embeddings",
                      "Locate people in camera frames",
                      "Compare today’s face-anchored body appearance",
                    ][index]
                  }
                </small>
              </article>
            ))}
          </div>
          <div className="padded">
            <div className="note">
              <ShieldCheck size={18} />
              {status?.message}
            </div>
            <p>
              Recognition requires {status?.confirmation_frames} consecutive
              matching frames, cosine score ≥ {status?.face_threshold} and a
              runner-up margin ≥ {status?.face_margin}. Inference sampling is
              capped at {status?.inference_fps_limit} FPS for workplace cameras;
              entry/exit cameras receive priority up to{" "}
              {status?.portal_fps_limit} FPS.
            </p>
            <p className="note amber">
              No perfect-accuracy or mask guarantee. No liveness/anti-spoofing.
              Daily appearance matrices expire at site-local midnight. Body
              matches are always tentative. Measure false matches and missed
              events on your site before operational use.
            </p>
            {status?.missing_models.length ? (
              <p className="form-error">
                Missing: {status.missing_models.join(", ")}. Run
                scripts/install-models.ps1.
              </p>
            ) : (
              canManage &&
              status?.model_state !== "ready" && (
                <button
                  className="button primary"
                  disabled={busy}
                  onClick={() =>
                    action(
                      () => post("/local/models/load", {}),
                      "Model loading started.",
                    )
                  }
                >
                  <Play size={16} />
                  Load installed models
                </button>
              )
            )}
          </div>
        </section>
      )}
      {edit !== undefined && (
        <CameraForm
          siteId={siteId}
          camera={edit}
          close={() => setEdit(undefined)}
          saved={async () => {
            await load();
            await changed();
          }}
        />
      )}
      {previewCamera && (
        <Dialog title={previewCamera.name} close={() => setSelected(null)}>
          <div className="modal-form">
            <Preview camera={previewCamera} />
            <DoorwayTimeLogs camera={previewCamera} />
            {canManage && (
              <button
                className="button secondary"
                onClick={() => {
                  setDoorCamera(previewCamera);
                  setSelected(null);
                }}
              >
                Door zones / Entry–Exit
              </button>
            )}
            <div className="vision-live-info">
              <span>
                Stream: <strong>{previewCamera.state}</strong>
              </span>
              <span>Inference: {previewCamera.inference_ms ?? "—"} ms</span>
            </div>
            <p className="fineprint">
              {previewCamera.message} · Face matches require stable
              observations; amber labels are uncertain or tentative.
            </p>
            {canManage && (
              <button
                className="button secondary"
                onClick={() => {
                  setEdit(previewCamera);
                  setSelected(null);
                }}
              >
                <Pencil size={15} />
                Edit connection / purpose
              </button>
            )}
          </div>
        </Dialog>
      )}
    </>
  );
}
