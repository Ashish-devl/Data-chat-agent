import { type FormEvent, useRef, useState } from "react";

import { type AskResponse, DataChatClient } from "datachat-agent";

import { apiBase, getToken } from "../api";
import { ErrorNote } from "../ui";

interface Turn {
  question: string;
  answer: string;
  done?: AskResponse;
  sql?: string;
  rows?: Record<string, unknown>[];
  columns?: string[];
  error?: string;
}

export function TryIt() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const session = useRef(`console-${Date.now().toString(36)}`);

  async function ask(e: FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q || busy) return;
    setQuestion("");
    setBusy(true);
    const index = turns.length;
    setTurns((t) => [...t, { question: q, answer: "" }]);
    const update = (change: Partial<Turn> | ((t: Turn) => Partial<Turn>)) =>
      setTurns((all) => all.map((t, i) => (i === index
        ? { ...t, ...(typeof change === "function" ? change(t) : change) } : t)));
    try {
      const client = new DataChatClient({ apiKey: getToken() ?? "", baseUrl: apiBase() });
      for await (const ev of client.askStream(q, { sessionId: session.current })) {
        if (ev.type === "token") update((t) => ({ answer: t.answer + ev.text }));
        if (ev.type === "data") update({ sql: ev.sql, rows: ev.rows, columns: ev.columns });
        if (ev.type === "done") update({ done: ev.response, answer: ev.response.answer });
        if (ev.type === "error") update({ error: ev.message });
      }
    } catch (err) {
      update({ error: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>Try it</h1>
          <p className="muted">Ask the way your users will. Follow-up questions remember this conversation.</p>
        </div>
        {turns.length > 0 && (
          <button type="button" className="secondary" onClick={() => {
            setTurns([]);
            session.current = `console-${Date.now().toString(36)}`;
          }}>New conversation</button>
        )}
      </header>
      <div className="chat" aria-live="polite">
        {turns.map((t, i) => (
          <div key={i} className="turn">
            <p className="question">{t.question}</p>
            <div className="answer">
              {t.answer || (!t.error && "Thinking…")}
              <ErrorNote error={t.error ?? (t.done?.error && !t.done.answer ? t.done.error : null)} />
              {t.rows && t.rows.length > 0 && t.columns && (
                <table className="nested">
                  <thead><tr>{t.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                  <tbody>
                    {t.rows.slice(0, 10).map((r, j) => (
                      <tr key={j}>{t.columns!.map((c) => <td key={c}>{String(r[c] ?? "")}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              )}
              {t.done && t.done.citations.length > 0 && (
                <p className="muted small">
                  Sources: {t.done.citations.map((c) => `[${c.ref}] ${c.filename}, page ${c.page}`).join("; ")}
                </p>
              )}
              {t.sql && (
                <details><summary>SQL</summary><pre>{t.sql}</pre></details>
              )}
              {t.done && (
                <p className="muted small">
                  {t.done.route} · {(t.done.latency_ms / 1000).toFixed(1)} s · {t.done.tokens.toLocaleString()} tokens
                </p>
              )}
            </div>
          </div>
        ))}
      </div>
      <form className="ask" onSubmit={ask}>
        <label className="sr" htmlFor="try-q">Question</label>
        <input id="try-q" value={question} maxLength={2000} placeholder="Which production orders are delayed?"
               onChange={(e) => setQuestion(e.target.value)} />
        <button type="submit" disabled={busy || !question.trim()}>{busy ? "Answering…" : "Ask"}</button>
      </form>
    </section>
  );
}
