/**
 * JavaScript client for DataChat Agent.
 *
 *   import { DataChatClient } from "datachat-agent";
 *   const dc = new DataChatClient({ baseUrl: "https://api.example.com", apiKey: "dc_live_..." });
 *   const answer = await dc.ask("Which production orders are delayed?");
 *
 * Works in browsers and Node 18+. In a web page use a publishable key (dc_pub_...), never a
 * secret one.
 */

export interface Citation {
  ref: number;
  document_id: string;
  filename: string;
  page: number;
  heading: string | null;
  snippet: string;
  url: string;
}

export interface Chart {
  type: "bar" | "line";
  x: string;
  y: string;
}

export interface AskResponse {
  query_id: string;
  session_id: string;
  answer: string;
  route: "database" | "documents" | "both" | "none";
  sql: string | null;
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
  truncated: boolean;
  citations: Citation[];
  chart: Chart | null;
  latency_ms: number;
  tokens: number;
  error: string | null;
}

export interface AskOptions {
  connectionId?: string;
  sessionId?: string;
  userRef?: string;
  /** Values for row filters. Not allowed with publishable keys. */
  userContext?: Record<string, string | number>;
  includeRows?: boolean;
  signal?: AbortSignal;
}

export type StreamEvent =
  | { type: "meta"; session_id: string; route: AskResponse["route"] }
  | { type: "data"; sql: string; columns: string[]; rows: Record<string, unknown>[];
      row_count: number; truncated: boolean }
  | { type: "token"; text: string }
  | { type: "done"; response: AskResponse }
  | { type: "error"; message: string };

export interface DocumentInfo {
  id: string;
  filename: string;
  size_bytes: number;
  sha256: string;
  pages: number | null;
  doc_date: string | null;
  status: "queued" | "processing" | "ready" | "failed";
  error: string | null;
  chunks: number;
  items: number;
  created_at: string;
}

export class DataChatError extends Error {
  readonly status: number;
  readonly detail: unknown;
  /** Seconds to wait, when the server sent Retry-After (rate limits). */
  readonly retryAfter: number | null;

  constructor(status: number, detail: unknown, retryAfter: number | null) {
    super(typeof detail === "string" ? detail : `DataChat request failed (${status})`);
    this.name = "DataChatError";
    this.status = status;
    this.detail = detail;
    this.retryAfter = retryAfter;
  }
}

export interface ClientOptions {
  apiKey: string;
  /** Where the API runs, e.g. "https://api.example.com" or "http://localhost:8000". */
  baseUrl?: string;
  /** Path the API is mounted under inside another app, e.g. "/ai". */
  prefix?: string;
  fetch?: typeof fetch;
}

export class DataChatClient {
  private readonly apiKey: string;
  private readonly root: string;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ClientOptions) {
    if (!options.apiKey) throw new Error("apiKey is required");
    this.apiKey = options.apiKey;
    const base = (options.baseUrl ?? "http://localhost:8000").replace(/\/+$/, "");
    const prefix = (options.prefix ?? "").replace(/\/+$/, "");
    this.root = `${base}${prefix}/v1`;
    this.fetchImpl = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  /** Ask a question and wait for the whole answer. */
  async ask(question: string, options: AskOptions = {}): Promise<AskResponse> {
    const res = await this.request("/ask", {
      method: "POST",
      body: JSON.stringify(askBody(question, options)),
      signal: options.signal,
    }, [503]);
    return (await res.json()) as AskResponse;
  }

  /** Ask a question and receive the answer as it is written. */
  async *askStream(question: string, options: AskOptions = {}): AsyncGenerator<StreamEvent> {
    const res = await this.request("/ask/stream", {
      method: "POST",
      body: JSON.stringify(askBody(question, options)),
      signal: options.signal,
    });
    if (!res.body) throw new DataChatError(res.status, "The response has no body", null);
    for await (const { event, data } of parseSSE(res.body)) {
      if (event === "done") yield { type: "done", response: data as AskResponse };
      else yield { type: event, ...(data as object) } as StreamEvent;
    }
  }

  /** Mark an answer helpful or wrong. */
  async feedback(queryId: string, rating: "helpful" | "wrong", comment?: string) {
    const res = await this.request("/feedback", {
      method: "POST",
      body: JSON.stringify({ query_id: queryId, rating, comment }),
    });
    return res.json();
  }

  /** The turns of a conversation (secret keys only). */
  async session(sessionId: string) {
    const res = await this.request(`/sessions/${encodeURIComponent(sessionId)}`, {});
    return res.json();
  }

  /** Upload a PDF (needs the docs scope). */
  async uploadDocument(file: Blob, filename = "document.pdf"): Promise<DocumentInfo> {
    const form = new FormData();
    form.append("file", file, filename);
    const res = await this.request("/documents", { method: "POST", body: form });
    return (await res.json()) as DocumentInfo;
  }

  async listDocuments(): Promise<DocumentInfo[]> {
    const res = await this.request("/documents", {});
    return (await res.json()) as DocumentInfo[];
  }

  async usage() {
    const res = await this.request("/usage", {});
    return res.json();
  }

  private async request(path: string, init: RequestInit, okStatuses: number[] = []) {
    const headers: Record<string, string> = { Authorization: `Bearer ${this.apiKey}` };
    if (typeof init.body === "string") headers["Content-Type"] = "application/json";
    const res = await this.fetchImpl(`${this.root}${path}`, { ...init, headers });
    if (!res.ok && !okStatuses.includes(res.status)) {
      let detail: unknown = res.statusText;
      try {
        const body = await res.json();
        detail = body?.detail ?? body;
      } catch {
        /* not JSON */
      }
      const retry = res.headers.get("retry-after");
      throw new DataChatError(res.status, detail, retry ? Number(retry) : null);
    }
    return res;
  }
}

function askBody(question: string, o: AskOptions) {
  const body: Record<string, unknown> = { question };
  if (o.connectionId) body.connection_id = o.connectionId;
  if (o.sessionId) body.session_id = o.sessionId;
  if (o.userRef) body.user_ref = o.userRef;
  if (o.userContext) body.user_context = o.userContext;
  if (o.includeRows === false) body.include_rows = false;
  return body;
}

/** Read Server-Sent Events: yields { event, data } with data parsed as JSON. */
export async function* parseSSE(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<{ event: string; data: unknown }> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
      buffer = buffer.replace(/\r\n/g, "\n");
      let end: number;
      while ((end = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const parsed = parseBlock(block);
        if (parsed) yield parsed;
      }
      if (done) {
        const parsed = parseBlock(buffer);
        if (parsed) yield parsed;
        return;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function parseBlock(block: string): { event: string; data: unknown } | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  if (!data.length) return null;
  const text = data.join("\n");
  try {
    return { event, data: JSON.parse(text) };
  } catch {
    return { event, data: text };
  }
}
