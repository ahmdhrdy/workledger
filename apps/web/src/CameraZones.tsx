import { useEffect, useState } from "react";
import type { FormEvent, MouseEvent } from "react";
import { api } from "./api";
import type { Employee } from "./api";

type Zone = {
  id: string;
  name: string;
  kind: string;
  points: number[][];
  employees: string[];
  applies_all: boolean;
  counts_as_work: boolean;
  priority: number;
  allowed_from?: string | null;
  allowed_until?: string | null;
};
export default function CameraZones({
  camera,
  employees,
  saved,
}: {
  camera: { id: string; name: string };
  employees: Employee[];
  saved: () => void;
}) {
  const [zones, setZones] = useState<Zone[]>([]),
    [points, setPoints] = useState<number[][]>([]),
    [image, setImage] = useState(
      `/api/v1/local/cameras/${camera.id}/frame?raw=true&t=${Date.now()}`,
    );
  const [kind, setKind] = useState("workstation"),
    [everyone, setEveryone] = useState(false),
    [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState(""),
    [counts, setCounts] = useState(true),
    [priority, setPriority] = useState(0);
  const [allowedFrom, setAllowedFrom] = useState(""),
    [allowedUntil, setAllowedUntil] = useState("");
  useEffect(() => {
    void api<Zone[]>(`/local/cameras/${camera.id}/zones`)
      .then(setZones)
      .catch((e) => setError(e.message));
    setImage(
      `/api/v1/local/cameras/${camera.id}/frame?raw=true&t=${Date.now()}`,
    );
  }, [camera.id]);
  function corner(event: MouseEvent<SVGSVGElement>) {
    if (points.length >= 32) return;
    const box = event.currentTarget.getBoundingClientRect();
    setPoints([
      ...points,
      [
        Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)),
        Math.max(0, Math.min(1, (event.clientY - box.top) / box.height)),
      ],
    ]);
  }
  function reset() {
    setPoints([]);
    setEditing(null);
    setName("");
    setSelected([]);
    setEveryone(false);
    setKind("workstation");
    setCounts(true);
    setPriority(0);
    setAllowedFrom("");
    setAllowedUntil("");
  }
  async function persist(next: Zone[]) {
    setBusy(true);
    setError("");
    try {
      await api(`/local/cameras/${camera.id}/zones`, {
        method: "PUT",
        body: JSON.stringify({ zones: next }),
      });
      setZones(next);
      reset();
      saved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void persist([
      ...zones.filter((z) => z.id !== editing),
      {
        id: editing || crypto.randomUUID(),
        name,
        kind,
        points,
        employees: selected,
        applies_all: everyone,
        counts_as_work: counts,
        priority,
        allowed_from: allowedFrom || null,
        allowed_until: allowedUntil || null,
      },
    ]);
  }
  return (
    <div className="zone-editor">
      <p>
        Click at least three corners around a workstation. A person’s
        bottom-center point determines their zone. Assign several people for a
        shared desk, or everyone for a meeting area.
      </p>
      <div className="zone-canvas">
        <img
          src={image}
          alt={`Draw workstation polygons on ${camera.name}`}
          onError={() =>
            setError(
              "A recent camera image is required. Connect the camera, then refresh the image.",
            )
          }
        />
        <svg
          viewBox="0 0 1000 1000"
          preserveAspectRatio="none"
          onClick={corner}
          role="img"
          aria-label="Click to add polygon corners"
        >
          {zones
            .filter((z) => z.id !== editing)
            .map((z) => (
              <g key={z.id}>
                <polygon
                  points={z.points
                    .map((p) => `${p[0] * 1000},${p[1] * 1000}`)
                    .join(" ")}
                  className="saved-zone"
                />
                <text x={z.points[0][0] * 1000} y={z.points[0][1] * 1000}>
                  {z.name}
                </text>
              </g>
            ))}
          <polygon
            points={points
              .map((p) => `${p[0] * 1000},${p[1] * 1000}`)
              .join(" ")}
            className="drawing-zone"
          />
          {points.map((p, i) => (
            <circle key={i} cx={p[0] * 1000} cy={p[1] * 1000} r={6} />
          ))}
        </svg>
      </div>
      <div className="zone-actions">
        <button
          className="button secondary"
          onClick={() => setPoints(points.slice(0, -1))}
        >
          Undo corner
        </button>
        <button className="button secondary" onClick={reset}>
          New polygon
        </button>
        <button
          className="text-button"
          onClick={() => {
            setError("");
            setImage(
              `/api/v1/local/cameras/${camera.id}/frame?raw=true&t=${Date.now()}`,
            );
          }}
        >
          Refresh image
        </button>
        <span>{points.length} corners</span>
      </div>
      <form onSubmit={submit} className="settings-form">
        <div className="form-grid">
          <label className="field">
            <span>Allowed from (optional, site local time)</span>
            <input
              type="time"
              value={allowedFrom}
              onChange={(e) => setAllowedFrom(e.target.value)}
            />
          </label>
          <label className="field">
            <span>Allowed until (optional)</span>
            <input
              type="time"
              value={allowedUntil}
              onChange={(e) => setAllowedUntil(e.target.value)}
            />
          </label>
        </div>
        <p className="fineprint">
          Leave both blank for the whole shift. Approved dining/break time is
          excluded up to the office break allowance; time outside this window is
          not approved break credit.
        </p>
        <div className="form-grid">
          <label className="field">
            <span>Area name</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              minLength={2}
              maxLength={100}
              placeholder="Design team desk"
            />
          </label>
          <label className="field">
            <span>Area type</span>
            <select
              value={kind}
              onChange={(e) => {
                setKind(e.target.value);
                setCounts(["workstation", "meeting"].includes(e.target.value));
              }}
            >
              <option value="workstation">Workstation / shared desk</option>
              <option value="meeting">Meeting room</option>
              <option value="break">Break / dining area</option>
              <option value="transit">Corridor / transit</option>
            </select>
          </label>
        </div>
        <label className="checkline">
          <input
            type="checkbox"
            checked={everyone}
            onChange={(e) => setEveryone(e.target.checked)}
          />{" "}
          Applies to all employees
        </label>
        {!everyone && (
          <div className="zone-people">
            {employees
              .filter((e) => e.active)
              .map((e) => (
                <label className="checkline" key={e.id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(e.id)}
                    onChange={(event) =>
                      setSelected(
                        event.target.checked
                          ? [...selected, e.id]
                          : selected.filter((id) => id !== e.id),
                      )
                    }
                  />
                  {e.name} · {e.code}
                </label>
              ))}
          </div>
        )}
        <label className="checkline">
          <input
            type="checkbox"
            checked={counts}
            disabled={["break", "transit"].includes(kind)}
            onChange={(e) => setCounts(e.target.checked)}
          />{" "}
          Count this area as workplace time
        </label>
        <label className="field">
          <span>Overlap priority (higher wins; smallest area breaks ties)</span>
          <input
            type="number"
            min={0}
            max={100}
            value={priority}
            onChange={(e) => setPriority(Number(e.target.value))}
          />
        </label>
        {error && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <button
          className="button primary"
          disabled={
            busy || points.length < 3 || (!everyone && !selected.length)
          }
        >
          {busy
            ? "Saving…"
            : editing
              ? "Save polygon changes"
              : "Save new polygon"}
        </button>
      </form>
      <div className="zone-list">
        {zones.map((z) => (
          <div className="enrollment-row" key={z.id}>
            <div>
              <strong>{z.name}</strong>
              <small>
                {z.kind} ·{" "}
                {z.applies_all ? "Everyone" : `${z.employees.length} assigned`}{" "}
                · {z.counts_as_work ? "Work time" : "Non-work area"}
              </small>
            </div>
            <button
              className="text-button"
              onClick={() => {
                setEditing(z.id);
                setName(z.name);
                setPoints(z.points);
                setSelected(z.employees);
                setEveryone(z.applies_all);
                setKind(z.kind);
                setCounts(z.counts_as_work);
                setPriority(z.priority);
                setAllowedFrom(z.allowed_from || "");
                setAllowedUntil(z.allowed_until || "");
              }}
            >
              Edit
            </button>
            <button
              className="text-button"
              disabled={busy}
              onClick={() =>
                void persist(zones.filter((item) => item.id !== z.id))
              }
            >
              Remove
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
