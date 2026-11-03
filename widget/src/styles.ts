// Lives inside the widget's shadow root, so it never touches the host page.
export const STYLES = `
:host { all: initial; }
* { box-sizing: border-box; }
.root {
  --accent: #2563eb; --accent-text: #ffffff;
  --bg: #ffffff; --text: #111827; --muted: #6b7280; --line: #e5e7eb; --bubble: #f3f4f6;
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  color: var(--text); position: fixed; bottom: 20px; z-index: 2147483000;
}
@media (prefers-color-scheme: dark) {
  .root { --bg: #111827; --text: #f9fafb; --muted: #9ca3af; --line: #374151; --bubble: #1f2937; }
}
.root.right { right: 20px; } .root.left { left: 20px; }
.launcher {
  width: 56px; height: 56px; border-radius: 50%; border: 0; cursor: pointer;
  background: var(--accent); color: var(--accent-text); box-shadow: 0 6px 20px rgba(0,0,0,.2);
  display: grid; place-items: center;
}
.launcher:focus-visible, button:focus-visible, textarea:focus-visible {
  outline: 3px solid var(--accent); outline-offset: 2px;
}
.panel {
  position: absolute; bottom: 72px; width: min(380px, calc(100vw - 32px));
  height: min(560px, calc(100vh - 120px)); background: var(--bg); border: 1px solid var(--line);
  border-radius: 14px; box-shadow: 0 12px 40px rgba(0,0,0,.22);
  display: flex; flex-direction: column; overflow: hidden;
}
.right .panel { right: 0; } .left .panel { left: 0; }
.head {
  padding: 12px 14px; background: var(--accent); color: var(--accent-text);
  display: flex; align-items: center; justify-content: space-between; font-weight: 600;
}
.head button { background: transparent; border: 0; color: inherit; cursor: pointer; font-size: 18px; }
.log { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 10px; }
.empty { color: var(--muted); margin: auto; text-align: center; padding: 0 16px; }
.msg { max-width: 92%; padding: 9px 12px; border-radius: 12px; white-space: pre-wrap;
  overflow-wrap: anywhere; }
.user { align-self: flex-end; background: var(--accent); color: var(--accent-text); }
.bot { align-self: flex-start; background: var(--bubble); }
.bot.error { border: 1px solid #ef4444; }
.meta { color: var(--muted); font-size: 12px; margin-top: 6px; }
.sources { margin-top: 6px; font-size: 12px; color: var(--muted); }
table { border-collapse: collapse; margin-top: 8px; font-size: 12px; display: block;
  overflow-x: auto; max-width: 100%; }
th, td { border: 1px solid var(--line); padding: 3px 6px; text-align: left; white-space: nowrap; }
.rate { margin-top: 6px; display: flex; gap: 6px; }
.rate button { font: inherit; font-size: 12px; padding: 2px 8px; border-radius: 6px;
  border: 1px solid var(--line); background: var(--bg); color: var(--text); cursor: pointer; }
.rate button[aria-pressed="true"] { border-color: var(--accent); color: var(--accent); }
form { display: flex; gap: 8px; padding: 10px; border-top: 1px solid var(--line); }
textarea { flex: 1; resize: none; height: 40px; padding: 9px 10px; border-radius: 8px;
  border: 1px solid var(--line); font: inherit; color: var(--text); background: var(--bg); }
form button { border: 0; border-radius: 8px; padding: 0 14px; background: var(--accent);
  color: var(--accent-text); font: inherit; font-weight: 600; cursor: pointer; }
form button:disabled { opacity: .5; cursor: default; }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
`;
