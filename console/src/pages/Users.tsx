import { type FormEvent, useState } from "react";

import { api, del, type Me, patch, post } from "../api";
import { Badge, ErrorNote, Loading, OneTimeSecret, Page, useAction, useLoad, when } from "../ui";

interface User {
  id: string;
  email: string;
  name: string | null;
  role: "owner" | "member";
  disabled: boolean;
  last_login_at: string | null;
}

function newPassword(): string {
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(14));
  return `${Array.from(bytes, (b) => chars[b % chars.length]).join("")}-7aQ`; // always meets the rules
}

export function Users({ me }: { me: Me }) {
  const list = useLoad(() => api<User[]>("/users"));
  const action = useAction();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<"owner" | "member">("member");
  const [created, setCreated] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const password = newPassword();
    if (await action.run(() => post("/users", { email, name: name || null, role, password }))) {
      setCreated(`${email} — ${password}`);
      setEmail("");
      setName("");
      list.reload();
    }
  }

  const change = (u: User, body: object) =>
    action.run(async () => {
      await patch(`/users/${u.id}`, body);
      list.reload();
    });

  return (
    <Page title="Users" intro="Owners manage everything. Members can ask questions and manage documents.">
      {created && <OneTimeSecret label="Their sign-in details" value={created} onDone={() => setCreated(null)} />}
      <ErrorNote error={list.error ?? action.error} />
      <Loading show={list.loading && !list.data} />
      {list.data && (
        <table>
          <thead><tr><th>Person</th><th>Role</th><th>Last sign-in</th><th /></tr></thead>
          <tbody>
            {list.data.map((u) => (
              <tr key={u.id} className={u.disabled ? "hidden-item" : undefined}>
                <td>{u.name ?? u.email}<div className="muted small">{u.email}</div></td>
                <td>
                  <select aria-label={`Role of ${u.email}`} value={u.role}
                          onChange={(e) => change(u, { role: e.target.value })}>
                    <option value="member">Member</option>
                    <option value="owner">Owner</option>
                  </select>
                </td>
                <td>{when(u.last_login_at)}</td>
                <td className="actions">
                  {u.disabled && <Badge tone="bad">disabled</Badge>}
                  {u.id !== me.id && (
                    <>
                      <button type="button" className="link" onClick={() => change(u, { disabled: !u.disabled })}>
                        {u.disabled ? "Enable" : "Disable"}
                      </button>
                      <button type="button" className="link danger" onClick={async () => {
                        if (confirm(`Remove ${u.email}?`)) {
                          await action.run(() => del(`/users/${u.id}`));
                          list.reload();
                        }
                      }}>Remove</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form className="card" onSubmit={submit}>
        <h2>Add a person</h2>
        <label>Email<input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label>Name<input maxLength={100} value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label>Role
          <select value={role} onChange={(e) => setRole(e.target.value as "owner" | "member")}>
            <option value="member">Member</option>
            <option value="owner">Owner</option>
          </select>
        </label>
        <p className="muted small">A password is generated and shown once; they can change it after signing in.</p>
        <button type="submit" disabled={action.busy}>Add</button>
      </form>
    </Page>
  );
}

export function Account({ me }: { me: Me }) {
  const action = useAction();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [done, setDone] = useState(false);
  return (
    <Page title="Your account" intro={`${me.email} · ${me.role} at ${me.tenant_name}`}>
      <form className="card" onSubmit={async (e) => {
        e.preventDefault();
        setDone(false);
        if (await action.run(() => post("/auth/password", { current_password: current, new_password: next }))) {
          setCurrent("");
          setNext("");
          setDone(true);
        }
      }}>
        <h2>Change password</h2>
        <label>Current password<input type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} /></label>
        <label>New password<input type="password" autoComplete="new-password" required minLength={10} value={next} onChange={(e) => setNext(e.target.value)} /></label>
        <p className="muted small">At least 10 characters, with upper- and lower-case letters and a digit. Other devices are signed out.</p>
        <ErrorNote error={action.error} />
        {done && <p className="ok" role="status">Password changed.</p>}
        <button type="submit" disabled={action.busy}>Change password</button>
      </form>
    </Page>
  );
}
