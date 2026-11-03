import assert from "node:assert/strict";
import { test } from "node:test";

import { DataChatClient, DataChatError, parseSSE } from "../dist/index.js";

function fakeFetch(handler) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    return handler(url, init);
  };
  fn.calls = calls;
  return fn;
}

function json(body, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

function sseStream(chunks) {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
}

test("ask sends the question, key and options", async () => {
  const f = fakeFetch(() => json({ answer: "Six buyers.", rows: [{ n: 6 }] }));
  const dc = new DataChatClient({ apiKey: "dc_live_x", baseUrl: "https://api.test/", fetch: f });
  const a = await dc.ask("How many buyers?", {
    sessionId: "s1", connectionId: "c1", userContext: { company: 7 }, includeRows: false,
  });
  assert.equal(a.answer, "Six buyers.");
  const { url, init } = f.calls[0];
  assert.equal(url, "https://api.test/v1/ask");
  assert.equal(init.headers.Authorization, "Bearer dc_live_x");
  assert.deepEqual(JSON.parse(init.body), {
    question: "How many buyers?", session_id: "s1", connection_id: "c1",
    user_context: { company: 7 }, include_rows: false,
  });
});

test("prefix for an API mounted inside another app", async () => {
  const f = fakeFetch(() => json([]));
  await new DataChatClient({ apiKey: "k", baseUrl: "https://app.test", prefix: "/ai/", fetch: f })
    .listDocuments();
  assert.equal(f.calls[0].url, "https://app.test/ai/v1/documents");
});

test("errors carry status, detail and Retry-After", async () => {
  const f = fakeFetch(() => json({ detail: "Rate limit reached" }, 429, { "retry-after": "12" }));
  const dc = new DataChatClient({ apiKey: "k", fetch: f });
  await assert.rejects(dc.ask("q"), (e) => {
    assert.ok(e instanceof DataChatError);
    assert.equal(e.status, 429);
    assert.equal(e.message, "Rate limit reached");
    assert.equal(e.retryAfter, 12);
    return true;
  });
});

test("a 503 from /ask still returns the body with its error", async () => {
  const f = fakeFetch(() => json({ answer: "", error: "The language model is overloaded" }, 503));
  const a = await new DataChatClient({ apiKey: "k", fetch: f }).ask("q");
  assert.match(a.error, /overloaded/);
});

test("askStream yields meta, data, tokens and done", async () => {
  const body = [
    'event: meta\ndata: {"session_id": "s1", "route": "database"}\n\n',
    'event: data\ndata: {"sql": "SELECT 1", "columns": ["n"], "rows": [{"n": 6}], ',
    '"row_count": 1, "truncated": false}\n\nevent: token\ndata: {"text": "Six "}\n\n',
    'event: token\ndata: {"text": "buyers."}\n\n',
    'event: done\ndata: {"answer": "Six buyers.", "query_id": "q1"}\n\n',
  ];
  const f = fakeFetch(() => new Response(sseStream(body), {
    headers: { "content-type": "text/event-stream" },
  }));
  const events = [];
  for await (const e of new DataChatClient({ apiKey: "k", fetch: f }).askStream("q")) {
    events.push(e);
  }
  assert.deepEqual(events.map((e) => e.type), ["meta", "data", "token", "token", "done"]);
  assert.equal(events.filter((e) => e.type === "token").map((e) => e.text).join(""), "Six buyers.");
  assert.equal(events.at(-1).response.query_id, "q1");
  assert.deepEqual(events[1].rows, [{ n: 6 }]);
});

test("parseSSE copes with events split anywhere and CRLF line ends", async () => {
  const text = 'event: token\r\ndata: {"text": "a"}\r\n\r\nevent: token\ndata: {"text": "b"}\n\n';
  const pieces = text.match(/.{1,3}/gs);
  const out = [];
  for await (const e of parseSSE(sseStream(pieces))) out.push(e);
  assert.deepEqual(out, [
    { event: "token", data: { text: "a" } },
    { event: "token", data: { text: "b" } },
  ]);
});

test("uploadDocument sends multipart form data", async () => {
  const f = fakeFetch(() => json({ id: "d1", status: "queued" }, 201));
  const dc = new DataChatClient({ apiKey: "k", fetch: f });
  const doc = await dc.uploadDocument(new Blob(["%PDF-1.4"], { type: "application/pdf" }), "a.pdf");
  assert.equal(doc.status, "queued");
  const { init } = f.calls[0];
  assert.ok(init.body instanceof FormData);
  assert.equal(init.headers["Content-Type"], undefined); // the browser sets the boundary
});

test("feedback posts the rating", async () => {
  const f = fakeFetch(() => json({ rating: "wrong" }));
  await new DataChatClient({ apiKey: "k", fetch: f }).feedback("q1", "wrong", "off by one");
  assert.deepEqual(JSON.parse(f.calls[0].init.body), {
    query_id: "q1", rating: "wrong", comment: "off by one",
  });
});

test("an api key is required", () => {
  assert.throws(() => new DataChatClient({ apiKey: "" }), /apiKey is required/);
});
