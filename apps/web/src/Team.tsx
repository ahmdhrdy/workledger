import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, currency, initials } from "./api";
import type { Employee } from "./api";
import { Plus, Search, X, ScanFace } from "lucide-react";

export function EmployeeDialog({
  siteId,
  employee,
  canManage,
  close,
  saved,
}: {
  siteId: string;
  employee?: Employee;
  canManage: boolean;
  close: () => void;
  saved: () => Promise<void>;
}) {
  const ref = useRef<HTMLDialogElement>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [face, setFace] = useState<Blob | null>(null),
    [preview, setPreview] = useState(""),
    [cameras, setCameras] = useState<{ id: string; name: string }[]>([]),
    [cameraId, setCameraId] = useState("");
  useEffect(() => {
    ref.current?.showModal();
    void api<{ id: string; name: string; state: string }[]>(
      `/local/cameras?site_id=${siteId}`,
    )
      .then((items) => setCameras(items.filter((c) => c.state === "online")))
      .catch((e) => setError(e.message));
    return () => ref.current?.close();
  }, [siteId]);
  useEffect(() => {
    if (!face) {
      setPreview("");
      return;
    }
    const url = URL.createObjectURL(face);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [face]);
  async function capture() {
    try {
      const response = await fetch(
        `/api/v1/local/cameras/${cameraId}/frame?raw=true`,
        { cache: "no-store" },
      );
      if (!response.ok)
        throw new Error("No recent frame. Connect the camera first.");
      setFace(await response.blob());
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    if (!employee && !face) {
      setError(
        "Choose a face photo or capture a single clear face from an online camera.",
      );
      return;
    }
    setBusy(true);
    setError("");
    const profile = {
      site_id: siteId,
      code: data.get("code"),
      name: data.get("name"),
      nickname: data.get("nickname"),
      department: data.get("department"),
      designation: data.get("designation"),
      joined_on: data.get("joined_on") || null,
      ...(canManage
        ? {
            monthly_salary: data.get("salary"),
            overtime_rate: data.get("overtime"),
          }
        : employee
          ? {}
          : { monthly_salary: "0", overtime_rate: "0" }),
    };
    try {
      if (employee) {
        await api(`/employees/${employee.id}`, {
          method: "PUT",
          body: JSON.stringify({
            ...profile,
            active: data.get("active") === "on",
            left_on: data.get("left_on") || null,
          }),
        });
        if (face) {
          const form = new FormData();
          form.set("image", face, "face.jpg");
          const r = await fetch(
            `/api/v1/local/employees/${employee.id}/enroll`,
            {
              method: "POST",
              headers: { "X-Workledger-Request": "1" },
              body: form,
            },
          );
          const result = await r.json();
          if (!r.ok)
            throw new Error(
              `Profile saved; face sample failed: ${result.detail}`,
            );
        }
      } else {
        const form = new FormData();
        form.set("profile", JSON.stringify(profile));
        form.set("image", face!, "face.jpg");
        const response = await fetch("/api/v1/local/employees/register", {
          method: "POST",
          credentials: "same-origin",
          headers: { "X-Workledger-Request": "1" },
          body: form,
        });
        const result = await response.json();
        if (!response.ok)
          throw new Error(
            typeof result.detail === "string"
              ? result.detail
              : "Check the employee details and face photo.",
          );
      }
      await saved();
      close();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <dialog ref={ref} className="vision-dialog" onCancel={close}>
      <div className="modal-heading">
        <h2>{employee ? "Edit employee" : "Enroll an employee"}</h2>
        <button
          className="icon-button"
          onClick={close}
          aria-label="Close employee dialog"
        >
          <X size={20} />
        </button>
      </div>
      <form className="modal-form" onSubmit={submit}>
        <div className="form-grid">
          <label className="field">
            <span>Full name</span>
            <input
              name="name"
              defaultValue={employee?.name}
              minLength={2}
              maxLength={100}
              required
            />
          </label>
          <label className="field">
            <span>Employee ID</span>
            <input
              name="code"
              defaultValue={employee?.code}
              pattern="[A-Za-z0-9_-]+"
              maxLength={32}
              required
            />
          </label>
          <label className="field">
            <span>Nickname on camera</span>
            <input
              name="nickname"
              defaultValue={employee?.nickname}
              maxLength={100}
              placeholder="Uses full name when blank"
            />
          </label>
          <label className="field">
            <span>Joining date</span>
            <input
              type="date"
              name="joined_on"
              defaultValue={employee?.joined_on || ""}
            />
          </label>
          <label className="field">
            <span>Designation</span>
            <input
              name="designation"
              defaultValue={employee?.designation || "Employee"}
              maxLength={80}
              required
            />
          </label>
          <label className="field">
            <span>Department</span>
            <input
              name="department"
              defaultValue={employee?.department || "Operations"}
              maxLength={80}
              required
            />
          </label>
          {canManage && (
            <>
              <label className="field">
                <span>Monthly salary (BDT)</span>
                <input
                  type="number"
                  min={0}
                  max={10000000}
                  step=".01"
                  name="salary"
                  defaultValue={employee?.monthly_salary || "0"}
                  required
                />
              </label>
              <label className="field">
                <span>Overtime rate / hour (BDT)</span>
                <input
                  type="number"
                  min={0}
                  max={100000}
                  step=".01"
                  name="overtime"
                  defaultValue={employee?.overtime_rate || "0"}
                  required
                />
              </label>
            </>
          )}
          {employee && (
            <>
              <label className="field">
                <span>Last employment date</span>
                <input
                  name="left_on"
                  type="date"
                  defaultValue={employee.left_on || ""}
                />
              </label>
              <label className="checkline">
                <input
                  name="active"
                  type="checkbox"
                  defaultChecked={employee.active}
                />
                Active employee
              </label>
            </>
          )}
        </div>
        <div className="face-picker">
          <label className="field">
            <span>
              {employee
                ? "Add a face sample (optional)"
                : "Face photo (required)"}
            </span>
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              onChange={(e) => setFace(e.target.files?.[0] || null)}
            />
          </label>
          {cameras.length > 0 && (
            <div className="form-grid">
              <label className="field">
                <span>Or capture from a camera</span>
                <select
                  value={cameraId}
                  onChange={(e) => setCameraId(e.target.value)}
                >
                  <option value="">Choose online camera</option>
                  {cameras.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="button secondary"
                type="button"
                disabled={!cameraId}
                onClick={() => void capture()}
              >
                Capture current frame
              </button>
            </div>
          )}
          {preview && (
            <img
              className="enrollment-photo"
              src={preview}
              alt="Selected enrollment face"
            />
          )}
          <p className="fineprint">
            Use one clear face. The photo is processed locally; encrypted face
            features are retained. Add more angles and masked samples in Cameras
            → Face enrollment.
          </p>
        </div>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button className="button secondary" type="button" onClick={close}>
            Cancel
          </button>
          <button className="button primary" disabled={busy}>
            <ScanFace size={17} />
            {busy
              ? "Processing…"
              : employee
                ? "Save employee"
                : "Enroll & save employee"}
          </button>
        </div>
      </form>
    </dialog>
  );
}

export default function Team({
  siteId,
  employees,
  canManage,
  canEdit,
  add,
  changed,
}: {
  siteId: string;
  employees: Employee[];
  canManage: boolean;
  canEdit: boolean;
  add: () => void;
  changed: () => Promise<void>;
}) {
  const [search, setSearch] = useState(""),
    [edit, setEdit] = useState<Employee | null>(null);
  const rows = employees.filter((e) =>
    `${e.name} ${e.nickname} ${e.code} ${e.department}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>
            Employee directory{" "}
            <span className="inline-count">{employees.length}</span>
          </h2>
          <p>
            Enroll your own team. Nicknames appear on local live camera views.
          </p>
        </div>
        <label className="search">
          <Search size={16} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search employees"
            aria-label="Search employees"
          />
        </label>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Employee</th>
              <th>ID / Nickname</th>
              <th>Designation</th>
              <th>Enrollment</th>
              {canManage && <th>Monthly salary</th>}
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.id}>
                <td>
                  <div className="person">
                    <span className="avatar">{initials(e.name)}</span>
                    <div>
                      <strong>{e.name}</strong>
                      <small className="muted block">{e.department}</small>
                    </div>
                  </div>
                </td>
                <td>
                  {e.code}
                  <small className="muted block">{e.nickname || e.name}</small>
                </td>
                <td>{e.designation}</td>
                <td>
                  {e.enrollment_status === "enrolled"
                    ? "Enrolled locally"
                    : "Needs face enrollment"}
                </td>
                {canManage && <td>৳ {currency(e.monthly_salary || 0)}</td>}
                <td>
                  <span className={`badge ${e.active ? "green" : ""}`}>
                    {e.active ? "Active" : "Inactive"}
                  </span>
                </td>
                <td>
                  {canEdit && (
                    <button className="text-button" onClick={() => setEdit(e)}>
                      Edit
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows.length && (
        <div className="empty">
          <h3>
            {search ? "No matching employees" : "Enroll your first employee"}
          </h3>
          <p>
            Add their name, ID, face, designation, salary, and camera nickname.
          </p>
          {canEdit && (
            <button className="button primary" onClick={add}>
              <Plus size={16} />
              Enroll employee
            </button>
          )}
        </div>
      )}
      {edit && (
        <EmployeeDialog
          siteId={siteId}
          employee={edit}
          canManage={canManage}
          close={() => setEdit(null)}
          saved={changed}
        />
      )}
    </section>
  );
}
