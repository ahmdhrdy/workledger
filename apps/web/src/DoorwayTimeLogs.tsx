import { useEffect, useState } from "react";
import { api } from "./api";

type Crossing = {
  id: string;
  employee_id: string | null;
  employee_name: string | null;
  camera_name: string;
  zone_name: string;
  kind: string;
  occurred_at: string;
  status: string;
  confidence: number | null;
  reason: string;
  zone_version: number;
};
export default function DoorwayTimeLogs({
  camera,
}: {
  camera: { id: string; name: string };
}) {
  const [logs, setLogs] = useState<Crossing[]>([]);
  const [error, setError] = useState("");
  const [updated, setUpdated] = useState<Date | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const rows = await api<Crossing[]>(
          `/local/cameras/${camera.id}/door-crossings`,
        );
        if (!cancelled) {
          setLogs(rows);
          setUpdated(new Date());
          setError("");
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) {
          setLoading(false);
          timer = setTimeout(load, 2000);
        }
      }
    }
    setLogs([]);
    setLoading(true);
    void load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [camera.id]);
  return (
    <section className="door-log-panel" aria-label="Recent doorway time logs">
      <header>
        <div>
          <h3>Recent doorway time logs</h3>
          <p>{camera.name} · Updates automatically every 2 seconds</p>
        </div>
        <span className="fineprint">
          {updated ? `Updated ${updated.toLocaleTimeString()}` : "Connecting…"}
        </span>
      </header>
      <p className="fineprint">
        Timestamps use {Intl.DateTimeFormat().resolvedOptions().timeZone}. Only
        confirmed records count toward attendance. Match scores are cosine
        similarity, not confidence percentages.
      </p>
      {error && (
        <p className="form-error" role="alert">
          Could not refresh doorway logs: {error}. Retrying automatically.
        </p>
      )}
      <div className="door-table-scroll">
        <table>
          <thead>
            <tr>
              <th>Exact timestamp</th>
              <th>Employee / ID</th>
              <th>Direction</th>
              <th>Camera / zone</th>
              <th>Status / match</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {logs.map((log) => (
              <tr key={log.id}>
                <td>
                  <time dateTime={log.occurred_at}>
                    {new Date(log.occurred_at).toLocaleString(undefined, {
                      year: "numeric",
                      month: "2-digit",
                      day: "2-digit",
                      hour: "2-digit",
                      minute: "2-digit",
                      second: "2-digit",
                      fractionalSecondDigits: 3,
                      hour12: false,
                    })}
                  </time>
                </td>
                <td>
                  <strong>{log.employee_name || "Unverified person"}</strong>
                  <small>{log.employee_id || "Identity pending"}</small>
                </td>
                <td>
                  <span
                    className={`door-direction ${log.kind === "entry" ? "in" : "out"}`}
                  >
                    {log.kind === "entry" ? "IN" : "OUT"}
                  </span>
                </td>
                <td>
                  {log.camera_name || camera.name}
                  <small>
                    {log.zone_name || "Doorway"} · v{log.zone_version}
                  </small>
                </td>
                <td>
                  <span className={`door-event-status ${log.status}`}>
                    {log.status}
                  </span>
                  <small>
                    Match:{" "}
                    {log.confidence == null
                      ? "not available"
                      : log.confidence.toFixed(3)}
                  </small>
                </td>
                <td>{log.reason.replaceAll("_", " ")}</td>
              </tr>
            ))}
            {!logs.length && (
              <tr>
                <td colSpan={6}>
                  {loading
                    ? "Loading crossings…"
                    : error
                      ? "No current log data available."
                      : "No doorway crossings recorded yet. Complete A → Transition → B or the reverse; inspect Live trigger diagnostics if no row appears."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
