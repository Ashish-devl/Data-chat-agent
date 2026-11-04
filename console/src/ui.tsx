import { type ReactNode, useCallback, useEffect, useState } from "react";

import { ApiError } from "./api";

/** Load data, with loading and error states and a way to reload. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await load());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    reload();
  }, [reload]);
  return { data, error, loading, reload, setData };
}

/** Run an action, showing its error; returns true when it worked. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      return true;
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
  }, []);
  return { busy, error, run, setError };
}

export function Page({ title, intro, actions, children }: {
  title: string; intro?: ReactNode; actions?: ReactNode; children: ReactNode;
}) {
  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>{title}</h1>
          {intro && <p className="muted">{intro}</p>}
        </div>
        {actions && <div className="actions">{actions}</div>}
      </header>
      {children}
    </section>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? <p className="error" role="alert">{error}</p> : null;
}

export function Loading({ show }: { show: boolean }) {
  return show ? <p className="muted">Loading…</p> : null;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function Badge({ tone, children }: { tone: "ok" | "warn" | "bad" | "plain"; children: ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export function statusTone(status: string): "ok" | "warn" | "bad" | "plain" {
  if (["ok", "ready", "helpful"].includes(status)) return "ok";
  if (["rejected", "failed", "error", "wrong"].includes(status)) return "bad";
  if (["queued", "processing", "new"].includes(status)) return "warn";
  return "plain";
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "–";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** Shows a secret once, with a copy button. */
export function OneTimeSecret({ label, value, onDone }: {
  label: string; value: string; onDone: () => void;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="notice" role="status">
      <p><strong>{label}</strong> — copy it now; it will not be shown again.</p>
      <code className="secret">{value}</code>
      <div className="actions">
        <button type="button" onClick={async () => {
          try {
            await navigator.clipboard.writeText(value);
            setCopied(true);
          } catch {
            setCopied(false);
          }
        }}>{copied ? "Copied" : "Copy"}</button>
        <button type="button" className="secondary" onClick={onDone}>Done</button>
      </div>
    </div>
  );
}
