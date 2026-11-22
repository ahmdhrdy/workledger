import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "./api";
type Account = { id: string; name: string; email: string; role: string };
export default function UserAccess() {
  const [items, setItems] = useState<Account[]>([]),
    [edit, setEdit] = useState<Account | null>(null),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  async function load() {
    setItems(await api<Account[]>("/users"));
  }
  useEffect(() => {
    void load().catch((e) => setError(e.message));
  }, []);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget,
      data = new FormData(form);
    setBusy(true);
    setError("");
    try {
      const result = await api<{ sign_in_again?: boolean }>(
        edit ? `/users/${edit.id}` : "/users",
        {
          method: edit ? "PUT" : "POST",
          body: JSON.stringify({
            name: data.get("name"),
            email: data.get("email"),
            role: data.get("role"),
            password: data.get("password") || null,
          }),
        },
      );
      if (result.sign_in_again) {
        location.reload();
        return;
      }
      setEdit(null);
      form.reset();
      await load();
      setNotice(
        "Account saved. Role or password changes revoke existing sessions.",
      );
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>Webapp user access</h2>
          <p>
            Separate logins for HR, payroll, administrators and read-only
            viewers.
          </p>
        </div>
      </div>
      {items.map((u) => (
        <div className="enrollment-row" key={u.id}>
          <div>
            <strong>{u.name}</strong>
            <small>
              {u.email} · {u.role}
            </small>
          </div>
          <button
            className="text-button"
            onClick={() => {
              setEdit(u);
              setNotice("");
            }}
          >
            Edit access
          </button>
        </div>
      ))}
      <form key={edit?.id || "new"} className="settings-form" onSubmit={submit}>
        <h3>{edit ? "Update account" : "Create a user"}</h3>
        <div className="form-grid">
          <label className="field">
            <span>Name</span>
            <input
              name="name"
              defaultValue={edit?.name}
              required
              minLength={2}
              maxLength={100}
            />
          </label>
          <label className="field">
            <span>Email / username</span>
            <input
              name="email"
              type="email"
              defaultValue={edit?.email}
              required
              maxLength={254}
            />
          </label>
          <label className="field">
            <span>Access role</span>
            <select name="role" defaultValue={edit?.role || "hr"}>
              <option value="admin">Administrator</option>
              <option value="hr">HR — review & enrollment</option>
              <option value="payroll">Payroll — salaries & payslips</option>
              <option value="viewer">Viewer — read only</option>
              <option value="disabled">Disabled — cannot sign in</option>
            </select>
          </label>
          <label className="field">
            <span>
              {edit ? "New password (blank keeps current)" : "Password"}
            </span>
            <input
              name="password"
              type="password"
              autoComplete="new-password"
              minLength={12}
              maxLength={128}
              required={!edit}
            />
          </label>
        </div>
        {error && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        {notice && (
          <p role="status" className="note">
            {notice}
          </p>
        )}
        <div className="zone-actions">
          <button className="button primary" disabled={busy}>
            {busy ? "Saving…" : edit ? "Save access" : "Create user"}
          </button>
          {edit && (
            <button
              className="button secondary"
              type="button"
              onClick={() => setEdit(null)}
            >
              Cancel edit
            </button>
          )}
        </div>
      </form>
    </section>
  );
}
