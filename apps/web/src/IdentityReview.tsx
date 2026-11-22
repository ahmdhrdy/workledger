import { useState } from "react";
import type { FormEvent } from "react";
import { post } from "./api";
import type { Employee } from "./api";
export type IdentityEvent = {
  id: string;
  camera_name: string;
  kind: string;
  occurred_at: string;
  employee_id: string | null;
  outcome: string;
  reason: string;
  visitor_name: string;
};
function Evidence({ id }: { id: string }) {
  const [error, setError] = useState(false);
  return error ? (
    <small>Evidence unavailable or expired.</small>
  ) : (
    <img
      className="event-evidence"
      src={`/api/v1/local/events/${id}/evidence`}
      alt="Unmatched camera detection"
      loading="lazy"
      onError={() => setError(true)}
    />
  );
}
function Card({
  event,
  employees,
  changed,
}: {
  event: IdentityEvent;
  employees: Employee[];
  changed: () => Promise<void>;
}) {
  const [classification, setClassification] = useState("employee"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    setBusy(true);
    setError("");
    try {
      await post(`/local/events/${event.id}/resolve`, {
        classification,
        employee_id:
          classification === "employee" ? data.get("employee_id") : null,
        visitor_name:
          classification === "visitor" ? data.get("visitor_name") : "",
        reason: data.get("reason"),
      });
      await changed();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="identity-review review-card" onSubmit={submit}>
      <div>
        <Evidence id={event.id} />
        <strong>{event.camera_name}</strong>
        <small>
          {event.kind === "interior"
            ? "Workplace detection"
            : `${event.kind.toUpperCase()} attendance`}{" "}
          · {new Date(event.occurred_at).toLocaleTimeString()}
        </small>
      </div>
      <div className="review-inputs">
        <label className="field">
          <span>Classify detection</span>
          <select
            value={classification}
            onChange={(e) => setClassification(e.target.value)}
          >
            <option value="employee">Employee missed by recognition</option>
            <option value="visitor">Visitor</option>
            <option value="false_detection">
              False / irrelevant detection
            </option>
          </select>
        </label>
        {classification === "employee" && (
          <label className="field">
            <span>Recognized employee</span>
            <select name="employee_id" required defaultValue="">
              <option value="" disabled>
                Select employee
              </option>
              {employees
                .filter((e) => e.active)
                .map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.name} · {e.code}
                  </option>
                ))}
            </select>
          </label>
        )}
        {classification === "visitor" && (
          <label className="field">
            <span>Visitor name (optional)</span>
            <input name="visitor_name" maxLength={100} />
          </label>
        )}
        <label className="field">
          <span>
            {classification === "visitor"
              ? "Reason for visit"
              : "Evidence / reason for correction"}
          </span>
          <textarea
            name="reason"
            minLength={5}
            maxLength={1000}
            required
            placeholder={
              classification === "visitor"
                ? "Meeting with accounts team"
                : "Describe the evidence HR checked"
            }
          />
        </label>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button className="button primary" disabled={busy}>
          {busy ? "Saving…" : "Save reviewed identity"}
        </button>
      </div>
    </form>
  );
}
export default function IdentityReview({
  events,
  employees,
  changed,
}: {
  events: IdentityEvent[];
  employees: Employee[];
  changed: () => Promise<void>;
}) {
  const [history, setHistory] = useState(false);
  const pending = events.filter((e) => e.outcome === "pending"),
    reviewed = events.filter((e) => e.outcome !== "pending");
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>Unmatched faces & visitor review</h2>
          <p>
            Entry, exit and workplace cameras · original detections stay in the
            audit trail.
          </p>
        </div>
        <span className="badge amber">{pending.length} pending</span>
      </div>
      {pending.map((event) => (
        <Card
          key={event.id}
          event={event}
          employees={employees}
          changed={changed}
        />
      ))}
      {!pending.length && (
        <p className="padded muted">
          No unmatched detections awaiting review for this date.
        </p>
      )}
      <div className="padded">
        <button className="text-button" onClick={() => setHistory(!history)}>
          {history ? "Hide" : "Show"} reviewed detections ({reviewed.length})
        </button>
      </div>
      {history &&
        reviewed.map((e) => (
          <div className="enrollment-row" key={e.id}>
            <div>
              <strong>
                {e.outcome === "visitor"
                  ? e.visitor_name || "Visitor"
                  : e.outcome === "assigned"
                    ? employees.find((person) => person.id === e.employee_id)
                        ?.name || "Employee"
                    : "False / irrelevant detection"}
              </strong>
              <small>
                {e.camera_name} · {new Date(e.occurred_at).toLocaleString()} ·{" "}
                {e.reason}
              </small>
            </div>
            <span className="badge">{e.outcome}</span>
          </div>
        ))}
    </section>
  );
}
