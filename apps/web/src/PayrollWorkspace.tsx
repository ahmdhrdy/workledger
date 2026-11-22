import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api, post, currency } from "./api";
import type { Employee, Payroll, Run } from "./api";
type Adjustment = {
  id: string;
  name: string;
  kind: string;
  amount: string;
  reason: string;
};
type Snapshot = {
  id: string;
  status: string;
  stale: boolean;
  snapshot: Payroll;
};
export default function PayrollWorkspace({
  siteId,
  day,
  employees,
  runs,
  changed,
}: {
  siteId: string;
  day: string;
  employees: Employee[];
  runs: Run[];
  changed: () => Promise<void>;
}) {
  const [month, setMonth] = useState(day.slice(0, 7)),
    [preview, setPreview] = useState<Payroll | null>(null),
    [adjustments, setAdjustments] = useState<Adjustment[]>([]),
    [selected, setSelected] = useState<Snapshot | null>(null),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [revision, setRevision] = useState(0);
  async function load() {
    const [result, extras] = await Promise.all([
      post<Payroll>("/payroll/preview", { site_id: siteId, month }),
      api<Adjustment[]>(
        `/payroll/adjustments?site_id=${siteId}&month=${month}`,
      ),
    ]);
    setPreview(result);
    setAdjustments(extras);
  }
  useEffect(() => {
    setPreview(null);
    void load().catch((e) => setError(e.message));
  }, [siteId, month, runs, revision]);
  async function action(fn: () => Promise<unknown>, message: string) {
    setBusy(true);
    setError("");
    try {
      await fn();
      setNotice(message);
      setRevision((r) => r + 1);
      await changed();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function addAdjustment(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget,
      data = new FormData(form);
    await action(async () => {
      await post("/payroll/adjustments", {
        employee_id: data.get("employee_id"),
        month,
        kind: data.get("kind"),
        amount: data.get("amount"),
        reason: data.get("reason"),
      });
      form.reset();
    }, "Payroll adjustment recorded with a reason.");
  }
  async function finalize(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!selected) return;
    const data = new FormData(e.currentTarget);
    await action(async () => {
      await post(`/payroll/runs/${selected.id}/finalize`, {
        reason: data.get("reason"),
      });
      setSelected(await api<Snapshot>(`/payroll/runs/${selected.id}`));
    }, "Payroll finalized. This snapshot is preserved for payment and payslips.");
  }
  return (
    <>
      <div className="payroll-toolbar">
        <label className="date-control">
          Payroll month{" "}
          <input
            type="month"
            value={month}
            aria-label="Payroll month"
            onChange={(e) => {
              if (e.target.value) {
                setMonth(e.target.value);
                setSelected(null);
              }
            }}
          />
        </label>
        <button
          className="button primary"
          disabled={busy || !preview}
          onClick={() =>
            void action(async () => {
              const run = await post<{ id: string }>("/payroll/runs", {
                site_id: siteId,
                month,
              });
              setSelected(await api<Snapshot>(`/payroll/runs/${run.id}`));
            }, "Draft snapshot saved. Review it before finalizing.")
          }
        >
          Save reviewed draft
        </button>
      </div>
      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="alert success" role="status">
          {notice}
        </p>
      )}
      <div className="note">
        Monthly base × approved paid units / scheduled units, plus approved
        overtime, holiday work and allowances, less explicit deductions. Paid
        holidays need no attendance. No automatic deduction uses workplace
        presence.
      </div>
      {preview && (
        <>
          <div className="stat-grid three">
            <article className="stat-card">
              <div>
                <span>Provisional net total</span>
              </div>
              <strong className="money">৳ {currency(preview.total)}</strong>
              <p>{preview.items.length} employees · BDT</p>
            </article>
            <article className="stat-card amber">
              <div>
                <span>Unresolved inputs</span>
              </div>
              <strong>{preview.blockers.length}</strong>
              <p>Review attendance, identity and calendar conflicts</p>
            </article>
            <article className="stat-card">
              <div>
                <span>Calculation status</span>
              </div>
              <strong className="status-text">
                {preview.can_finalize ? "Review ready" : "Blocked"}
              </strong>
              <p>Finalize only after the month ends</p>
            </article>
          </div>
          <section className="panel">
            <div className="panel-heading">
              <h2>Salary sheet preview</h2>
            </div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Employee</th>
                    <th>Monthly base</th>
                    <th>Paid units</th>
                    <th>Earned base</th>
                    <th>OT / holiday</th>
                    <th>Allowances</th>
                    <th>Deductions</th>
                    <th>Net</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.items.map((e) => (
                    <tr key={e.employee_id}>
                      <td>
                        <strong>{e.name}</strong>
                        <small className="muted block">{e.code}</small>
                        {e.unresolved_days > 0 && (
                          <small className="form-error">
                            {e.unresolved_days} unresolved
                          </small>
                        )}
                      </td>
                      <td>৳ {currency(e.base_salary)}</td>
                      <td>
                        {e.paid_days}/{e.scheduled_days}
                        <small className="muted block">
                          {e.paid_holidays} holidays included
                        </small>
                      </td>
                      <td>৳ {currency(e.earned_salary)}</td>
                      <td>
                        ৳ {currency(e.overtime)} / {currency(e.holiday_pay)}
                      </td>
                      <td>৳ {currency(e.allowances)}</td>
                      <td>৳ {currency(e.deductions)}</td>
                      <td>
                        <strong>৳ {currency(e.net)}</strong>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!preview.items.length && (
              <p className="padded muted">
                Enroll employees to calculate payroll.
              </p>
            )}
            {preview.blockers.length > 0 && (
              <details className="padded">
                <summary>
                  Show calculation blockers ({preview.blockers.length})
                </summary>
                {preview.blockers.map((b, i) => (
                  <p key={i}>
                    {b.name}: {b.reason} ({b.days})
                  </p>
                ))}
              </details>
            )}
          </section>
        </>
      )}
      <div className="calendar-layout">
        <section className="panel">
          <div className="panel-heading">
            <h2>Allowances & deductions</h2>
          </div>
          <form className="settings-form" onSubmit={addAdjustment}>
            <div className="form-grid">
              <label className="field">
                <span>Employee</span>
                <select
                  name="employee_id"
                  aria-label="Employee"
                  required
                  defaultValue=""
                >
                  <option value="" disabled>
                    Select employee
                  </option>
                  {employees.map((e) => (
                    <option key={e.id} value={e.id}>
                      {e.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Adjustment type</span>
                <select name="kind">
                  <option value="allowance">Allowance / bonus</option>
                  <option value="deduction">Deduction / approved tax</option>
                </select>
              </label>
              <label className="field">
                <span>Amount (BDT)</span>
                <input
                  name="amount"
                  type="number"
                  min=".01"
                  max={10000000}
                  step=".01"
                  required
                />
              </label>
              <label className="field">
                <span>Reason</span>
                <input name="reason" minLength={5} maxLength={1000} required />
              </label>
            </div>
            <button className="button secondary" disabled={busy}>
              Add adjustment for {month}
            </button>
          </form>
          {adjustments.map((a) => (
            <div className="enrollment-row" key={a.id}>
              <div>
                <strong>
                  {a.name} · {a.kind} · ৳ {currency(a.amount)}
                </strong>
                <small>{a.reason}</small>
              </div>
              <button
                className="text-button"
                disabled={busy}
                onClick={() =>
                  void action(
                    () =>
                      api(`/payroll/adjustments/${a.id}`, { method: "DELETE" }),
                    "Adjustment removed. Recalculate any drafts.",
                  )
                }
              >
                Remove
              </button>
            </div>
          ))}
        </section>
        <section className="panel">
          <div className="panel-heading">
            <h2>Saved payroll snapshots</h2>
          </div>
          {runs.map((run) => (
            <div className="enrollment-row" key={run.id}>
              <div>
                <strong>
                  {run.month} · ৳ {currency(run.total)}
                </strong>
                <small>
                  {new Date(run.created_at).toLocaleString()} · {run.status}
                </small>
              </div>
              <button
                className="text-button"
                onClick={() => {
                  setError("");
                  void api<Snapshot>(`/payroll/runs/${run.id}`)
                    .then(setSelected)
                    .catch((e) => setError(e.message));
                }}
              >
                Review snapshot
              </button>
            </div>
          ))}
          {!runs.length && (
            <p className="padded muted">No salary sheets saved.</p>
          )}
        </section>
      </div>
      {selected && (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>
                {selected.snapshot.month} · {selected.status} snapshot
              </h2>
              <p>
                ৳ {currency(selected.snapshot.total)} ·{" "}
                {selected.snapshot.items.length} employees
              </p>
            </div>
            <a
              className="button secondary"
              href={`/api/v1/payroll/runs/${selected.id}/export.csv`}
            >
              Export salary sheet CSV
            </a>
          </div>
          {selected.stale && (
            <p className="note amber">
              Current inputs differ from this snapshot. Save a new draft for
              current calculations. A finalized snapshot keeps its approved
              figures.
            </p>
          )}
          <div className="padded payslip-links">
            {selected.snapshot.items.map((e) => (
              <a
                key={e.employee_id}
                className="button secondary"
                href={`/api/v1/payroll/runs/${selected.id}/payslip/${e.employee_id}`}
                target="_blank"
                rel="noreferrer"
              >
                Payslip · {e.name} · ৳ {currency(e.net)}
              </a>
            ))}
          </div>
          {selected.status === "draft" && (
            <form className="settings-form" onSubmit={finalize}>
              <label className="field">
                <span>Final approval reason</span>
                <input
                  name="reason"
                  minLength={5}
                  maxLength={1000}
                  required
                  placeholder="Attendance and salary sheet checked by payroll"
                />
              </label>
              <button
                className="button primary"
                disabled={
                  busy || selected.stale || !selected.snapshot.can_finalize
                }
              >
                Finalize salary sheet
              </button>
              <p className="fineprint">
                Finalization requires complete HR approvals, unchanged inputs
                and a completed payroll month. Finalized snapshots cannot be
                edited.
              </p>
            </form>
          )}
        </section>
      )}
    </>
  );
}
