import { Fragment, useEffect, useRef, useState } from "react";

import { api, apiBase, del, getToken } from "../api";
import { Badge, Empty, ErrorNote, Loading, Page, statusTone, useAction, useLoad, when } from "../ui";

interface Doc {
  id: string;
  filename: string;
  size_bytes: number;
  pages: number | null;
  doc_date: string | null;
  status: string;
  error: string | null;
  chunks: number;
  items: number;
  created_at: string;
}

interface Item { id: string; page: number; item: string | null; qty: number | null; unit: string | null; rate: number | null; amount: number | null }

const num = (v: number | null) => (v === null ? "–" : v.toLocaleString());

export function Documents() {
  const list = useLoad(() => api<Doc[]>("/documents"));
  const action = useAction();
  const fileRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState<string | null>(null);

  // While anything is still being processed, check again every few seconds.
  const pending = list.data?.some((d) => d.status === "queued" || d.status === "processing");
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(list.reload, 3000);
    return () => clearInterval(t);
  }, [pending, list.reload]);

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    await action.run(async () => {
      for (const file of Array.from(files)) {
        const form = new FormData();
        form.append("file", file, file.name);
        await api("/documents", { method: "POST", body: form });
      }
    });
    if (fileRef.current) fileRef.current.value = "";
    list.reload();
  }

  async function download(d: Doc) {
    const res = await fetch(`${apiBase()}/v1/documents/${d.id}/file`, {
      headers: { Authorization: `Bearer ${getToken()}` },
    });
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: d.filename });
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <Page
      title="Documents"
      intro="PDFs with a text layer, up to 20 MB. Answers from them cite the file and page."
      actions={
        <label className="button">
          {action.busy ? "Uploading…" : "Upload PDFs"}
          <input ref={fileRef} type="file" accept="application/pdf" multiple hidden
                 onChange={(e) => upload(e.target.files)} />
        </label>
      }
    >
      <ErrorNote error={list.error ?? action.error} />
      <Loading show={list.loading && !list.data} />
      {list.data?.length === 0 && <Empty>No documents yet.</Empty>}
      {list.data && list.data.length > 0 && (
        <table>
          <thead>
            <tr><th>File</th><th>Status</th><th>Pages</th><th>Line items</th><th>Date on document</th><th>Uploaded</th><th /></tr>
          </thead>
          <tbody>
            {list.data.map((d) => (
              <Fragment key={d.id}>
                <tr>
                  <td>{d.filename}</td>
                  <td>
                    <Badge tone={statusTone(d.status)}>{d.status}</Badge>
                    {d.error && <div className="muted small">{d.error}</div>}
                  </td>
                  <td>{d.pages ?? "–"}</td>
                  <td>
                    {d.items > 0
                      ? <button type="button" className="link" onClick={() => setOpen(open === d.id ? null : d.id)}>{d.items}</button>
                      : "–"}
                  </td>
                  <td>{d.doc_date ?? "–"}</td>
                  <td>{when(d.created_at)}</td>
                  <td className="actions">
                    <button type="button" className="link" onClick={() => download(d)}>Download</button>
                    <button type="button" className="link danger" onClick={async () => {
                      if (confirm(`Delete ${d.filename}?`)) {
                        await action.run(() => del(`/documents/${d.id}`));
                        list.reload();
                      }
                    }}>Delete</button>
                  </td>
                </tr>
                {open === d.id && (
                  <tr><td colSpan={7}><ItemsTable id={d.id} /></td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </Page>
  );
}

function ItemsTable({ id }: { id: string }) {
  const items = useLoad(() => api<Item[]>(`/documents/${id}/items`), [id]);
  if (!items.data) return <Loading show />;
  return (
    <table className="nested">
      <thead><tr><th>Page</th><th>Item</th><th>Qty</th><th>Unit</th><th>Rate</th><th>Amount</th></tr></thead>
      <tbody>
        {items.data.map((i) => (
          <tr key={i.id}>
            <td>{i.page}</td><td>{i.item}</td><td>{num(i.qty)}</td><td>{i.unit ?? "–"}</td>
            <td>{num(i.rate)}</td><td>{num(i.amount)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
