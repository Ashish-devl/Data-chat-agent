import { api, type Me } from "../api";
import { Badge, ErrorNote, Loading, Page, statusTone, useLoad, when } from "../ui";

interface Usage {
  period_start: string;
  requests: number;
  tokens: number;
  monthly_token_limit: number | null;
  tokens_remaining: number | null;
  rate_limit_per_minute: number;
}

interface LogItem {
  id: string;
  question: string;
  route: string | null;
  feedback: string | null;
  error: string | null;
  latency_ms: number | null;
  created_at: string;
}

export function Overview({ me }: { me: Me }) {
  const owner = me.role === "owner";
  const usage = useLoad(() => (owner ? api<Usage>("/usage") : Promise.resolve(null)), [owner]);
  const recent = useLoad(
    () => (owner ? api<{ items: LogItem[] }>("/query-log?limit=8") : Promise.resolve(null)),
    [owner],
  );
  const docs = useLoad(() => api<{ status: string }[]>("/documents"));

  const u = usage.data;
  const ready = docs.data?.filter((d) => d.status === "ready").length ?? 0;
  return (
    <Page title={`Welcome, ${me.name ?? me.email}`} intro={me.tenant_name}>
      <ErrorNote error={usage.error ?? recent.error ?? docs.error} />
      <div className="tiles">
        {u && (
          <>
            <div className="tile">
              <span className="tile-label">Questions this month</span>
              <span className="tile-value">{u.requests.toLocaleString()}</span>
            </div>
            <div className="tile">
              <span className="tile-label">Model tokens this month</span>
              <span className="tile-value">{u.tokens.toLocaleString()}</span>
              <span className="muted small">
                {u.monthly_token_limit === null
                  ? "No monthly cap"
                  : `${(u.tokens_remaining ?? 0).toLocaleString()} left of ${u.monthly_token_limit.toLocaleString()}`}
              </span>
            </div>
            <div className="tile">
              <span className="tile-label">Requests per minute per key</span>
              <span className="tile-value">{u.rate_limit_per_minute}</span>
            </div>
          </>
        )}
        <div className="tile">
          <span className="tile-label">Documents ready</span>
          <span className="tile-value">{ready}</span>
          <span className="muted small">of {docs.data?.length ?? 0} uploaded</span>
        </div>
      </div>

      {owner && (
        <>
          <h2>Recent questions</h2>
          <Loading show={recent.loading} />
          {recent.data && recent.data.items.length === 0 && (
            <p className="empty">No questions yet. Try one on the <a href="#/try">Try it</a> page.</p>
          )}
          {recent.data && recent.data.items.length > 0 && (
            <table>
              <thead>
                <tr><th>Question</th><th>Source</th><th>Rating</th><th>Time</th><th>When</th></tr>
              </thead>
              <tbody>
                {recent.data.items.map((q) => (
                  <tr key={q.id}>
                    <td>{q.question}{q.error && <Badge tone="bad">error</Badge>}</td>
                    <td>{q.route ?? "–"}</td>
                    <td>{q.feedback ? <Badge tone={statusTone(q.feedback)}>{q.feedback}</Badge> : "–"}</td>
                    <td>{q.latency_ms ? `${(q.latency_ms / 1000).toFixed(1)} s` : "–"}</td>
                    <td>{when(q.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p><a href="#/log">See the whole query log</a></p>
        </>
      )}
    </Page>
  );
}
