import { type FormEvent, useState } from "react";

import { api, del, patch, post } from "../api";
import { Badge, Empty, ErrorNote, Loading, Page, statusTone, useAction, useLoad, when } from "../ui";

interface Connection {
  id: string;
  name: string;
  dialect: string;
  target: string;
  status: string;
  status_detail: string | null;
  last_scanned_at: string | null;
  row_filters: Record<string, string>;
}

interface Column {
  id: string;
  name: string;
  data_type: string | null;
  is_primary_key: boolean;
  foreign_key: string | null;
  description: string | null;
  sample_values: unknown[];
  hidden: boolean;
}

interface SchemaTable {
  id: string;
  schema_name: string;
  name: string;
  row_count: number | null;
  description: string | null;
  hidden: boolean;
  columns: Column[];
}

interface Term { id: string; term: string; definition: string }
interface Example { id: string; question: string; sql: string }

export function Connections({ go }: { go: (path: string) => void }) {
  const list = useLoad(() => api<Connection[]>("/connections"));
  const [name, setName] = useState("");
  const [dsn, setDsn] = useState("");
  const add = useAction();

  async function submit(e: FormEvent) {
    e.preventDefault();
    const ok = await add.run(async () => {
      const c = await post<Connection>("/connections", { name, dsn });
      await post(`/connections/${c.id}/test`);
      go(`/connections/${c.id}`);
    });
    if (ok) {
      setName("");
      setDsn("");
    }
  }

  return (
    <Page title="Databases"
          intro="DataChat only ever reads. Connect with a database user that can SELECT and nothing else.">
      <ErrorNote error={list.error} />
      <Loading show={list.loading && !list.data} />
      {list.data?.length === 0 && <Empty>No database connected yet.</Empty>}
      {list.data && list.data.length > 0 && (
        <table>
          <thead><tr><th>Name</th><th>Database</th><th>Status</th><th>Last scan</th></tr></thead>
          <tbody>
            {list.data.map((c) => (
              <tr key={c.id}>
                <td><a href={`#/connections/${c.id}`}>{c.name}</a></td>
                <td><code>{c.dialect}</code> {c.target}</td>
                <td><Badge tone={statusTone(c.status)}>{c.status}</Badge></td>
                <td>{when(c.last_scanned_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <form className="card" onSubmit={submit}>
        <h2>Connect a database</h2>
        <label>
          Name
          <input required maxLength={100} value={name} placeholder="ERP"
                 onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          Connection string
          <input required value={dsn} spellCheck={false}
                 placeholder="postgresql://readonly:password@host:5432/erp  or  mysql://…"
                 onChange={(e) => setDsn(e.target.value)} />
        </label>
        <p className="muted small">The password is encrypted at rest and never shown again.</p>
        <ErrorNote error={add.error} />
        <button type="submit" disabled={add.busy}>{add.busy ? "Connecting…" : "Connect and test"}</button>
      </form>
    </Page>
  );
}

export function ConnectionDetail({ id, go }: { id: string; go: (path: string) => void }) {
  const conn = useLoad(() => api<Connection>(`/connections/${id}`), [id]);
  const schema = useLoad(() => api<SchemaTable[]>(`/connections/${id}/schema`), [id]);
  const action = useAction();
  const [tab, setTab] = useState<"schema" | "terms" | "examples">("schema");

  const c = conn.data;
  const act = (fn: () => Promise<unknown>) => action.run(async () => {
    await fn();
    await Promise.all([conn.reload(), schema.reload()]);
  });

  return (
    <Page
      title={c?.name ?? "Database"}
      intro={c && <><code>{c.dialect}</code> {c.target}</>}
      actions={c && (
        <>
          <button type="button" disabled={action.busy}
                  onClick={() => act(() => post(`/connections/${id}/test`))}>Test</button>
          <button type="button" disabled={action.busy}
                  onClick={() => act(() => post(`/connections/${id}/scan`))}>
            {action.busy ? "Working…" : "Scan schema"}
          </button>
          <button type="button" className="danger" disabled={action.busy} onClick={async () => {
            if (!confirm(`Remove the connection "${c.name}"? Its schema notes go too.`)) return;
            if (await action.run(() => del(`/connections/${id}`))) go("/connections");
          }}>Remove</button>
        </>
      )}
    >
      <ErrorNote error={conn.error ?? action.error} />
      {c && (
        <p>
          <Badge tone={statusTone(c.status)}>{c.status}</Badge>{" "}
          <span className="muted">{c.status_detail}</span>
        </p>
      )}
      <div className="tabs" role="tablist">
        {(["schema", "terms", "examples"] as const).map((t) => (
          <button key={t} type="button" role="tab" aria-selected={tab === t}
                  className={tab === t ? "tab active" : "tab"} onClick={() => setTab(t)}>
            {t === "schema" ? "Tables and columns" : t === "terms" ? "Business terms" : "Worked examples"}
          </button>
        ))}
      </div>
      {tab === "schema" && <SchemaView tables={schema.data} loading={schema.loading}
                                       error={schema.error} reload={schema.reload} />}
      {tab === "terms" && <Terms connectionId={id} />}
      {tab === "examples" && <Examples connectionId={id} />}
    </Page>
  );
}

function SchemaView({ tables, loading, error, reload }: {
  tables: SchemaTable[] | null; loading: boolean; error: string | null; reload: () => void;
}) {
  const action = useAction();
  const [open, setOpen] = useState<string | null>(null);
  const update = (itemId: string, body: object) =>
    action.run(async () => {
      await patch(`/schema-items/${itemId}`, body);
      reload();
    });

  if (loading && !tables) return <Loading show />;
  if (!tables?.length) return <><ErrorNote error={error} /><Empty>Scan the schema to see its tables.</Empty></>;
  return (
    <>
      <p className="muted">
        Hidden tables and columns are never shown to the model and can never be queried. A
        description helps the model pick the right column.
      </p>
      <ErrorNote error={error ?? action.error} />
      {tables.map((t) => (
        <div key={t.id} className={`table-card${t.hidden ? " hidden-item" : ""}`}>
          <div className="table-head">
            <button type="button" className="link" aria-expanded={open === t.id}
                    onClick={() => setOpen(open === t.id ? null : t.id)}>
              {t.schema_name}.{t.name}
            </button>
            <span className="muted small">{t.row_count?.toLocaleString() ?? "?"} rows · {t.columns.length} columns</span>
            <label className="inline">
              <input type="checkbox" checked={t.hidden}
                     onChange={(e) => update(t.id, { hidden: e.target.checked })} /> Hidden
            </label>
          </div>
          {open === t.id && (
            <table>
              <thead><tr><th>Column</th><th>Type</th><th>Examples</th><th>Description</th><th>Hidden</th></tr></thead>
              <tbody>
                {t.columns.map((col) => (
                  <tr key={col.id} className={col.hidden ? "hidden-item" : undefined}>
                    <td>
                      <code>{col.name}</code>
                      {col.is_primary_key && <Badge tone="plain">key</Badge>}
                      {col.foreign_key && <span className="muted small"> → {col.foreign_key}</span>}
                    </td>
                    <td className="muted">{col.data_type}</td>
                    <td className="muted small">{col.sample_values.slice(0, 3).join(", ")}</td>
                    <td>
                      <input aria-label={`Description of ${col.name}`} defaultValue={col.description ?? ""}
                             placeholder="What this column means"
                             onBlur={(e) => {
                               if (e.target.value !== (col.description ?? "")) {
                                 update(col.id, { description: e.target.value });
                               }
                             }} />
                    </td>
                    <td>
                      <input type="checkbox" aria-label={`Hide ${col.name}`} checked={col.hidden}
                             onChange={(e) => update(col.id, { hidden: e.target.checked })} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ))}
    </>
  );
}

function Terms({ connectionId }: { connectionId: string }) {
  const list = useLoad(() => api<Term[]>(`/connections/${connectionId}/terms`), [connectionId]);
  const [term, setTerm] = useState("");
  const [definition, setDefinition] = useState("");
  const action = useAction();
  return (
    <>
      <p className="muted">Words your company uses, e.g. “open PO” = a purchase order whose status is Open or Partially Received.</p>
      <ErrorNote error={list.error ?? action.error} />
      {list.data?.length === 0 && <Empty>No business terms yet.</Empty>}
      {list.data && list.data.length > 0 && (
        <table>
          <thead><tr><th>Term</th><th>Meaning</th><th /></tr></thead>
          <tbody>
            {list.data.map((t) => (
              <tr key={t.id}>
                <td><strong>{t.term}</strong></td>
                <td>{t.definition}</td>
                <td><button type="button" className="link danger"
                            onClick={() => action.run(async () => { await del(`/terms/${t.id}`); list.reload(); })}>Remove</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form className="card row" onSubmit={async (e) => {
        e.preventDefault();
        if (await action.run(() => post(`/connections/${connectionId}/terms`, { term, definition }))) {
          setTerm("");
          setDefinition("");
          list.reload();
        }
      }}>
        <input aria-label="Term" required placeholder="open PO" value={term} onChange={(e) => setTerm(e.target.value)} />
        <input aria-label="Meaning" required placeholder="a purchase order whose status is Open" value={definition}
               onChange={(e) => setDefinition(e.target.value)} />
        <button type="submit" disabled={action.busy}>Add term</button>
      </form>
    </>
  );
}

function Examples({ connectionId }: { connectionId: string }) {
  const list = useLoad(() => api<Example[]>(`/connections/${connectionId}/examples`), [connectionId]);
  const [question, setQuestion] = useState("");
  const [sql, setSql] = useState("");
  const action = useAction();
  return (
    <>
      <p className="muted">A question with the right SQL teaches the model your conventions. The SQL must pass the same safety checks as any query.</p>
      <ErrorNote error={list.error ?? action.error} />
      {list.data?.length === 0 && <Empty>No worked examples yet.</Empty>}
      {list.data?.map((x) => (
        <div key={x.id} className="card">
          <p><strong>{x.question}</strong></p>
          <pre>{x.sql}</pre>
          <button type="button" className="link danger"
                  onClick={() => action.run(async () => { await del(`/examples/${x.id}`); list.reload(); })}>Remove</button>
        </div>
      ))}
      <form className="card" onSubmit={async (e) => {
        e.preventDefault();
        if (await action.run(() => post(`/connections/${connectionId}/examples`, { question, sql }))) {
          setQuestion("");
          setSql("");
          list.reload();
        }
      }}>
        <label>Question<input required value={question} onChange={(e) => setQuestion(e.target.value)} /></label>
        <label>SQL<textarea required rows={4} spellCheck={false} value={sql} onChange={(e) => setSql(e.target.value)} /></label>
        <button type="submit" disabled={action.busy}>Add example</button>
      </form>
    </>
  );
}
