import { fileURLToPath } from "node:url";

import preact from "@preact/preset-vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [preact()],
  resolve: {
    alias: {
      // Build against the SDK's source so the widget always matches it.
      "datachat-agent": fileURLToPath(new URL("../sdk-js/src/index.ts", import.meta.url)),
    },
  },
  build: {
    lib: {
      entry: "src/main.tsx",
      name: "DataChatWidget",
      formats: ["iife"],
      fileName: () => "widget.js",
    },
    target: "es2019",
    minify: true,
  },
  test: {
    environment: "happy-dom",
  },
});
