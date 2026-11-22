import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api, post } from "./api";
import type { Site } from "./api";
type Day = {
  id: string;
  day: string;
  name: string;
  kind: string;
  note: string;
};
export default function OfficeCalendar({
  site,
  day,
  canEdit,
  changed,
}: {
  site: Site;
  day: string;
  canEdit: boolean;
  changed: () => Promise<void>;
}) {
  const [month, setMonth] = useState(day.slice(0, 7)),
    [items, setItems] = useState<Day[]>([]),
    [selected, setSelected] = useState(day),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  async function load() {
    setItems(await api<Day[]>(`/calendar?site_id=${site.id}&month=${month}`));
  }
  useEffect(() => {
    void load().catch((e) => setError(e.message));
  }, [site.id, month]);
  const current = items.find((i) => i.day === selected),
    [year, number] = month.split("-").map(Number),
    days = new Date(year, number, 0).getDate(),
    offset = (new Date(year, number - 1, 1).getDay() + 6) % 7;
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    setBusy(true);
    setError("");
    try {
      await post("/calendar", {
        site_id: site.id,
        day: selected,
        name: data.get("name"),
        kind: data.get("kind"),
        note: data.get("note"),
      });
      await load();
      await changed();
      setNotice("Calendar saved. Payroll will use this day’s classification.");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function policy(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    setBusy(true);
    setError("");
    try {
      await api(`/sites/${site.id}/payroll-policy`, {
        method: "PUT",
        body: JSON.stringify({
          overtime_multiplier: data.get("overtime_multiplier"),
          holiday_multiplier: data.get("holiday_multiplier"),
          holiday_hourly_rate: data.get("holiday_hourly_rate"),
        }),
      });
      await changed();
      setNotice("Pay rates saved. Existing drafts must be recalculated.");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="note">
        Paid holidays keep normal monthly salary and require no attendance. Work
        on a holiday or weekly off earns separately approved holiday pay.
        Working-day overrides can open an otherwise closed day.
      </div>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <div className="alert success" role="status">
          {notice}
        </div>
      )}
      <div className="calendar-layout">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Office holiday calendar</h2>
              <p>Click a date to configure the office schedule.</p>
            </div>
            <input
              aria-label="Calendar month"
              type="month"
              value={month}
              onChange={(e) => {
                if (e.target.value) {
                  setMonth(e.target.value);
                  setSelected(e.target.value + "-01");
                }
              }}
            />
          </div>
          <div className="calendar-grid">
            {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((label) => (
              <strong className="calendar-label" key={label}>
                {label}
              </strong>
            ))}
            {Array.from({ length: offset }, (_, i) => (
              <span key={`empty-${i}`} />
            ))}
            {Array.from({ length: days }, (_, i) => {
              const value = `${month}-${String(i + 1).padStart(2, "0")}`,
                entry = items.find((d) => d.day === value),
                off = !site.weekdays.includes(
                  (new Date(year, number - 1, i + 1).getDay() + 6) % 7,
                );
              return (
                <button
                  key={value}
                  className={`calendar-date ${selected === value ? "selected" : ""} ${entry?.kind || (off ? "weekly_off" : "working_day")}`}
                  onClick={() => setSelected(value)}
                >
                  <b>{i + 1}</b>
                  <span>
                    {entry?.name || (off ? "Weekly off" : "Working day")}
                  </span>
                </button>
              );
            })}
          </div>
          <div className="calendar-legend">
            <span>Paid holiday</span>
            <span>Weekly off / closure</span>
            <span>Working day</span>
          </div>
        </section>
        <section className="panel">
          <div className="panel-heading">
            <h2>{selected}</h2>
          </div>
          <form
            key={`${selected}-${current?.id}-${current?.kind}-${current?.name}`}
            className="settings-form"
            onSubmit={save}
          >
            <label className="field">
              <span>Day classification</span>
              <select
                name="kind"
                defaultValue={current?.kind || "paid_holiday"}
                disabled={!canEdit}
              >
                <option value="paid_holiday">
                  Paid holiday — salary protected
                </option>
                <option value="unpaid_closure">Unpaid office closure</option>
                <option value="working_day">Working day override</option>
              </select>
            </label>
            <label className="field">
              <span>Holiday / schedule name</span>
              <input
                name="name"
                defaultValue={current?.name || ""}
                required
                minLength={2}
                maxLength={120}
                disabled={!canEdit}
              />
            </label>
            <label className="field">
              <span>Notes</span>
              <textarea
                name="note"
                defaultValue={current?.note || ""}
                maxLength={1000}
                disabled={!canEdit}
              />
            </label>
            {canEdit && (
              <div className="zone-actions">
                <button className="button primary" disabled={busy}>
                  Save date
                </button>
                {current && (
                  <button
                    className="button secondary"
                    type="button"
                    disabled={busy}
                    onClick={async () => {
                      setError("");
                      try {
                        await api(`/calendar/${current.id}`, {
                          method: "DELETE",
                        });
                        await load();
                        await changed();
                        setNotice("Date reset to the regular weekly schedule.");
                      } catch (e) {
                        setError((e as Error).message);
                      }
                    }}
                  >
                    Reset to weekly schedule
                  </button>
                )}
              </div>
            )}
          </form>
        </section>
      </div>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Overtime & holiday work rates</h2>
            <p>
              Explicit office policy. Configure your applicable rates before
              using payroll.
            </p>
          </div>
        </div>
        <form
          className="settings-form"
          onSubmit={policy}
          key={JSON.stringify(site.payroll_policy)}
        >
          <div className="form-grid">
            <label className="field">
              <span>Regular overtime multiplier</span>
              <input
                name="overtime_multiplier"
                type="number"
                min={0}
                max={10}
                step=".01"
                defaultValue={site.payroll_policy.overtime_multiplier || "1"}
                disabled={!canEdit}
                required
              />
            </label>
            <label className="field">
              <span>Holiday work multiplier</span>
              <input
                name="holiday_multiplier"
                type="number"
                min={0}
                max={10}
                step=".01"
                defaultValue={site.payroll_policy.holiday_multiplier || "1"}
                disabled={!canEdit}
                required
              />
            </label>
            <label className="field">
              <span>Holiday base rate / hour (BDT)</span>
              <input
                name="holiday_hourly_rate"
                type="number"
                min={0}
                max={100000}
                step=".01"
                defaultValue={site.payroll_policy.holiday_hourly_rate || "0"}
                disabled={!canEdit}
                required
              />
              <small>0 uses each employee’s overtime hourly rate.</small>
            </label>
          </div>
          {canEdit && (
            <button className="button primary" disabled={busy}>
              Save pay policy
            </button>
          )}
        </form>
      </section>
    </>
  );
}
