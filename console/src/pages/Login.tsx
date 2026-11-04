import { type FormEvent, useState } from "react";

import { type Me, post, setToken } from "../api";
import { ErrorNote, useAction } from "../ui";

export function Login({ onSignedIn }: { onSignedIn: (me: Me) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const { busy, error, run } = useAction();

  async function submit(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      const r = await post<{ token: string; user: Me }>("/auth/login", { email, password });
      setToken(r.token);
      onSignedIn(r.user);
    });
  }

  return (
    <main className="login">
      <form className="card" onSubmit={submit} aria-labelledby="login-title">
        <h1 id="login-title">DataChat Agent</h1>
        <p className="muted">Sign in to manage your data sources and see what people ask.</p>
        <label>
          Email
          <input type="email" autoComplete="username" required value={email}
                 onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          Password
          <input type="password" autoComplete="current-password" required value={password}
                 onChange={(e) => setPassword(e.target.value)} />
        </label>
        <ErrorNote error={error} />
        <button type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        <p className="muted small">
          Forgot your password? Ask whoever runs this server to run{" "}
          <code>datachat-agent reset-password your@email</code>.
        </p>
      </form>
    </main>
  );
}
