export type Site = {
  id: string;
  name: string;
  timezone: string;
  shift_start: string;
  shift_end: string;
  weekdays: number[];
  grace_minutes: number;
  break_minutes: number;
  retention_days: 90 | 365;
  policy_version: number;
  payroll_policy: {
    overtime_multiplier?: string;
    holiday_multiplier?: string;
    holiday_hourly_rate?: string;
  };
};
export type Workspace = {
  user: { name: string; email: string; role: string };
  company: { id: string; name: string; demo: boolean };
  sites: Site[];
  default_date: string;
};
export type Employee = {
  id: string;
  code: string;
  name: string;
  department: string;
  nickname: string;
  joined_on: string | null;
  left_on: string | null;
  designation: string;
  active: boolean;
  enrollment_status: string;
  monthly_salary?: string;
  overtime_rate?: string;
};
export type Attendance = {
  employee: Employee;
  business_date: string;
  first_in: string | null;
  last_out: string | null;
  inside_minutes: number;
  away_minutes: number;
  late_minutes: number;
  complete: boolean;
  currently_inside: boolean;
  issues: string[];
  revision: string;
  status: string;
  approval_stale: boolean;
  schedule_kind: string;
  decision: {
    reason: string;
    outcome: string;
    paid_leave: boolean;
    overtime_minutes: number;
    holiday_minutes: number;
  } | null;
};
export type Camera = {
  id: string;
  name: string;
  role: string;
  target_fps: number;
  status: string;
};
export type Event = {
  id: string;
  employee_id: string | null;
  camera_id: string;
  kind: string;
  occurred_at: string;
  identity_status: string;
  source: string;
};
export type Payroll = {
  month: string;
  items: {
    employee_id: string;
    name: string;
    code: string;
    paid_days: number;
    scheduled_days: number;
    unresolved_days: number;
    base_salary: string;
    earned_salary: string;
    overtime: string;
    holiday_pay: string;
    allowances: string;
    deductions: string;
    gross: string;
    paid_holidays: number;
    net: string;
  }[];
  blockers: { name: string; days: number; reason: string }[];
  total: string;
  can_finalize: boolean;
  input_revision: string;
  calculation_policy: string;
};
export type Run = {
  id: string;
  month: string;
  status: string;
  total: string;
  created_at: string;
};
export type Audit = {
  id: string;
  action: string;
  target: string;
  detail: Record<string, unknown>;
  created_at: string;
};
export type Agent = { id: string; name: string; last_seen: string | null };

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-Workledger-Request": "1",
      ...options.headers,
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((d: { msg: string }) => d.msg).join("; ")
      : data.detail;
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return data as T;
}
export const post = <T>(path: string, data: unknown) =>
  api<T>(path, { method: "POST", body: JSON.stringify(data) });
export const minutes = (n: number) => `${Math.floor(n / 60)}h ${n % 60}m`;
export const currency = (value: string | number) =>
  new Intl.NumberFormat("en-BD", { maximumFractionDigits: 2 }).format(
    Number(value),
  );
export const initials = (name: string) =>
  name
    .split(" ")
    .slice(0, 2)
    .map((x) => x[0])
    .join("");
export const clock = (value: string | null, zone = "Asia/Dhaka") =>
  value
    ? new Intl.DateTimeFormat("en-GB", {
        hour: "2-digit",
        minute: "2-digit",
        timeZone: zone,
      }).format(new Date(value))
    : "—";
