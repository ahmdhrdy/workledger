import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  Activity,
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  CalendarDays,
  Camera as CameraIcon,
  Check,
  CheckCircle2,
  CircleHelp,
  Clock3,
  Download,
  LayoutDashboard,
  Leaf,
  LogOut,
  Plus,
  RefreshCw,
  LockKeyhole,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Users,
  Wallet,
  X,
  AlertCircle,
  ClipboardCheck,
  Server,
  Building2,
} from "lucide-react";
import { api, post, clock, initials, minutes } from "./api";
import BrandMark from "./BrandMark";
import LocalVision from "./LocalVision";
import Team, { EmployeeDialog } from "./Team";
import OfficeCalendar from "./OfficeCalendar";
import PayrollWorkspace from "./PayrollWorkspace";
import MovementCorrection from "./MovementCorrection";
import UserAccess from "./UserAccess";
import type {
  Agent,
  Attendance,
  Audit,
  Camera,
  Employee,
  Event,
  Run,
  Site,
  Workspace,
} from "./api";

type View =
  | "Overview"
  | "Attendance"
  | "Employees"
  | "Cameras"
  | "Review inbox"
  | "Payroll"
  | "Settings"
  | "Office calendar";
const navigation = [
  { name: "Overview", icon: LayoutDashboard },
  { name: "Attendance", icon: CalendarDays },
  { name: "Employees", icon: Users },
  { name: "Cameras", icon: CameraIcon },
  { name: "Review inbox", icon: ClipboardCheck },
  { name: "Office calendar", icon: CalendarDays },
  { name: "Payroll", icon: Wallet },
  { name: "Settings", icon: Settings2 },
] as const;
type Data = {
  rows: Attendance[];
  unknown: number;
  employees: Employee[];
  cameras: Camera[];
  events: Event[];
  runs: Run[];
  audit: Audit[];
  agents: Agent[];
};
const emptyData: Data = {
  rows: [],
  unknown: 0,
  employees: [],
  cameras: [],
  events: [],
  runs: [],
  audit: [],
  agents: [],
};

function Badge({
  children,
  tone = "",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return (
    <span className={`badge ${tone}`}>
      <i />
      {children}
    </span>
  );
}
function Avatar({ name, small = false }: { name: string; small?: boolean }) {
  return (
    <span className={`avatar ${small ? "small" : ""}`}>{initials(name)}</span>
  );
}
function Empty({
  title,
  detail,
  action,
}: {
  title: string;
  detail: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Leaf size={26} />
      </div>
      <h3>{title}</h3>
      <p>{detail}</p>
      {action}
    </div>
  );
}
function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
function Modal({
  title,
  children,
  close,
}: {
  title: string;
  children: ReactNode;
  close: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
    return () => ref.current?.close();
  }, []);
  return (
    <dialog ref={ref} onCancel={close}>
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

function Access({
  setup,
  done,
}: {
  setup: boolean;
  done: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const f = new FormData(event.currentTarget);
    try {
      await post(
        setup ? "/setup" : "/auth/login",
        setup
          ? {
              company: f.get("company"),
              site: f.get("site"),
              name: f.get("name"),
              email: f.get("email"),
              password: f.get("password"),
              demo: false,
            }
          : { email: f.get("email"), password: f.get("password") },
      );
      await done();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="access">
      <aside className="access-brand">
        <a className="brand">
          <span>
            <BrandMark size={25} />
          </span>
          workledger<span className="brand-dot">.</span>
        </a>
        <div>
          <span className="eyebrow">A clearer view of your workplace</span>
          <h1>
            Every arrival.
            <br />
            Every workday.
            <br />
            <em>Accounted for.</em>
          </h1>
          <p>
            Attendance, thoughtful review, and payroll in one workspace. Your
            cameras and biometric data stay on your network.
          </p>
        </div>
        <div className="access-foot">
          <ShieldCheck size={18} /> Local intelligence. Private by design.
        </div>
      </aside>
      <section className="access-form">
        <span className="dev-chip">Local AI build · 0.3</span>
        <div className="access-inner">
          <div className="logo-mark">
            <Leaf />
          </div>
          <h2>{setup ? "Make room for better work." : "Welcome back."}</h2>
          <p>
            {setup
              ? "Create your local development workspace. Connect your cameras and enroll employee faces after setup."
              : "Sign in to your company workspace."}
          </p>
          <form onSubmit={submit}>
            {setup && (
              <>
                <Field label="Company name">
                  <input
                    name="company"
                    required
                    minLength={2}
                    maxLength={120}
                    placeholder="Your company"
                  />
                </Field>
                <Field label="Office / site">
                  <input
                    name="site"
                    required
                    minLength={2}
                    maxLength={120}
                    placeholder="Dhaka office"
                  />
                </Field>
                <Field label="Your name">
                  <input
                    name="name"
                    required
                    minLength={2}
                    autoComplete="name"
                  />
                </Field>
              </>
            )}
            <Field label="Work email">
              <input
                name="email"
                type="email"
                required
                autoComplete="username"
              />
            </Field>
            <Field
              label="Password"
              hint={setup ? "Use at least 12 characters." : undefined}
            >
              <input
                name="password"
                type="password"
                required
                minLength={setup ? 12 : 1}
                maxLength={128}
                autoComplete={setup ? "new-password" : "current-password"}
              />
            </Field>
            {error && (
              <p className="form-error" role="alert">
                {error}
              </p>
            )}
            <button className="button primary full" disabled={busy}>
              {busy ? "Please wait…" : setup ? "Create workspace" : "Sign in"}
              <ArrowRight size={17} />
            </button>
          </form>
          <p className="fineprint">
            {setup
              ? "Initial setup is restricted to the local machine. Cloud production provisioning is not available in this build."
              : "No remote live video. Your site keeps its biometric profiles locally."}
          </p>
        </div>
      </section>
    </main>
  );
}

export default function App() {
  const [signingOut, setSigningOut] = useState(false);
  const [correction, setCorrection] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null),
    [setup, setSetup] = useState(false),
    [ready, setReady] = useState(false);
  const [siteId, setSiteId] = useState(""),
    [day, setDay] = useState(""),
    [view, setView] = useState<View>("Overview");
  const [data, setData] = useState<Data>(emptyData),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [toast, setToast] = useState("");
  const [modal, setModal] = useState<"employee" | "agent" | null>(null),
    [review, setReview] = useState<Attendance | null>(null),
    [busy, setBusy] = useState(false);
  const [agentToken, setAgentToken] = useState("");
  const requestNumber = useRef(0);
  async function initialize() {
    try {
      const state = await api<{ required: boolean }>("/setup");
      setSetup(state.required);
      if (!state.required) {
        try {
          const w = await api<Workspace>("/me");
          setWorkspace(w);
          setSiteId(w.sites[0]?.id ?? "");
          setDay(w.default_date);
        } catch {
          setWorkspace(null);
        }
      }
      setError("");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setReady(true);
    }
  }
  useEffect(() => {
    void initialize();
  }, []);
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(""), 5000);
    return () => clearTimeout(id);
  }, [toast]);
  async function load() {
    if (!siteId || !day || !workspace) return;
    const number = ++requestNumber.current;
    setLoading(true);
    setError("");
    const q = `site_id=${encodeURIComponent(siteId)}`,
      dq = `${q}&day=${day}`;
    try {
      const [attendance, employees, cameras, events, runs, audit, agents] =
        await Promise.all([
          api<{ rows: Attendance[]; unknown_events: number }>(
            `/attendance?${dq}`,
          ),
          api<Employee[]>(`/employees?${q}`),
          api<Camera[]>(`/cameras?${q}`),
          api<Event[]>(`/events?${dq}`),
          ["admin", "payroll"].includes(workspace.user.role)
            ? api<Run[]>(`/payroll/runs?${q}`)
            : Promise.resolve([]),
          workspace.user.role === "admin"
            ? api<Audit[]>("/audit")
            : Promise.resolve([]),
          workspace.user.role === "admin"
            ? api<Agent[]>(`/agents?${q}`)
            : Promise.resolve([]),
        ]);
      if (number === requestNumber.current)
        setData({
          rows: attendance.rows,
          unknown: attendance.unknown_events,
          employees,
          cameras,
          events,
          runs,
          audit,
          agents,
        });
    } catch (err) {
      if (number === requestNumber.current) setError((err as Error).message);
    } finally {
      if (number === requestNumber.current) setLoading(false);
    }
  }
  useEffect(() => {
    void load();
  }, [siteId, day, workspace]);
  useEffect(() => {
    if (!workspace || !siteId || !day || modal || review || busy) return;
    const timer = setInterval(() => {
      if (!document.hidden) void load();
    }, 10000);
    return () => clearInterval(timer);
  }, [siteId, day, workspace, modal, review, busy]);
  async function mutate(action: () => Promise<unknown>, message: string) {
    setBusy(true);
    try {
      await action();
      setModal(null);
      setReview(null);
      setToast(message);
      await load();
      return true;
    } catch (err) {
      setError((err as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }
  if (!ready)
    return (
      <main className="loading-page">
        <BrandMark size={35} />
        <p>Opening your workspace…</p>
      </main>
    );
  if (!workspace)
    return (
      <>
        {error && (
          <div className="connection-error" role="alert">
            {error} · Start the API on port 8000, then{" "}
            <button onClick={initialize}>retry</button>.
          </div>
        )}
        <Access setup={setup} done={initialize} />
      </>
    );
  const site = workspace.sites.find((s) => s.id === siteId)!;
  const pending = data.rows.filter((r) => r.status === "needs_review");
  const exportUrl = `/api/v1/reports/attendance.csv?site_id=${encodeURIComponent(siteId)}&day=${day}`;
  const canManage = workspace.user.role === "admin";
  const reviewable = ["admin", "hr"].includes(workspace.user.role);
  async function signOut() {
    setSigningOut(true);
    try {
      await post("/auth/logout", {});
      ++requestNumber.current;
      setModal(null);
      setReview(null);
      setCorrection(null);
      setAgentToken("");
      setToast("");
      setError("");
      setView("Overview");
      setWorkspace(null);
      setData(emptyData);
    } catch (err) {
      setError(
        `Could not log out: ${(err as Error).message}. Please try again.`,
      );
    } finally {
      setSigningOut(false);
    }
  }
  async function submitRecord(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const f = new FormData(event.currentTarget);
    if (modal === "employee")
      await mutate(
        () =>
          post("/employees", {
            site_id: siteId,
            code: f.get("code"),
            name: f.get("name"),
            department: f.get("department"),
            designation: f.get("designation"),
            monthly_salary: f.get("salary"),
            overtime_rate: f.get("overtime"),
          }),
        "Employee added. Enroll their face in Cameras → Face enrollment.",
      );
    else {
      setBusy(true);
      try {
        const result = await post<{ token: string }>("/agents", {
          site_id: siteId,
          name: f.get("name"),
        });
        setAgentToken(result.token);
        setModal(null);
        await load();
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setBusy(false);
      }
    }
  }
  async function submitReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!review) return;
    const f = new FormData(event.currentTarget);
    await mutate(
      () =>
        post("/attendance/decisions", {
          employee_id: review.employee.id,
          business_date: day,
          outcome: f.get("outcome"),
          paid_leave: f.get("outcome") === "leave" && f.get("paid") === "on",
          overtime_minutes: Number(f.get("overtime") || 0),
          holiday_minutes: Number(f.get("holiday_minutes") || 0),
          reason: f.get("reason"),
        }),
      "Review decision saved with an audit trail.",
    );
  }
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" onClick={() => setView("Overview")}>
          <span>
            <BrandMark size={23} />
          </span>
          workledger<span className="brand-dot">.</span>
        </a>
        <div className="workspace-switch">
          <span className="company-icon">
            <Building2 size={19} />
          </span>
          <span>
            <strong>{workspace.company.name}</strong>
            <small>Company workspace</small>
          </span>
        </div>
        <span className="nav-caption">WORKSPACE</span>
        <nav>
          {navigation
            .filter(
              (n) =>
                n.name !== "Payroll" ||
                ["admin", "payroll"].includes(workspace.user.role),
            )
            .map(({ name, icon: Icon }) => (
              <button
                key={name}
                aria-label={name}
                title={name}
                className={view === name ? "nav-item active" : "nav-item"}
                onClick={() => setView(name)}
              >
                <Icon size={19} />
                <span>{name}</span>
                {name === "Review inbox" && pending.length > 0 && (
                  <b className="nav-count">{pending.length}</b>
                )}
              </button>
            ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="privacy-card">
            <ShieldCheck size={19} />
            <strong>Your video stays local.</strong>
            <p>Only attendance events leave your network.</p>
          </div>
          <button
            className="nav-item"
            onClick={() => {
              setView("Settings");
              setToast(
                "This is a development build. See README.md for supported features and limitations.",
              );
            }}
          >
            <CircleHelp size={18} />
            Help & build status
            <ArrowUpRight size={14} />
          </button>
          <div className="profile">
            <Avatar name={workspace.user.name} small />
            <span>
              <strong>{workspace.user.name}</strong>
              <small>{workspace.user.role} account</small>
            </span>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            Workspace <span>/</span>
            <strong>{view}</strong>
          </div>
          <div className="topbar-right">
            <span className="dev-chip">Development build</span>
            <span className="privacy-note">
              <LockKeyhole size={13} /> Events-only cloud
            </span>
            <Avatar name={workspace.user.name} small />
            <button
              className="logout-button"
              onClick={signOut}
              disabled={signingOut}
            >
              <LogOut size={16} />
              {signingOut ? "Logging out…" : "Log out"}
            </button>
          </div>
        </header>
        <main className="main-content">
          <div className="page-heading">
            <div>
              <div className="eyebrow">
                {view === "Overview"
                  ? "YOUR WORKDAY, AT A GLANCE"
                  : "WORKSPACE / " + view.toUpperCase()}
              </div>
              <h1>
                {view === "Overview"
                  ? "A little clarity for every workday."
                  : view}
              </h1>
              <p>
                {
                  (
                    {
                      Overview:
                        "Understand attendance, resolve exceptions, and keep work moving.",
                      Attendance:
                        "A transparent record of arrivals, exits, and time away.",
                      Employees: "The people behind every workday.",
                      Cameras:
                        "Connect local IP cameras, enroll faces, and inspect real inference.",
                      "Review inbox":
                        "Missing observations are questions to resolve, not automatic absences.",
                      Payroll:
                        "Calculate from reviewed attendance. Keep every amount explainable.",
                      "Office calendar":
                        "Paid holidays, office closures and approved holiday work rates.",
                      Settings:
                        "Your office, your policies. Every change leaves a trail.",
                    } as Record<View, string>
                  )[view]
                }
              </p>
            </div>
            <div className="heading-actions">
              {(view === "Overview" || view === "Attendance") && (
                <a className="button secondary" href={exportUrl}>
                  <Download size={16} />
                  Export CSV
                </a>
              )}
              {view === "Employees" && reviewable && (
                <button
                  className="button primary"
                  onClick={() => setModal("employee")}
                >
                  <Plus size={17} />
                  Add employee
                </button>
              )}
            </div>
          </div>
          <div className="toolbar">
            <div className="site-select">
              <Building2 size={16} />
              <select
                aria-label="Office site"
                value={siteId}
                onChange={(e) => {
                  setSiteId(e.target.value);
                  setData(emptyData);
                  setReview(null);
                }}
              >
                {workspace.sites.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>
            <span className="toolbar-divider" />
            <span className="timezone">{site.timezone}</span>
            <div className="toolbar-end">
              <label className="date-control">
                <CalendarDays size={15} />
                <input
                  aria-label="Business date"
                  type="date"
                  value={day}
                  onChange={(e) => {
                    if (e.target.value) {
                      setDay(e.target.value);
                      setData(emptyData);
                      setReview(null);
                    }
                  }}
                />
              </label>
              <button
                className={`icon-button ${loading ? "spinning" : ""}`}
                aria-label="Refresh data"
                onClick={load}
                disabled={loading}
              >
                <RefreshCw size={16} />
              </button>
            </div>
          </div>
          {error && (
            <div className="alert error" role="alert">
              <AlertCircle size={18} />
              <span>{error}</span>
              <button
                className="icon-button"
                aria-label="Dismiss error"
                onClick={() => setError("")}
              >
                <X size={16} />
              </button>
            </div>
          )}
          {view === "Overview" && (
            <Overview
              data={data}
              site={site}
              day={day}
              pending={pending.length}
              navigate={setView}
              review={setReview}
              reviewable={reviewable}
            />
          )}
          {view === "Attendance" && (
            <section className="panel">
              <div className="panel-heading">
                <div>
                  <h2>Daily attendance</h2>
                  <p>Confirmed intervals are separate from HR approval.</p>
                </div>
                <div className="zone-actions">
                  <Badge>{data.rows.length} employees</Badge>
                  {reviewable && (
                    <button
                      className="button secondary"
                      onClick={() => setCorrection("")}
                    >
                      Correct movements
                    </button>
                  )}
                </div>
              </div>
              <AttendanceTable
                rows={data.rows}
                zone={site.timezone}
                review={reviewable ? setReview : undefined}
              />
              {!data.rows.length && (
                <Empty
                  title="Your first workday starts here"
                  detail="Add employees, then connect a site agent to receive confirmed attendance events."
                  action={
                    <button
                      className="button secondary"
                      onClick={() => setView("Employees")}
                    >
                      Manage employees
                      <ArrowRight size={15} />
                    </button>
                  }
                />
              )}
            </section>
          )}
          {view === "Employees" && (
            <Team
              siteId={siteId}
              employees={data.employees}
              canManage={canManage}
              canEdit={reviewable}
              add={() => setModal("employee")}
              changed={load}
            />
          )}
          {view === "Office calendar" && (
            <OfficeCalendar
              site={site}
              day={day}
              canEdit={["admin", "hr", "payroll"].includes(workspace.user.role)}
              changed={initialize}
            />
          )}
          {view === "Cameras" && (
            <LocalVision
              siteId={siteId}
              day={day}
              employees={data.employees}
              canManage={canManage}
              canEnroll={reviewable}
              changed={load}
            />
          )}
          {view === "Review inbox" && (
            <ReviewInbox
              rows={pending}
              review={reviewable ? setReview : undefined}
              unknown={data.unknown}
            />
          )}
          {view === "Payroll" && (
            <PayrollWorkspace
              siteId={siteId}
              day={day}
              employees={data.employees}
              runs={data.runs}
              changed={load}
            />
          )}
          {view === "Settings" && (
            <SettingsView
              site={site}
              audit={data.audit}
              canManage={canManage}
              saved={initialize}
              mutate={mutate}
            />
          )}
          <footer className="page-footer">
            <span>
              <Leaf size={13} />
              Built for a more considered workplace.
            </span>
            <span>WorkLedger · Local AI 0.3</span>
          </footer>
        </main>
      </div>
      {toast && (
        <div className="toast" role="status">
          <CheckCircle2 size={18} />
          {toast}
          <button
            className="icon-button"
            aria-label="Dismiss notification"
            onClick={() => setToast("")}
          >
            <X size={15} />
          </button>
        </div>
      )}
      {modal === "employee" && (
        <EmployeeDialog
          siteId={siteId}
          canManage={canManage}
          close={() => setModal(null)}
          saved={load}
        />
      )}
      {modal === "agent" && (
        <Modal title="Provision a site agent" close={() => setModal(null)}>
          <form className="modal-form" onSubmit={submitRecord}>
            <Field label="Agent name">
              <input name="name" required minLength={2} />
            </Field>
            <button className="button primary" disabled={busy}>
              Provision agent
            </button>
          </form>
        </Modal>
      )}
      {correction !== null && (
        <MovementCorrection
          employees={data.employees}
          employeeId={correction}
          siteId={siteId}
          day={day}
          timezone={site.timezone}
          close={() => setCorrection(null)}
          changed={load}
        />
      )}
      {review && (
        <Modal title="Review attendance" close={() => setReview(null)}>
          <form className="modal-form" onSubmit={submitReview}>
            <div className="person-large">
              <Avatar name={review.employee.name} />
              <div>
                <strong>{review.employee.name}</strong>
                <small>
                  {review.employee.code} · {day}
                </small>
              </div>
            </div>
            <div className="review-facts">
              <span>
                First IN<strong>{clock(review.first_in, site.timezone)}</strong>
              </span>
              <span>
                Last OUT<strong>{clock(review.last_out, site.timezone)}</strong>
              </span>
              <span>
                Confirmed time<strong>{minutes(review.inside_minutes)}</strong>
              </span>
            </div>
            {review.issues.length > 0 && (
              <div className="note amber">
                <AlertCircle size={17} />
                {review.issues.join(" · ")}
              </div>
            )}
            <Field label="Decision">
              <select
                name="outcome"
                defaultValue={
                  review.complete
                    ? "present"
                    : review.first_in
                      ? "pending"
                      : review.decision?.outcome || "leave"
                }
              >
                {review.complete && (
                  <option value="present">Approve present</option>
                )}
                {!review.first_in && (
                  <>
                    <option value="leave">Approve leave</option>
                    <option value="absent">Approve absence</option>
                  </>
                )}
                {!review.complete && review.first_in && (
                  <option value="pending" disabled>
                    Movement correction required
                  </option>
                )}
              </select>
            </Field>
            {!review.first_in && (
              <label className="checkline">
                <input
                  name="paid"
                  type="checkbox"
                  defaultChecked={review.decision?.paid_leave || false}
                />
                <span>Paid leave (leave decisions only)</span>
              </label>
            )}
            {review.complete && (
              <Field
                label={
                  review.schedule_kind === "working_day"
                    ? "Approved overtime (minutes)"
                    : "Approved holiday / weekly-off work (minutes)"
                }
              >
                <input
                  name={
                    review.schedule_kind === "working_day"
                      ? "overtime"
                      : "holiday_minutes"
                  }
                  type="number"
                  min="0"
                  max="720"
                  defaultValue={
                    review.schedule_kind === "working_day"
                      ? review.decision?.overtime_minutes || 0
                      : review.decision?.holiday_minutes || 0
                  }
                />
              </Field>
            )}
            <Field label="Reason / evidence reference">
              <textarea
                name="reason"
                defaultValue={review.decision?.reason || ""}
                minLength={5}
                maxLength={1000}
                required
                placeholder="Explain this decision for the audit trail."
              />
            </Field>
            {review.first_in && !review.complete && (
              <p className="fineprint">
                This record cannot be approved until its missing or
                contradictory movement events are resolved. Use the movement
                correction action below after checking the evidence.
              </p>
            )}
            <button
              type="button"
              className="button secondary"
              onClick={() => {
                setCorrection(review.employee.id);
                setReview(null);
              }}
            >
              Correct movement events
            </button>
            <div className="modal-actions">
              <button
                className="button secondary"
                type="button"
                onClick={() => setReview(null)}
              >
                Cancel
              </button>
              <button
                className="button primary"
                disabled={busy || (!!review.first_in && !review.complete)}
              >
                Save decision
                <Check size={16} />
              </button>
            </div>
          </form>
        </Modal>
      )}
      {agentToken && (
        <Modal
          title="Save your agent credential"
          close={() => setAgentToken("")}
        >
          <div className="modal-form">
            <p>
              This credential is displayed once. Keep it on your site agent,
              never in frontend code or camera settings.
            </p>
            <textarea
              className="token"
              readOnly
              value={agentToken}
              aria-label="One-time agent credential"
            />
            <button
              className="button primary"
              onClick={() => setAgentToken("")}
            >
              I have saved it securely
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function AttendanceTable({
  rows,
  zone,
  review,
}: {
  rows: Attendance[];
  zone: string;
  review?: (row: Attendance) => void;
}) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Employee</th>
            <th>First in</th>
            <th>Last out</th>
            <th>Office time</th>
            <th>Time away</th>
            <th>Approval</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.employee.id}>
              <td>
                <div className="person">
                  <Avatar name={row.employee.name} small />
                  <span>
                    <strong>{row.employee.name}</strong>
                    <small>{row.employee.department}</small>
                  </span>
                </div>
              </td>
              <td className="time-cell">
                {clock(row.first_in, zone)}
                {row.late_minutes > 0 && (
                  <small className="late">
                    {row.late_minutes}m after grace
                  </small>
                )}
              </td>
              <td className="time-cell">{clock(row.last_out, zone)}</td>
              <td>
                {row.inside_minutes ? minutes(row.inside_minutes) : "—"}
                {row.currently_inside && (
                  <small className="muted block">
                    Final exit missing / open
                  </small>
                )}
              </td>
              <td>{row.away_minutes ? minutes(row.away_minutes) : "—"}</td>
              <td>
                <Badge
                  tone={
                    row.status === "present"
                      ? "green"
                      : row.status === "leave"
                        ? "blue"
                        : "amber"
                  }
                >
                  {row.approval_stale
                    ? "Changed · re-review"
                    : row.status === "needs_review"
                      ? "Needs review"
                      : row.status === "leave"
                        ? "Approved leave"
                        : row.status === "absent"
                          ? "Approved absent"
                          : "Approved present"}
                </Badge>
              </td>
              <td>
                {review && (
                  <button className="text-button" onClick={() => review(row)}>
                    Review
                    <ArrowRight size={14} />
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Overview({
  data,
  site,
  day,
  pending,
  navigate,
  review,
  reviewable,
}: {
  data: Data;
  site: Site;
  day: string;
  pending: number;
  navigate: (view: View) => void;
  review: (row: Attendance) => void;
  reviewable: boolean;
}) {
  const recorded = data.rows.filter((r) => r.first_in).length;
  const leave = data.rows.filter((r) => r.status === "leave").length;
  const hourCounts = Array.from(
    { length: 12 },
    (_, i) =>
      data.events.filter(
        (e) =>
          e.kind === "entry" &&
          Number(clock(e.occurred_at, site.timezone).split(":")[0]) === i + 7,
      ).length,
  );
  const peak = Math.max(1, ...hourCounts);
  return (
    <>
      <div className="stat-grid">
        {[
          {
            label: "Recorded attendance",
            value: recorded,
            sub: `of ${data.employees.length} employees have an IN`,
            icon: Users,
            tone: "green",
          },
          {
            label: "Needs your review",
            value: pending,
            sub: "Resolve before finalizing payroll",
            icon: ClipboardCheck,
            tone: "amber",
          },
          {
            label: "Approved leave",
            value: leave,
            sub: "Reviewed leave for this business day",
            icon: CalendarDays,
            tone: "blue",
          },
          {
            label: "Connected cameras",
            value: data.cameras.filter((c) => c.status === "online").length,
            sub: `${data.cameras.length} configured · local runtime`,
            icon: CameraIcon,
            tone: "neutral",
          },
        ].map((s) => (
          <article className={`stat-card ${s.tone}`} key={s.label}>
            <div>
              <span>{s.label}</span>
              <s.icon size={18} />
            </div>
            <strong>{s.value.toString().padStart(2, "0")}</strong>
            <p>{s.sub}</p>
          </article>
        ))}
      </div>
      <div className="overview-grid">
        <section className="panel activity-panel">
          <div className="panel-heading">
            <div>
              <h2>Arrivals through the day</h2>
              <p>
                Entry events · {day} · {site.timezone}
              </p>
            </div>
            <span className="chart-legend">
              <i />
              Recorded entries
            </span>
          </div>
          {data.events.length ? (
            <>
              <div className="bar-chart">
                {hourCounts.map((n, i) => (
                  <div className="bar-column" key={i}>
                    <span className="bar-value">{n || ""}</span>
                    <div
                      className={`chart-bar ${n === peak ? "peak" : ""}`}
                      style={{ height: `${Math.max(3, (n / peak) * 120)}px` }}
                      title={`${i + 7}:00 · ${n} entries`}
                    />
                    <span>{i + 7}</span>
                  </div>
                ))}
              </div>
              <div className="chart-footer">
                <span>
                  <Clock3 size={14} />
                  Based on recorded event timestamps
                </span>
                <button
                  className="text-button"
                  onClick={() => navigate("Attendance")}
                >
                  View attendance
                  <ArrowRight size={14} />
                </button>
              </div>
            </>
          ) : (
            <Empty
              title="No arrivals recorded"
              detail="Real entry events will appear after a camera agent is connected."
            />
          )}
        </section>
        <section className="panel review-preview">
          <div className="panel-heading">
            <div>
              <h2>A human check matters.</h2>
              <p>Keep exceptions visible and explainable.</p>
            </div>
            <span className="round-icon">
              <ClipboardCheck size={20} />
            </span>
          </div>
          <div className="review-number">
            {pending}
            <span>records awaiting review</span>
          </div>
          <p className="review-copy">
            Missing a camera observation never automatically means someone was
            absent.
          </p>
          <button
            className="button primary"
            onClick={() => navigate("Review inbox")}
          >
            Open review inbox
            <ArrowRight size={16} />
          </button>
          <div className="review-assurance">
            <ShieldCheck size={15} />
            Decisions are saved with an audit trail.
          </div>
        </section>
      </div>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>People & presence</h2>
            <p>The day's movement history and approval status.</p>
          </div>
          <button
            className="text-button"
            onClick={() => navigate("Attendance")}
          >
            See all employees
            <ArrowRight size={15} />
          </button>
        </div>
        {data.rows.length ? (
          <AttendanceTable
            rows={data.rows.slice(0, 5)}
            zone={site.timezone}
            review={reviewable ? review : undefined}
          />
        ) : (
          <Empty
            title="Meet your workspace"
            detail="Add your team to begin building attendance records."
          />
        )}
      </section>
      <div className="bottom-grid">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Latest movements</h2>
              <p>Entry and exit events for the selected day.</p>
            </div>
            <Activity size={18} className="muted" />
          </div>
          <div className="movement-list">
            {data.events.slice(0, 5).map((event) => (
              <div className="movement" key={event.id}>
                <span
                  className={`movement-icon ${event.kind === "entry" ? "" : "exit"}`}
                >
                  {event.kind === "entry" ? (
                    <ArrowDownLeft size={18} />
                  ) : (
                    <ArrowUpRight size={18} />
                  )}
                </span>
                <div>
                  <strong>
                    {data.employees.find((e) => e.id === event.employee_id)
                      ?.name ?? "Unidentified person"}
                  </strong>
                  <small>
                    {event.kind === "entry"
                      ? "Entered the office"
                      : "Left the office"}
                    {event.source === "simulated" ? " · Simulated" : ""}
                  </small>
                </div>
                <time>{clock(event.occurred_at, site.timezone)}</time>
              </div>
            ))}
            {!data.events.length && (
              <p className="padded muted">No movement events recorded.</p>
            )}
          </div>
        </section>
        <section className="panel edge-card">
          <div className="panel-heading">
            <div>
              <h2>Intelligence at the edge.</h2>
              <p>Light on hardware. Clear on privacy.</p>
            </div>
            <Server size={20} />
          </div>
          <div className="edge-facts">
            <span>
              Camera inference<strong>Local CPU runtime</strong>
            </span>
            <span>
              Biometric enrollment
              <strong>Local camera & photo enrollment</strong>
            </span>
            <span>
              Remote live video<strong>Disabled by design</strong>
            </span>
            <span>
              Internal sampling target<strong>2 FPS · configurable</strong>
            </span>
          </div>
          <button className="text-button" onClick={() => navigate("Cameras")}>
            View site configuration
            <ArrowRight size={14} />
          </button>
        </section>
      </div>
    </>
  );
}

function ReviewInbox({
  rows,
  review,
  unknown,
}: {
  rows: Attendance[];
  review?: (row: Attendance) => void;
  unknown: number;
}) {
  return (
    <>
      {unknown > 0 && (
        <div className="alert amber">
          <AlertCircle size={18} />
          {unknown} unidentified portal events require local evidence review.
          Review unmatched faces and visitors in Cameras → Identity review.
        </div>
      )}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Attendance exceptions</h2>
            <p>
              Confirm facts, check leave, and record the reason for every
              decision.
            </p>
          </div>
          <Badge tone="amber">{rows.length} pending</Badge>
        </div>
        {rows.map((row) => (
          <div className="review-row" key={row.employee.id}>
            <Avatar name={row.employee.name} />
            <div>
              <strong>{row.employee.name}</strong>
              <p>
                {row.approval_stale
                  ? "New events or a policy change invalidated the prior approval."
                  : row.issues.join(" · ") ||
                    "Complete movement history · awaiting HR approval"}
              </p>
              <small>
                {row.employee.code} · {row.employee.department}
              </small>
            </div>
            <Badge tone={row.complete ? "green" : "amber"}>
              {row.complete ? "Ready to review" : "Incomplete"}
            </Badge>
            {review && (
              <button className="button secondary" onClick={() => review(row)}>
                Review
                <ArrowRight size={15} />
              </button>
            )}
          </div>
        ))}
        {!rows.length && (
          <Empty
            title="A clear inbox. A clearer day."
            detail="No employee attendance records currently require review for this date."
          />
        )}
      </section>
    </>
  );
}

function SettingsView({
  site,
  audit,
  canManage,
  mutate,
  saved,
}: {
  site: Site;
  audit: Audit[];
  canManage: boolean;
  mutate: (action: () => Promise<unknown>, message: string) => Promise<boolean>;
  saved: () => Promise<void>;
}) {
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const f = new FormData(event.currentTarget);
    const ok = await mutate(
      () =>
        api(`/sites/${site.id}/policy`, {
          method: "PUT",
          body: JSON.stringify({
            timezone: f.get("timezone"),
            shift_start: f.get("start"),
            shift_end: f.get("end"),
            weekdays: f.getAll("weekday").map(Number),
            grace_minutes: Number(f.get("grace")),
            break_minutes: Number(f.get("break")),
            retention_days: Number(f.get("retention")),
          }),
        }),
      "Policy saved. Existing attendance decisions now require re-review.",
    );
    if (ok) await saved();
  }
  return (
    <div className="settings-grid">
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Office policy</h2>
            <p>
              Version {site.policy_version} · Changes invalidate prior
              attendance approvals.
            </p>
          </div>
          <SlidersHorizontal size={19} />
        </div>
        <form
          className="settings-form"
          key={site.policy_version}
          onSubmit={submit}
        >
          <Field label="Site timezone">
            <input
              name="timezone"
              defaultValue={site.timezone}
              required
              disabled={!canManage}
            />
          </Field>
          <div className="form-grid">
            <Field label="Shift start">
              <input
                name="start"
                type="time"
                defaultValue={site.shift_start}
                required
                disabled={!canManage}
              />
            </Field>
            <Field label="Shift end">
              <input
                name="end"
                type="time"
                defaultValue={site.shift_end}
                required
                disabled={!canManage}
              />
            </Field>
            <Field label="Arrival grace (minutes)">
              <input
                name="grace"
                type="number"
                min="0"
                max="120"
                defaultValue={site.grace_minutes}
                disabled={!canManage}
              />
            </Field>
            <Field label="Break policy (minutes)">
              <input
                name="break"
                type="number"
                min="0"
                max="240"
                defaultValue={site.break_minutes}
                disabled={!canManage}
              />
            </Field>
          </div>
          <p className="fineprint">
            Approved observed breaks are excluded up to this allowance from the
            workplace-time ratio. Payroll uses HR-approved attendance.
          </p>
          <Field label="Scheduled weekdays">
            <div className="weekday-grid">
              {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(
                (day, i) => (
                  <label key={day}>
                    <input
                      type="checkbox"
                      name="weekday"
                      value={i}
                      defaultChecked={site.weekdays.includes(i)}
                      disabled={!canManage}
                    />
                    <span>{day}</span>
                  </label>
                ),
              )}
            </div>
          </Field>
          <Field label="Attendance retention preference">
            <select
              name="retention"
              defaultValue={site.retention_days}
              disabled={!canManage}
            >
              <option value="90">90 days</option>
              <option value="365">365 days</option>
            </select>
          </Field>
          <p className="fineprint">
            Local evidence and workplace observations follow this period. Daily
            appearance profiles expire at midnight. Business and payroll history
            are retained separately.
          </p>
          {canManage && (
            <button className="button primary" type="submit">
              Save policy
              <Check size={16} />
            </button>
          )}
        </form>
      </section>
      <div>
        <section className="panel privacy-settings">
          <ShieldCheck size={26} />
          <h2>Privacy is a boundary.</h2>
          <p>
            No remote video endpoints. No face images or embeddings in the
            event-ingestion schema.
          </p>
          <div className="edge-facts">
            <span>
              Video & biometrics<strong>Local only</strong>
            </span>
            <span>
              Camera inference<strong>Local CPU runtime</strong>
            </span>
            <span>
              Cloud/site sync<strong>Journal prototype</strong>
            </span>
            <span>
              Production deployment<strong>Not ready</strong>
            </span>
          </div>
        </section>
        {canManage && <UserAccess />}
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Recent audit trail</h2>
              <p>
                {canManage
                  ? "Administrative changes in this company."
                  : "Administrator access required."}
              </p>
            </div>
          </div>
          {audit.slice(0, 6).map((a) => (
            <div className="audit-row" key={a.id}>
              <span className="audit-dot" />
              <div>
                <strong>{a.action.replaceAll(".", " / ")}</strong>
                <small>{new Date(a.created_at).toLocaleString()}</small>
              </div>
            </div>
          ))}
          {!audit.length && (
            <p className="padded muted">No audit entries available.</p>
          )}
        </section>
      </div>
    </div>
  );
}
