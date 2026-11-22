import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, post } from "./api";
import type { Employee, Event } from "./api";
function zonedISO(value: string, timezone: string) {
  const wanted = Date.parse(value + ":00Z");
  let guess = wanted;
  const fmt = new Intl.DateTimeFormat("sv-SE", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  for (let i = 0; i < 3; i++) {
    const local = fmt.format(new Date(guess)).replace(" ", "T");
    guess += wanted - Date.parse(local + "Z");
  }
  return new Date(guess).toISOString();
}
export default function MovementCorrection({
  employees,
  employeeId,
  siteId,
  day,
  timezone,
  close,
  changed,
}: {
  employees: Employee[];
  employeeId: string;
  siteId: string;
  day: string;
  timezone: string;
  close: () => void;
  changed: () => Promise<void>;
}) {
  const ref = useRef<HTMLDialogElement>(null),
    [events, setEvents] = useState<Event[]>([]),
    [selected, setSelected] = useState(employeeId),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    ref.current?.showModal();
    void api<Event[]>(`/events?site_id=${siteId}&day=${day}`)
      .then(setEvents)
      .catch((e) => setError(e.message));
    return () => ref.current?.close();
  }, [siteId, day]);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    setBusy(true);
    setError("");
    try {
      await post("/events/manual", {
        employee_id: selected,
        kind: data.get("kind"),
        occurred_at: zonedISO(String(data.get("at")), timezone),
        reason: data.get("reason"),
      });
      await changed();
      close();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <dialog ref={ref} className="vision-dialog" onCancel={close}>
      <div className="modal-heading">
        <h2>Correct missing or incorrect movements</h2>
        <button
          className="icon-button"
          aria-label="Close movement correction"
          onClick={close}
        >
          ×
        </button>
      </div>
      <form className="modal-form" onSubmit={submit}>
        <label className="field">
          <span>Employee</span>
          <select
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            required
          >
            <option value="" disabled>
              Select employee
            </option>
            {employees.map((e) => (
              <option key={e.id} value={e.id}>
                {e.name} · {e.code}
              </option>
            ))}
          </select>
        </label>
        <div className="form-grid">
          <label className="field">
            <span>Missing movement</span>
            <select name="kind">
              <option value="entry">Entry / IN</option>
              <option value="exit">Exit / OUT</option>
            </select>
          </label>
          <label className="field">
            <span>Actual time ({timezone})</span>
            <input
              name="at"
              type="datetime-local"
              defaultValue={day + "T09:00"}
              required
            />
          </label>
        </div>
        <label className="field">
          <span>Verified evidence / reason</span>
          <textarea name="reason" minLength={5} maxLength={1000} required />
        </label>
        <p className="fineprint">
          Corrections are identified as HR-entered events. Review attendance
          again after changing movements.
        </p>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button className="button primary" disabled={busy}>
          {busy ? "Saving…" : "Add verified movement"}
        </button>
      </form>
      <div className="padded">
        <h3>Recorded movements for this date</h3>
        {events
          .filter(
            (e) =>
              e.employee_id === selected && ["entry", "exit"].includes(e.kind),
          )
          .map((e) => (
            <div key={e.id} className="enrollment-row">
              <div>
                <strong>
                  {e.kind.toUpperCase()} ·{" "}
                  {new Date(e.occurred_at).toLocaleTimeString("en-GB", {
                    timeZone: timezone,
                  })}
                </strong>
                <small>{e.source}</small>
              </div>
              <button
                className="text-button"
                disabled={busy}
                onClick={async () => {
                  const reason = prompt(
                    "Reason to void this incorrect event (original evidence remains in the audit trail):",
                  );
                  if (!reason) return;
                  setBusy(true);
                  try {
                    await post(`/events/${e.id}/void`, { reason });
                    setEvents(events.filter((item) => item.id !== e.id));
                    await changed();
                  } catch (err) {
                    setError((err as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Void incorrect event
              </button>
            </div>
          ))}
      </div>
    </dialog>
  );
}
