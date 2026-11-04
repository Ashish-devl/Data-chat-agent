import { type ReactNode, useEffect, useState } from "react";

import { api, type Me, post, setToken, getToken } from "./api";
import { ConnectionDetail, Connections } from "./pages/Connections";
import { Documents } from "./pages/Documents";
import { Keys } from "./pages/Keys";
import { Login } from "./pages/Login";
import { Overview } from "./pages/Overview";
import { QueryLog } from "./pages/QueryLog";
import { TryIt } from "./pages/TryIt";
import { Account, Users } from "./pages/Users";

interface NavItem { path: string; label: string; ownerOnly?: boolean }

const NAV: NavItem[] = [
  { path: "/", label: "Overview" },
  { path: "/connections", label: "Databases", ownerOnly: true },
  { path: "/documents", label: "Documents" },
  { path: "/try", label: "Try it" },
  { path: "/log", label: "Query log", ownerOnly: true },
  { path: "/keys", label: "API keys", ownerOnly: true },
  { path: "/users", label: "Users", ownerOnly: true },
];

function useHashPath(): [string, (p: string) => void] {
  const read = () => location.hash.replace(/^#/, "") || "/";
  const [path, setPath] = useState(read);
  useEffect(() => {
    const onChange = () => setPath(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return [path, (p: string) => { location.hash = p; }];
}

export function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [checking, setChecking] = useState(Boolean(getToken()));
  const [path, go] = useHashPath();

  useEffect(() => {
    if (!getToken()) return;
    api<Me>("/auth/me").then(setMe).catch(() => setToken(null)).finally(() => setChecking(false));
  }, []);
  useEffect(() => {
    const out = () => setMe(null);
    window.addEventListener("datachat-signed-out", out);
    return () => window.removeEventListener("datachat-signed-out", out);
  }, []);

  if (checking) return <p className="muted center">Loading…</p>;
  if (!me) return <Login onSignedIn={setMe} />;

  const owner = me.role === "owner";
  const nav = NAV.filter((n) => owner || !n.ownerOnly);
  const signOut = async () => {
    try {
      await post("/auth/logout");
    } finally {
      setToken(null);
      setMe(null);
    }
  };

  let page: ReactNode;
  const detail = path.match(/^\/connections\/([\w-]+)$/);
  if (detail && owner) page = <ConnectionDetail id={detail[1]} go={go} />;
  else if (path === "/connections" && owner) page = <Connections go={go} />;
  else if (path === "/documents") page = <Documents />;
  else if (path === "/try") page = <TryIt />;
  else if (path === "/log" && owner) page = <QueryLog />;
  else if (path === "/keys" && owner) page = <Keys />;
  else if (path === "/users" && owner) page = <Users me={me} />;
  else if (path === "/account") page = <Account me={me} />;
  else page = <Overview me={me} />;

  const active = (p: string) => (p === "/" ? path === "/" : path.startsWith(p));
  return (
    <div className="shell">
      <a className="skip" href="#main">Skip to content</a>
      <nav className="side" aria-label="Main">
        <div className="brand">DataChat <span>Agent</span></div>
        <ul>
          {nav.map((n) => (
            <li key={n.path}>
              <a href={`#${n.path}`} aria-current={active(n.path) ? "page" : undefined}>{n.label}</a>
            </li>
          ))}
        </ul>
        <div className="who">
          <a href="#/account">{me.name ?? me.email}</a>
          <span className="muted small">{me.role}</span>
          <button type="button" className="link" onClick={signOut}>Sign out</button>
        </div>
      </nav>
      <main id="main">{page}</main>
    </div>
  );
}
