import { afterEach, describe, expect, it, vi } from "vitest";

import { configFromScript, mount } from "../src/main";

const SSE = [
  'event: meta\ndata: {"session_id": "s", "route": "database"}\n\n',
  'event: data\ndata: {"sql": "SELECT 1", "columns": ["style_no", "behind"], "rows": ',
  '[{"style_no": "26C1740", "behind": 1390}], "row_count": 1, "truncated": false}\n\n',
  'event: token\ndata: {"text": "One order "}\n\n',
  'event: token\ndata: {"text": "is late."}\n\n',
  'event: done\ndata: {"answer": "One order is late.", "query_id": "q-1", "citations": ',
  '[{"ref": 1, "filename": "manual.pdf", "page": 2}], "error": null}\n\n',
];

function stream(chunks: string[]) {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(c) {
      chunks.forEach((x) => c.enqueue(enc.encode(x)));
      c.close();
    },
  });
}

const tick = () => new Promise((r) => setTimeout(r, 20));

async function until(check: () => boolean, tries = 50) {
  for (let i = 0; i < tries && !check(); i++) await tick();
  expect(check()).toBe(true);
}

function setup(fetchImpl: typeof fetch) {
  vi.stubGlobal("fetch", fetchImpl);
  const el = mount({ apiKey: "dc_pub_test", baseUrl: "https://api.test", title: "Ask Knitwell" });
  const root = el.shadowRoot!;
  return { el, root, $: (sel: string) => root.querySelector(sel) as HTMLElement };
}

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
});

describe("widget", () => {
  it("opens, streams an answer with a table and sources, and sends feedback", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      if (url.endsWith("/ask/stream")) {
        return new Response(stream(SSE), { headers: { "content-type": "text/event-stream" } });
      }
      return new Response(JSON.stringify({ rating: "helpful" }));
    }) as unknown as typeof fetch;
    const { root, $ } = setup(fetchImpl);

    expect($(".panel")).toBeNull();
    $(".launcher").click();
    await tick();
    expect($(".panel").getAttribute("aria-label")).toBe("Ask Knitwell");

    const box = $("textarea") as HTMLTextAreaElement;
    box.value = "Which orders are late?";
    box.dispatchEvent(new Event("input", { bubbles: true }));
    await tick();
    $("form button").click();
    await until(() => root.querySelector(".rate") !== null);

    const bot = root.querySelectorAll(".msg.bot")[0] as HTMLElement;
    expect(bot.textContent).toContain("One order is late.");
    expect(bot.querySelector("td")!.textContent).toBe("26C1740");
    expect(bot.querySelector(".sources")!.textContent).toContain("manual.pdf, page 2");

    const ask = calls[0];
    expect(ask.url).toBe("https://api.test/v1/ask/stream");
    expect((ask.init.headers as Record<string, string>).Authorization).toBe("Bearer dc_pub_test");
    expect(JSON.parse(ask.init.body as string).question).toBe("Which orders are late?");

    (root.querySelector(".rate button") as HTMLElement).click();
    await until(() => calls.length === 2);
    expect(calls[1].url).toBe("https://api.test/v1/feedback");
    expect(JSON.parse(calls[1].init.body as string)).toMatchObject({
      query_id: "q-1", rating: "helpful",
    });
  });

  it("shows a friendly message when the key is not allowed on this site", async () => {
    const fetchImpl = vi.fn(async () => new Response(
      JSON.stringify({ detail: "This publishable key is not allowed on this website." }),
      { status: 403 },
    )) as unknown as typeof fetch;
    const { root, $ } = setup(fetchImpl);
    $(".launcher").click();
    await tick();
    const box = $("textarea") as HTMLTextAreaElement;
    box.value = "hello";
    box.dispatchEvent(new Event("input", { bubbles: true }));
    await tick();
    $("form button").click();
    await until(() => root.querySelector(".msg.bot.error") !== null);
    expect(root.querySelector(".msg.bot")!.textContent).toBe(
      "This chat is not set up for this website.");
  });

  it("keeps its styles inside a shadow root", () => {
    const { el } = setup(vi.fn() as unknown as typeof fetch);
    expect(el.shadowRoot!.querySelector("style")!.textContent).toContain(".launcher");
    expect(document.head.querySelector("style")).toBeNull();
  });
});

describe("script tag", () => {
  function script(attrs: Record<string, string>) {
    const s = document.createElement("script");
    s.src = "https://api.example.com/widget.js";
    Object.entries(attrs).forEach(([k, v]) => s.setAttribute(k, v));
    return s;
  }

  it("reads its settings, defaulting the API to where widget.js came from", () => {
    const c = configFromScript(script({ "data-key": "dc_pub_x", "data-position": "left" }))!;
    expect(c.baseUrl).toBe("https://api.example.com");
    expect(c.position).toBe("left");
  });

  it("refuses secret keys and missing keys", () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(configFromScript(script({ "data-key": "dc_live_secret" }))).toBeNull();
    expect(configFromScript(script({}))).toBeNull();
    expect(err).toHaveBeenCalledTimes(2);
  });
});
