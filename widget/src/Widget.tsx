import { useEffect, useRef, useState } from "preact/hooks";

import { type AskResponse, type Citation, DataChatClient, DataChatError } from "datachat-agent";

export interface WidgetConfig {
  apiKey: string;
  baseUrl: string;
  prefix?: string;
  title?: string;
  placeholder?: string;
  connectionId?: string;
  position?: "right" | "left";
  accent?: string;
}

interface Message {
  id: number;
  role: "user" | "bot";
  text: string;
  pending?: boolean;
  error?: boolean;
  queryId?: string;
  citations?: Citation[];
  columns?: string[];
  rows?: Record<string, unknown>[];
  rowCount?: number;
  rating?: "helpful" | "wrong";
}

const TABLE_ROWS = 5;

function newSessionId(): string {
  try {
    const saved = sessionStorage.getItem("datachat-widget-session");
    if (saved) return saved;
    const id = crypto.randomUUID();
    sessionStorage.setItem("datachat-widget-session", id);
    return id;
  } catch {
    return Math.random().toString(36).slice(2);
  }
}

function errorText(e: unknown): string {
  if (e instanceof DataChatError) {
    if (e.status === 429) return "Too many questions right now. Please wait a moment.";
    if (e.status === 401 || e.status === 403) return "This chat is not set up for this website.";
    return e.message;
  }
  return "Could not reach the server. Please try again.";
}

export function Widget({ config }: { config: WidgetConfig }) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const client = useRef(new DataChatClient({
    apiKey: config.apiKey, baseUrl: config.baseUrl, prefix: config.prefix,
  }));
  const session = useRef(newSessionId());
  const nextId = useRef(1);
  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [messages]);
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const update = (id: number, change: Partial<Message> | ((m: Message) => Partial<Message>)) =>
    setMessages((all) => all.map((m) => m.id === id ? { ...m, ...(typeof change === "function"
      ? change(m) : change) } : m));

  async function send(e?: Event) {
    e?.preventDefault();
    const question = input.trim();
    if (!question || busy) return;
    setInput("");
    setBusy(true);
    const botId = nextId.current + 1;
    nextId.current += 2;
    setMessages((all) => [
      ...all,
      { id: botId - 1, role: "user", text: question },
      { id: botId, role: "bot", text: "", pending: true },
    ]);
    try {
      let done: AskResponse | undefined;
      for await (const ev of client.current.askStream(question, {
        sessionId: session.current, connectionId: config.connectionId,
      })) {
        if (ev.type === "token") update(botId, (m) => ({ text: m.text + ev.text }));
        else if (ev.type === "data") {
          update(botId, { columns: ev.columns, rows: ev.rows, rowCount: ev.row_count });
        } else if (ev.type === "error") update(botId, { error: true, text: ev.message });
        else if (ev.type === "done") done = ev.response;
      }
      if (done) {
        update(botId, {
          text: done.answer, pending: false, queryId: done.query_id, citations: done.citations,
          error: Boolean(done.error && !done.answer),
        });
      } else update(botId, { pending: false });
    } catch (err) {
      update(botId, { text: errorText(err), error: true, pending: false });
    } finally {
      setBusy(false);
    }
  }

  async function rate(m: Message, rating: "helpful" | "wrong") {
    if (!m.queryId) return;
    update(m.id, { rating });
    try {
      await client.current.feedback(m.queryId, rating);
    } catch {
      update(m.id, { rating: undefined });
    }
  }

  const title = config.title ?? "Ask about our data";
  return (
    <div class={`root ${config.position ?? "right"}`}
         style={config.accent ? `--accent:${config.accent}` : undefined}>
      {open && (
        <section class="panel" role="dialog" aria-label={title}>
          <div class="head">
            <span>{title}</span>
            <button type="button" aria-label="Close chat" onClick={() => setOpen(false)}>×</button>
          </div>
          <div class="log" ref={logRef} aria-live="polite">
            {messages.length === 0 && (
              <p class="empty">Ask a question about our records or documents.</p>
            )}
            {messages.map((m) => (
              <div key={m.id} class={`msg ${m.role}${m.error ? " error" : ""}`}>
                {m.text || (m.pending ? "Thinking…" : "")}
                {m.role === "bot" && m.rows && m.rows.length > 0 && m.columns && (
                  <table>
                    <thead><tr>{m.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                    <tbody>
                      {m.rows.slice(0, TABLE_ROWS).map((r, i) => (
                        <tr key={i}>{m.columns!.map((c) => <td key={c}>{String(r[c] ?? "")}</td>)}</tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {m.role === "bot" && (m.rowCount ?? 0) > TABLE_ROWS && (
                  <div class="meta">Showing {TABLE_ROWS} of {m.rowCount} rows</div>
                )}
                {m.citations && m.citations.length > 0 && (
                  <div class="sources">
                    Sources: {m.citations.map((c) => `[${c.ref}] ${c.filename}, page ${c.page}`)
                      .join("; ")}
                  </div>
                )}
                {m.role === "bot" && m.queryId && !m.pending && (
                  <div class="rate">
                    <button type="button" aria-pressed={m.rating === "helpful"}
                            onClick={() => rate(m, "helpful")}>Helpful</button>
                    <button type="button" aria-pressed={m.rating === "wrong"}
                            onClick={() => rate(m, "wrong")}>Wrong</button>
                  </div>
                )}
              </div>
            ))}
          </div>
          <form onSubmit={send}>
            <label class="sr" for="datachat-q">Your question</label>
            <textarea id="datachat-q" ref={inputRef} value={input} maxLength={2000}
                      placeholder={config.placeholder ?? "Type your question"}
                      onInput={(e) => setInput((e.target as HTMLTextAreaElement).value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) send(e);
                      }} />
            <button type="submit" disabled={busy || !input.trim()}>Ask</button>
          </form>
        </section>
      )}
      <button type="button" class="launcher" aria-label={open ? "Close chat" : title}
              aria-expanded={open} onClick={() => setOpen(!open)}>
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M4 5h16v11H8l-4 4V5z" stroke="currentColor" stroke-width="2"
                stroke-linejoin="round" />
        </svg>
      </button>
    </div>
  );
}
