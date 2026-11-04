import { cleanup, fireEvent, render, within, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";

type Handler = (url: string, init: RequestInit) => Response | Promise<Response>;

const OWNER = { id: "u1", email: "asha@example.test", name: "Asha", role: "owner", tenant_id: "t1",
  tenant_name: "Knitwell" };
const MEMBER = { ...OWNER, id: "u2", role: "member", name: "Ravi" };

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function serve(routes: Record<string, Handler>) {
  const calls: { method: string; url: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    const path = new URL(url).pathname.replace(/^\/v1/, "") + new URL(url).search;
    calls.push({ method, url: path, body: typeof init.body === "string" ? JSON.parse(init.body) : init.body });
    const key = Object.keys(routes).find((k) => {
      const [m, p] = k.split(" ");
      return m === method && (path === p || path.split("?")[0] === p);
    });
    return key ? routes[key](url, init) : json({ detail: `no route ${method} ${path}` }, 404);
  }));
  return calls;
}

const empty = {
  "GET /usage": () => json({ period_start: "2026-11-01", requests: 3, tokens: 1200,
    monthly_token_limit: null, tokens_remaining: null, rate_limit_per_minute: 60 }),
  "GET /query-log": () => json({ total: 0, items: [] }),
  "GET /documents": () => json([]),
};

beforeEach(() => {
  localStorage.clear();
  location.hash = "";
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("signing in", () => {
  it("shows the server's message for a wrong password", async () => {
    serve({ "POST /auth/login": () => json({ detail: "Wrong email or password" }, 401) });
    render(<App />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "asha@example.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Wrong email or password");
    expect(localStorage.getItem("datachat-console-token")).toBeNull();
  });

  it("signs in an owner and shows every page", async () => {
    const calls = serve({
      ...empty,
      "POST /auth/login": () => json({ token: "dc_sess_abc", expires_at: "x", user: OWNER }),
    });
    render(<App />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "asha@example.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "Correct-Horse-42" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Welcome, Asha")).toBeTruthy();
    expect(localStorage.getItem("datachat-console-token")).toBe("dc_sess_abc");
    // Only the menu: the overview can also link to "Try it" once its data has loaded.
    const menu = within(screen.getByRole("navigation", { name: "Main" }));
    for (const name of ["Databases", "Documents", "Try it", "Query log", "API keys", "Users"]) {
      expect(menu.getByRole("link", { name })).toBeTruthy();
    }
    expect(await screen.findByText("1,200")).toBeTruthy(); // tokens this month
    const usage = calls.find((c) => c.url === "/usage");
    expect(usage).toBeTruthy();
  });

  it("a member sees only the pages a member may use", async () => {
    localStorage.setItem("datachat-console-token", "dc_sess_m");
    const calls = serve({ ...empty, "GET /auth/me": () => json(MEMBER) });
    render(<App />);
    expect(await screen.findByText("Welcome, Ravi")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "API keys" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Users" })).toBeNull();
    expect(screen.getByRole("link", { name: "Documents" })).toBeTruthy();
    expect(calls.some((c) => c.url === "/usage")).toBe(false); // members are not asked for it
  });

  it("an expired session goes back to the sign-in page", async () => {
    localStorage.setItem("datachat-console-token", "dc_sess_old");
    serve({ "GET /auth/me": () => json({ detail: "Invalid or expired session" }, 401) });
    render(<App />);
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeTruthy();
    expect(localStorage.getItem("datachat-console-token")).toBeNull();
  });
});

describe("pages", () => {
  function signedInAt(hash: string, routes: Record<string, Handler>) {
    localStorage.setItem("datachat-console-token", "dc_sess_abc");
    location.hash = hash;
    return serve({ ...empty, "GET /auth/me": () => json(OWNER), ...routes });
  }

  it("creates a website key, shows it once with the script tag", async () => {
    const calls = signedInAt("#/keys", {
      "GET /keys": () => json([]),
      "POST /keys": () => json({ id: "k1", key: "dc_pub_secretvalue", name: "site", prefix: "dc_pub_secr",
        scopes: ["ask"], key_type: "publishable", allowed_domains: ["erp.example.com"],
        revoked: false, created_at: "2026-11-04T10:00:00Z", last_used_at: null }, 201),
    });
    render(<App />);
    fireEvent.click(await screen.findByLabelText(/Publishable/));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "site" } });
    fireEvent.change(screen.getByLabelText(/Websites it may be used on/),
      { target: { value: "erp.example.com, *.example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Create key" }));
    expect(await screen.findByText("dc_pub_secretvalue")).toBeTruthy();
    expect(screen.getByText(/widget\.js" data-key="dc_pub_secretvalue"/)).toBeTruthy();
    const sent = calls.find((c) => c.method === "POST" && c.url === "/keys")!.body;
    expect(sent).toEqual({ name: "site", type: "publishable",
      allowed_domains: ["erp.example.com", "*.example.com"] });
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByText("dc_pub_secretvalue")).toBeNull());
  });

  it("adds a database, tests it and opens it", async () => {
    const conn = { id: "c1", name: "ERP", dialect: "postgresql", target: "ro@db/erp", status: "ok",
      status_detail: "Connected as 'ro', read-only.", last_scanned_at: null, row_filters: {} };
    const calls = signedInAt("#/connections", {
      "GET /connections": () => json([]),
      "POST /connections": () => json(conn, 201),
      "POST /connections/c1/test": () => json(conn),
      "GET /connections/c1": () => json(conn),
      "GET /connections/c1/schema": () => json([]),
    });
    render(<App />);
    expect(await screen.findByText("No database connected yet.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "ERP" } });
    fireEvent.change(screen.getByLabelText("Connection string"),
      { target: { value: "postgresql://ro:pw@db/erp" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect and test" }));
    expect(await screen.findByText("Connected as 'ro', read-only.")).toBeTruthy();
    expect(location.hash).toBe("#/connections/c1");
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual(expect.arrayContaining([
      "POST /connections", "POST /connections/c1/test"]));
  });

  it("filters the query log by rating", async () => {
    const calls = signedInAt("#/log", {
      "GET /query-log": () => json({ total: 1, items: [{ id: "q1", question: "How many buyers?",
        route: "database", sql: "SELECT COUNT(*) FROM buyers", row_count: 1, answer: "Six.",
        latency_ms: 2100, tokens: 900, feedback: "wrong", feedback_comment: "Should be 7",
        error: null, created_at: "2026-11-04T09:00:00Z" }] }),
    });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "How many buyers?" }));
    expect(screen.getByText("Should be 7")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Rating"), { target: { value: "wrong" } });
    await waitFor(() => expect(calls.some((c) => c.url.includes("feedback=wrong"))).toBe(true));
  });
});
