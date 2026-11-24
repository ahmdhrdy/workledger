import { useEffect, useId, useRef, useState } from "react";
import type { PointerEvent } from "react";
import { api } from "./api";
import DoorwayTimeLogs from "./DoorwayTimeLogs";

type Role = "a" | "transition" | "b";
type Point = [number, number];
type Polygon = { role: Role; name: string; points: Point[] };
type DoorConfig = {
  version: number;
  name: string;
  active: boolean;
  zones: Polygon[];
  entry_forward: boolean;
  anchor: "foot" | "torso";
  inertia: number;
  stable_ms: number;
  max_gap_ms: number;
  traversal_ms: number;
  cooldown_ms: number;
  verification_wait_ms: number;
};
type Anchor = {
  track_id: number;
  point: Point;
  reliable: boolean;
  foot?: Point;
  torso?: Point;
};
const roles: Role[] = ["a", "transition", "b"];
const roleNames = {
  a: "A · entry-side approach",
  transition: "Transition · doorway",
  b: "B · exit-side destination",
};
const colors = { a: "#2f80ed", transition: "#e4a321", b: "#2a9b62" };
const emptyZones = (): Polygon[] =>
  roles.map((role) => ({ role, name: "", points: [] }));
function contains(point: Point, polygon: Point[]) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const a = polygon[i],
      b = polygon[j];
    if (
      a[1] > point[1] !== b[1] > point[1] &&
      point[0] < ((b[0] - a[0]) * (point[1] - a[1])) / (b[1] - a[1]) + a[0]
    )
      inside = !inside;
  }
  return inside;
}
export default function DoorZoneEditor({
  camera,
}: {
  camera: { id: string; name: string };
}) {
  const [config, setConfig] = useState<DoorConfig>({
    version: 0,
    name: "",
    active: true,
    zones: emptyZones(),
    entry_forward: true,
    anchor: "foot",
    inertia: 2,
    stable_ms: 100,
    max_gap_ms: 750,
    traversal_ms: 10000,
    cooldown_ms: 1500,
    verification_wait_ms: 5000,
  });
  const [selected, setSelected] = useState<Role>("a");
  const [closed, setClosed] = useState<Record<Role, boolean>>({
    a: false,
    transition: false,
    b: false,
  });
  const [source, setSource] = useState("");
  const [imageAspect, setImageAspect] = useState<number>(16 / 9);
  const [live, setLive] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [anchors, setAnchors] = useState<Anchor[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [status, setStatus] = useState<{
    applied_version: number | null;
    writer_error?: string | null;
    camera_running?: boolean;
    active?: boolean;
    camera_role?: string;
    last_processed_at?: number;
    last_processed_age_ms?: number;
    visual_tracking_error?: boolean;
    tracks?: {
      track_id: string;
      reason: string;
      zone?: string;
      point?: Point;
      verification?: string;
    }[];
  }>({ applied_version: null });
  const [statusError, setStatusError] = useState("");
  const saved = useRef<DoorConfig | null>(null);
  const drag = useRef<{ index: number; start: Point; points: Point[] } | null>(
    null,
  );
  const arrow = `door-${useId().replace(/:/g, "")}`;
  const current = config.zones.find((z) => z.role === selected)!;
  const anchorPoint = (anchor: Anchor) => anchor[config.anchor] || anchor.point;
  useEffect(() => {
    let cancelled = false;
    void api<DoorConfig>(`/local/cameras/${camera.id}/door-zone`)
      .then((data) => {
        if (cancelled) return;
        const next = data.version
          ? data
          : { ...config, version: 0, zones: emptyZones() };
        saved.current = next;
        setConfig(next);
        setClosed({
          a: !!data.version,
          transition: !!data.version,
          b: !!data.version,
        });
        setLoaded(true);
      })
      .catch((e) => setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [camera.id]);
  useEffect(() => {
    let cancelled = false,
      timer: ReturnType<typeof setTimeout>,
      url = "";
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch(
          `/api/v1/local/cameras/${camera.id}/frame?t=${Date.now()}`,
          { credentials: "same-origin", signal: controller.signal },
        );
        if (!response.ok)
          throw new Error(
            "Connect the camera, then refresh the reference image.",
          );
        const next = URL.createObjectURL(await response.blob());
        const image = new Image();
        image.src = next;
        await image.decode();
        if (cancelled) {
          URL.revokeObjectURL(next);
          return;
        }
        if (url) URL.revokeObjectURL(url);
        url = next;
        setSource(next);
        const header = response.headers.get("X-Workledger-Geometry");
        if (header) {
          const bytes = Uint8Array.from(atob(header), (c) => c.charCodeAt(0));
          setAnchors(JSON.parse(new TextDecoder().decode(bytes)).anchors || []);
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
      if (live && !cancelled) timer = setTimeout(load, 300);
    }
    void load();
    return () => {
      cancelled = true;
      controller.abort();
      clearTimeout(timer);
      if (url) URL.revokeObjectURL(url);
    };
  }, [camera.id, live, refresh]);
  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const state = await api<typeof status>(
          `/local/cameras/${camera.id}/door-zone/status`,
        );
        if (!cancelled) {
          setStatus(state);
          setStatusError("");
        }
      } catch (error) {
        if (!cancelled) setStatusError((error as Error).message);
      }
    }
    void load();
    const timer = setInterval(load, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [camera.id]);
  function replace(points: Point[]) {
    setConfig((previous) => ({
      ...previous,
      zones: previous.zones.map((z) =>
        z.role === selected ? { ...z, points } : z,
      ),
    }));
  }
  function coordinate(event: PointerEvent<SVGSVGElement>): Point {
    const rect = event.currentTarget.getBoundingClientRect();
    return [
      Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    ];
  }
  function down(event: PointerEvent<SVGSVGElement>) {
    const target = event.target as SVGElement;
    const index = target.getAttribute("data-vertex");
    const point = coordinate(event);
    if (index !== null) {
      if (
        Number(index) === 0 &&
        !closed[selected] &&
        current.points.length >= 3
      ) {
        setClosed({ ...closed, [selected]: true });
        return;
      }
      drag.current = {
        index: Number(index),
        start: point,
        points: current.points.map((p) => [...p] as Point),
      };
      event.currentTarget.setPointerCapture(event.pointerId);
      return;
    }
    const role = target.getAttribute("data-role") as Role | null;
    if (role && role !== selected) {
      setSelected(role);
      return;
    }
    if (closed[selected] && role === selected) {
      drag.current = {
        index: -1,
        start: point,
        points: current.points.map((p) => [...p] as Point),
      };
      event.currentTarget.setPointerCapture(event.pointerId);
    } else if (!closed[selected] && current.points.length < 32)
      replace([...current.points, point]);
  }
  function move(event: PointerEvent<SVGSVGElement>) {
    const state = drag.current;
    if (!state) return;
    const point = coordinate(event);
    if (state.index >= 0)
      replace(state.points.map((p, i) => (i === state.index ? point : p)));
    else {
      const dx = Math.max(
        -Math.min(...state.points.map((p) => p[0])),
        Math.min(
          1 - Math.max(...state.points.map((p) => p[0])),
          point[0] - state.start[0],
        ),
      );
      const dy = Math.max(
        -Math.min(...state.points.map((p) => p[1])),
        Math.min(
          1 - Math.max(...state.points.map((p) => p[1])),
          point[1] - state.start[1],
        ),
      );
      replace(state.points.map((p) => [p[0] + dx, p[1] + dy]));
    }
  }
  async function save() {
    setBusy(true);
    setError("");
    try {
      if (!roles.every((role) => closed[role]))
        throw new Error("Draw and close all three polygons first.");
      const body = {
        expected_version: config.version,
        name: config.name,
        active: config.active,
        zones: config.zones,
        entry_forward: config.entry_forward,
        anchor: config.anchor,
        inertia: config.inertia,
        stable_ms: config.stable_ms,
        max_gap_ms: config.max_gap_ms,
        traversal_ms: config.traversal_ms,
        cooldown_ms: config.cooldown_ms,
        verification_wait_ms: config.verification_wait_ms,
      };
      const result = await api<DoorConfig>(
        `/local/cameras/${camera.id}/door-zone`,
        {
          method: "PUT",
          body: JSON.stringify(body),
        },
      );
      saved.current = result;
      setConfig(result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const centers = config.zones.map((z) =>
    z.points.length
      ? z.points.reduce(
          (a, p) =>
            [
              a[0] + p[0] / z.points.length,
              a[1] + p[1] / z.points.length,
            ] as Point,
          [0, 0] as Point,
        )
      : null,
  );
  const sequence = config.entry_forward ? centers : [...centers].reverse();
  return (
    <div className="door-workspace">
      <div className="modal-form door-editor-controls">
        <p>
          Draw A, Transition and B on this camera image. Click the first corner
          to close each polygon. Drag a corner to adjust it, or drag a closed
          polygon to move it. No zones exist until you draw and save them.
        </p>
        <div className="zone-actions">
          {roles.map((role) => (
            <button
              key={role}
              className={`button ${selected === role ? "" : "secondary"}`}
              onClick={() => setSelected(role)}
            >
              {roleNames[role]}
            </button>
          ))}
        </div>
        <div
          className="zone-canvas door-zone-canvas"
          style={{ touchAction: "none", aspectRatio: imageAspect }}
        >
          {source ? (
            <img
              src={source}
              alt={`Draw door zones on ${camera.name}`}
              onLoad={(event) =>
                setImageAspect(
                  event.currentTarget.naturalWidth /
                    event.currentTarget.naturalHeight,
                )
              }
            />
          ) : (
            <p>Loading camera image…</p>
          )}
          <svg
            viewBox="0 0 1000 1000"
            preserveAspectRatio="none"
            onPointerDown={down}
            onPointerMove={move}
            onPointerUp={() => {
              drag.current = null;
            }}
            onPointerCancel={() => {
              drag.current = null;
            }}
            aria-label="Door polygon drawing surface"
          >
            <defs>
              <marker
                id={arrow}
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="8"
                markerHeight="8"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" fill="#fff" />
              </marker>
            </defs>
            {config.zones.map((zone) => (
              <g key={zone.role}>
                <polygon
                  data-role={zone.role}
                  points={zone.points
                    .map((p) => `${p[0] * 1000},${p[1] * 1000}`)
                    .join(" ")}
                  fill={colors[zone.role]}
                  fillOpacity={
                    anchors.some(
                      (a) =>
                        a.reliable && contains(anchorPoint(a), zone.points),
                    )
                      ? 0.4
                      : 0.18
                  }
                  stroke={colors[zone.role]}
                  strokeWidth={selected === zone.role ? 5 : 3}
                />
                {zone.points[0] && (
                  <text
                    x={zone.points[0][0] * 1000}
                    y={zone.points[0][1] * 1000 - 10}
                    fill="#fff"
                    fontSize="20"
                    pointerEvents="none"
                  >
                    {zone.name || roleNames[zone.role]}
                  </text>
                )}
              </g>
            ))}
            {sequence.every(Boolean) && (
              <polyline
                points={sequence
                  .map((p) => `${p![0] * 1000},${p![1] * 1000}`)
                  .join(" ")}
                fill="none"
                stroke="#fff"
                strokeWidth="4"
                markerEnd={`url(#${arrow})`}
                pointerEvents="none"
              />
            )}
            {current.points.map((p, i) => (
              <circle
                key={i}
                data-vertex={i}
                cx={p[0] * 1000}
                cy={p[1] * 1000}
                r={10}
                fill="white"
                stroke={colors[selected]}
                strokeWidth={4}
              />
            ))}
            {live &&
              anchors.map((a) => (
                <circle
                  key={a.track_id}
                  cx={anchorPoint(a)[0] * 1000}
                  cy={anchorPoint(a)[1] * 1000}
                  r={6}
                  fill={a.reliable ? "#fff" : "#aaa"}
                  pointerEvents="none"
                />
              ))}
          </svg>
        </div>
        <div className="zone-actions">
          <button
            className="button secondary"
            onClick={() => {
              replace(current.points.slice(0, -1));
              setClosed({ ...closed, [selected]: false });
            }}
          >
            Undo corner
          </button>
          <button
            className="button secondary"
            onClick={() => {
              replace([]);
              setClosed({ ...closed, [selected]: false });
            }}
          >
            Clear selected polygon
          </button>
          <button
            className="button secondary"
            onClick={() => setClosed({ ...closed, [selected]: true })}
            disabled={current.points.length < 3}
          >
            Close polygon
          </button>
          <button
            className="button secondary"
            onClick={() => {
              if (saved.current) {
                setConfig(saved.current);
                setClosed({
                  a: !!saved.current.version,
                  transition: !!saved.current.version,
                  b: !!saved.current.version,
                });
                setError("");
              }
            }}
          >
            Reset to saved
          </button>
          <button
            className="button secondary"
            onClick={() => setRefresh(refresh + 1)}
          >
            Refresh image
          </button>
          <label>
            <input
              type="checkbox"
              checked={live}
              onChange={(e) => setLive(e.target.checked)}
            />{" "}
            Test live anchor points
          </label>
        </div>
        <div className="form-grid">
          <label className="field">
            Door name
            <input
              value={config.name}
              onChange={(e) => setConfig({ ...config, name: e.target.value })}
            />
          </label>
          <label className="field">
            Selected polygon name
            <input
              value={current.name}
              onChange={(e) =>
                setConfig({
                  ...config,
                  zones: config.zones.map((z) =>
                    z.role === selected ? { ...z, name: e.target.value } : z,
                  ),
                })
              }
            />
          </label>
          <label className="field">
            Anchor
            <select
              value={config.anchor}
              onChange={(e) =>
                setConfig({
                  ...config,
                  anchor: e.target.value as "foot" | "torso",
                })
              }
            >
              <option value="foot">Bottom-center / foot</option>
              <option value="torso">
                Torso center — draw zones for this height
              </option>
            </select>
          </label>
          <label>
            <input
              type="checkbox"
              checked={config.active}
              onChange={(e) =>
                setConfig({ ...config, active: e.target.checked })
              }
            />{" "}
            Enable automatic door attendance
          </label>
        </div>
        <button
          className="button secondary"
          onClick={() =>
            setConfig({ ...config, entry_forward: !config.entry_forward })
          }
        >
          Flip entry/exit direction
        </button>
        <p>
          ENTRY:{" "}
          {config.entry_forward ? "A → Transition → B" : "B → Transition → A"}.
          EXIT: reverse sequence. Every verified trip is logged; daily
          attendance uses first ENTRY and last EXIT.
        </p>
        <details>
          <summary>Selected polygon coordinates (0–1)</summary>
          {current.points.map((point, index) => (
            <div className="form-grid" key={index}>
              <label className="field">
                Corner {index + 1} X
                <input
                  type="number"
                  min="0"
                  max="1"
                  step="0.001"
                  value={point[0]}
                  onChange={(event) =>
                    replace(
                      current.points.map((p, i) =>
                        i === index
                          ? [
                              Math.max(
                                0,
                                Math.min(1, Number(event.target.value)),
                              ),
                              p[1],
                            ]
                          : p,
                      ),
                    )
                  }
                />
              </label>
              <label className="field">
                Corner {index + 1} Y
                <input
                  type="number"
                  min="0"
                  max="1"
                  step="0.001"
                  value={point[1]}
                  onChange={(event) =>
                    replace(
                      current.points.map((p, i) =>
                        i === index
                          ? [
                              p[0],
                              Math.max(
                                0,
                                Math.min(1, Number(event.target.value)),
                              ),
                            ]
                          : p,
                      ),
                    )
                  }
                />
              </label>
            </div>
          ))}
        </details>
        <details>
          <summary>Crossing stability settings</summary>
          <div className="form-grid">
            {(
              [
                ["inertia", "Consecutive observations"],
                ["stable_ms", "Stable zone time (ms)"],
                ["max_gap_ms", "Maximum visual gap (ms)"],
                ["traversal_ms", "Maximum traversal time (ms)"],
                ["cooldown_ms", "Same-direction cooldown (ms)"],
                ["verification_wait_ms", "Verification wait (ms)"],
              ] as const
            ).map(([key, title]) => (
              <label className="field" key={key}>
                {title}
                <input
                  type="number"
                  value={config[key]}
                  onChange={(e) =>
                    setConfig({ ...config, [key]: Number(e.target.value) })
                  }
                />
              </label>
            ))}
          </div>
        </details>
        {error && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        {status.writer_error && (
          <p role="alert" className="form-error">
            {status.writer_error}
          </p>
        )}
        <button
          className="button"
          disabled={
            busy ||
            !loaded ||
            !source ||
            !config.name ||
            config.zones.some((z) => !z.name || z.points.length < 3)
          }
          onClick={() => void save()}
        >
          {busy ? "Saving…" : "Save door zones"}
        </button>
        <p className="fineprint">
          Saved version {config.version}. Applied version{" "}
          {status.applied_version ?? "waiting for camera frames"}. Geometry
          changes apply without restarting the camera. Editing does not create a
          crossing.
        </p>
        <section
          className="door-diagnostics"
          aria-label="Door trigger diagnostics"
        >
          <h3>Live trigger diagnostics</h3>
          <p>
            Processing uses the saved geometry. A visible name alone does not
            confirm attendance.
          </p>
          <p>
            Camera: {status.camera_running ? "running" : "stopped"} · Mode:{" "}
            {status.camera_role || "checking"} · Door zones:{" "}
            {status.active ? "enabled" : "disabled / not saved"}
          </p>
          <p>
            Anchor: {config.anchor}. Draw polygons around the selected marker's
            path, not around the face box. For body-height polygons, select
            torso and save.
          </p>
          {!status.last_processed_at && (
            <p role="status">
              No zone-processing frames received yet. Check that the updated
              backend is running and this camera is connected.
            </p>
          )}
          {status.last_processed_age_ms != null &&
            status.last_processed_age_ms > 2000 && (
              <p role="alert">
                Door processing is stale (
                {Math.round(status.last_processed_age_ms / 1000)} seconds).
                Check camera decoding and backend worker status.
              </p>
            )}
          {status.visual_tracking_error && (
            <p role="alert">
              Visual tracking failed; reliable crossing anchors are unavailable.
            </p>
          )}
          {statusError && (
            <p role="alert" className="form-error">
              Diagnostics unavailable: {statusError}
            </p>
          )}
          <div className="door-table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Track</th>
                  <th>Region</th>
                  <th>Anchor (0–1)</th>
                  <th>Trigger state</th>
                  <th>Identity evidence</th>
                </tr>
              </thead>
              <tbody>
                {(status.tracks || []).map((track) => (
                  <tr key={track.track_id}>
                    <td title={track.track_id}>{track.track_id.slice(-8)}</td>
                    <td>{track.zone || "outside / unavailable"}</td>
                    <td>
                      {track.point
                        ? track.point.map((v) => v.toFixed(3)).join(", ")
                        : "—"}
                    </td>
                    <td>{track.reason.replaceAll("_", " ")}</td>
                    <td>{(track.verification || "—").replaceAll("_", " ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
      <DoorwayTimeLogs camera={camera} />
    </div>
  );
}
