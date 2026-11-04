import { type FormEvent, useState } from "react";

import { api, apiBase, del, post } from "../api";
import { Badge, Empty, ErrorNote, Loading, OneTimeSecret, Page, useAction, useLoad, when } from "../ui";

interface Key {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  key_type: "secret" | "publishable";
  allowed_domains: string[];
  revoked: boolean;
  created_at: string;
  last_used_at: string | null;
}

export function Keys() {
  const list = useLoad(() => api<Key[]>("/keys"));
  const action = useAction();
  const [type, setType] = useState<"secret" | "publishable">("secret");
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["ask"]);
  const [domains, setDomains] = useState("");
  const [created, setCreated] = useState<{ key: string; type: string } | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    await action.run(async () => {
      const body = type === "publishable"
        ? { name, type, allowed_domains: domains.split(/[\s,]+/).filter(Boolean) }
        : { name, type, scopes };
      const k = await post<Key & { key: string }>("/keys", body);
      setCreated({ key: k.key, type });
      setName("");
      setDomains("");
      list.reload();
    });
  }

  const toggle = (s: string) =>
    setScopes((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));

  return (
    <Page title="API keys" intro="Secret keys are for servers. Publishable keys go in a web page for the chat widget.">
      {created && (
        <OneTimeSecret label={created.type === "publishable" ? "Your publishable key" : "Your secret key"}
                       value={created.key} onDone={() => setCreated(null)} />
      )}
      {created?.type === "publishable" && (
        <div className="card">
          <p>Add this to your web page:</p>
          <pre>{`<script src="${apiBase()}/widget.js" data-key="${created.key}"></script>`}</pre>
        </div>
      )}
      <ErrorNote error={list.error ?? action.error} />
      <Loading show={list.loading && !list.data} />
      {list.data?.length === 0 && <Empty>No keys yet.</Empty>}
      {list.data && list.data.length > 0 && (
        <table>
          <thead><tr><th>Name</th><th>Key</th><th>Can</th><th>Last used</th><th /></tr></thead>
          <tbody>
            {list.data.map((k) => (
              <tr key={k.id} className={k.revoked ? "hidden-item" : undefined}>
                <td>{k.name}</td>
                <td><code>{k.prefix}…</code> {k.key_type === "publishable" && <Badge tone="plain">website</Badge>}</td>
                <td>
                  {k.scopes.join(", ")}
                  {k.allowed_domains.length > 0 && <div className="muted small">on {k.allowed_domains.join(", ")}</div>}
                </td>
                <td>{when(k.last_used_at)}</td>
                <td>
                  {k.revoked ? <Badge tone="bad">revoked</Badge> : (
                    <button type="button" className="link danger" onClick={async () => {
                      if (confirm(`Revoke "${k.name}"? Anything using it stops working at once.`)) {
                        await action.run(() => del(`/keys/${k.id}`));
                        list.reload();
                      }
                    }}>Revoke</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <form className="card" onSubmit={submit}>
        <h2>New key</h2>
        <fieldset className="choice">
          <legend>Kind</legend>
          <label className="inline"><input type="radio" checked={type === "secret"} onChange={() => setType("secret")} /> Secret (server)</label>
          <label className="inline"><input type="radio" checked={type === "publishable"} onChange={() => setType("publishable")} /> Publishable (web page)</label>
        </fieldset>
        <label>Name<input required maxLength={100} value={name} placeholder="ERP backend" onChange={(e) => setName(e.target.value)} /></label>
        {type === "secret" ? (
          <fieldset className="choice">
            <legend>Allowed to</legend>
            {[["ask", "Ask questions"], ["docs", "Manage documents"], ["admin", "Everything (admin)"]].map(([s, label]) => (
              <label key={s} className="inline"><input type="checkbox" checked={scopes.includes(s)} onChange={() => toggle(s)} /> {label}</label>
            ))}
          </fieldset>
        ) : (
          <label>
            Websites it may be used on
            <input required value={domains} placeholder="erp.example.com, *.example.com" onChange={(e) => setDomains(e.target.value)} />
            <span className="muted small">It can only ask questions, and only from these sites.</span>
          </label>
        )}
        <button type="submit" disabled={action.busy || (type === "secret" && scopes.length === 0)}>Create key</button>
      </form>
    </Page>
  );
}
