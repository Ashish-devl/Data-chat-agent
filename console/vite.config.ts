import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // Relative asset paths: the same build works at /console on any server, or on its own site.
  base: "./",
  resolve: {
    alias: {
      "datachat-agent": fileURLToPath(new URL("../sdk-js/src/index.ts", import.meta.url)),
    },
  },
  server: { proxy: { "/v1": "http://127.0.0.1:8000" } },
  test: { environment: "happy-dom" },
});
