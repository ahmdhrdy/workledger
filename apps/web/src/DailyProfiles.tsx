import { useEffect, useState } from "react";
import { api } from "./api";
type Profile = {
  employee_id: string;
  name: string;
  code: string;
  nickname: string;
  day: string;
  expires_at: number;
  created_at: number;
  updated_at: number;
  clothing?: { upper: string; lower: string };
  signature_count: number;
  source: string;
};
export default function DailyProfiles({ siteId }: { siteId: string }) {
  const [items, setItems] = useState<Profile[]>([]),
    [error, setError] = useState("");
  async function load() {
    setItems(await api<Profile[]>(`/local/daily-profiles?site_id=${siteId}`));
  }
  useEffect(() => {
    void load().catch((e) => setError(e.message));
    const timer = setInterval(
      () => void load().catch((e) => setError(e.message)),
      5000,
    );
    return () => clearInterval(timer);
  }, [siteId]);
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>Today’s appearance matrices</h2>
          <p>
            Created after stable face identification or HR review. Encrypted
            body signatures expire at site-local midnight.
          </p>
        </div>
        <span className="badge">{items.length} profiles</span>
      </div>
      <div className="note">
        Permanent face enrollment is separate. Clothing colors are estimates;
        similar uniforms and camera angles can still produce uncertain matches.
      </div>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Employee</th>
              <th>Overlay nickname</th>
              <th>Appearance estimate</th>
              <th>Signatures</th>
              <th>Expires</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((p) => (
              <tr key={p.employee_id}>
                <td>
                  <strong>{p.name}</strong>
                  <small className="block muted">
                    {p.code} · {p.day}
                  </small>
                </td>
                <td>{p.nickname || p.name}</td>
                <td>
                  Upper: {p.clothing?.upper || "unknown"}
                  <br />
                  Lower: {p.clothing?.lower || "unknown"}
                </td>
                <td>
                  {p.signature_count} · {p.source}
                </td>
                <td>{new Date(p.expires_at * 1000).toLocaleString()}</td>
                <td>
                  <button
                    className="text-button"
                    onClick={async () => {
                      try {
                        await api(`/local/daily-profiles/${p.employee_id}`, {
                          method: "DELETE",
                        });
                        await load();
                      } catch (e) {
                        setError((e as Error).message);
                      }
                    }}
                  >
                    Reset today
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!items.length && (
        <p className="padded muted">
          No employee has a confirmed daily body signature yet. Enroll
          employees, then let the entry camera see a clear face and body.
        </p>
      )}
    </section>
  );
}
