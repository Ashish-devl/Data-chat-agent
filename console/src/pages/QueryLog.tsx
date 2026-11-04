import { Fragment, useState } from "react";

import { api } from "../api";
import { Badge, Empty, ErrorNote, Loading, Page, statusTone, useLoad, when } from "../ui";

interface Entry {
  id: string;
  question: string;
  route: string | null;
  sql: string | null;
  row_count: number | null;
  answer: string | null;
  latency_ms: number | null;
  tokens: number;
  feedback: string | null;
  feedback_comment: string | null;
  error: string | null;
  created_at: string;
}

const PAGE = 25;

export function QueryLog() {
  const [feedback, setFeedback] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [errors, setErrors] = useState(false);
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<string | null>(null);

  const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (feedback) params.set("feedback", feedback);
  if (query) params.set("q", query);
  if (errors) params.set("errors", "true");
  const page = useLoad(
    () => api<{ total: number; items: Entry[] }>(`/query-log?${params}`),
    [params.toString()],
  );

  const total = page.data?.total ?? 0;
  return (
    <Page title="Query log" intro="Every question asked, newest first. Wrong answers are the best ones to learn from.">
      <form className="filters" onSubmit={(e) => { e.preventDefault(); setOffset(0); setQuery(search); }}>
        <input aria-label="Search questions and answers" placeholder="Search questions and answers"
               value={search} onChange={(e) => setSearch(e.target.value)} />
        <select aria-label="Rating" value={feedback} onChange={(e) => { setOffset(0); setFeedback(e.target.value); }}>
          <option value="">All ratings</option>
          <option value="wrong">Marked wrong</option>
          <option value="helpful">Marked helpful</option>
          <option value="any">Any rating</option>
        </select>
        <label className="inline">
          <input type="checkbox" checked={errors} onChange={(e) => { setOffset(0); setErrors(e.target.checked); }} />
          Errors only
        </label>
        <button type="submit">Search</button>
      </form>
      <ErrorNote error={page.error} />
      <Loading show={page.loading && !page.data} />
      {page.data?.items.length === 0 && <Empty>Nothing matches.</Empty>}
      {page.data && page.data.items.length > 0 && (
        <table>
          <thead><tr><th>Question</th><th>Source</th><th>Rows</th><th>Rating</th><th>Time</th><th>When</th></tr></thead>
          <tbody>
            {page.data.items.map((q) => (
              <Fragment key={q.id}>
                <tr>
                  <td>
                    <button type="button" className="link" aria-expanded={open === q.id}
                            onClick={() => setOpen(open === q.id ? null : q.id)}>{q.question}</button>
                    {q.error && <Badge tone="bad">error</Badge>}
                  </td>
                  <td>{q.route ?? "–"}</td>
                  <td>{q.row_count ?? "–"}</td>
                  <td>{q.feedback ? <Badge tone={statusTone(q.feedback)}>{q.feedback}</Badge> : "–"}</td>
                  <td>{q.latency_ms ? `${(q.latency_ms / 1000).toFixed(1)} s` : "–"}</td>
                  <td>{when(q.created_at)}</td>
                </tr>
                {open === q.id && (
                  <tr>
                    <td colSpan={6} className="detail">
                      {q.answer && <p>{q.answer}</p>}
                      {q.error && <p className="error">{q.error}</p>}
                      {q.feedback_comment && <p><strong>Comment:</strong> {q.feedback_comment}</p>}
                      {q.sql && <pre>{q.sql}</pre>}
                      <p className="muted small">{q.tokens.toLocaleString()} tokens</p>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
      {total > PAGE && (
        <div className="pager">
          <button type="button" className="secondary" disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE))}>Newer</button>
          <span className="muted">{offset + 1}–{Math.min(offset + PAGE, total)} of {total}</span>
          <button type="button" className="secondary" disabled={offset + PAGE >= total}
                  onClick={() => setOffset(offset + PAGE)}>Older</button>
        </div>
      )}
    </Page>
  );
}
