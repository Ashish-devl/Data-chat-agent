/**
 * <script src="https://your-api.example.com/widget.js" data-key="dc_pub_..."></script>
 *
 * Optional attributes: data-api (API address; default: where widget.js was loaded from),
 * data-prefix, data-title, data-placeholder, data-connection, data-position (right|left),
 * data-color (accent colour).
 */
import { render } from "preact";

import { STYLES } from "./styles";
import { Widget, type WidgetConfig } from "./Widget";

export function mount(config: WidgetConfig, host?: HTMLElement): HTMLElement {
  const el = host ?? document.createElement("div");
  el.setAttribute("data-datachat-widget", "");
  if (!el.isConnected) document.body.appendChild(el);
  const root = el.shadowRoot ?? el.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = STYLES;
  root.appendChild(style);
  const app = document.createElement("div");
  root.appendChild(app);
  render(<Widget config={config} />, app);
  return el;
}

export function configFromScript(script: HTMLScriptElement): WidgetConfig | null {
  const d = script.dataset;
  if (!d.key) {
    console.error("DataChat widget: add data-key=\"dc_pub_...\" to the script tag.");
    return null;
  }
  if (d.key.startsWith("dc_live_")) {
    console.error("DataChat widget: never put a secret key (dc_live_) in a web page. "
      + "Create a publishable key (dc_pub_) instead.");
    return null;
  }
  const origin = script.src ? new URL(script.src, location.href).origin : location.origin;
  return {
    apiKey: d.key,
    baseUrl: d.api ?? origin,
    prefix: d.prefix,
    title: d.title,
    placeholder: d.placeholder,
    connectionId: d.connection,
    position: d.position === "left" ? "left" : "right",
    accent: d.color,
  };
}

const current = document.currentScript as HTMLScriptElement | null;
if (current?.dataset.key !== undefined) {
  const start = () => {
    const config = configFromScript(current);
    if (config) mount(config);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
}
