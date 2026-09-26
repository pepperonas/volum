import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwind from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";

// The dev server port is pinned because Tauri's webview must know where to look,
// and because the engine's CORS allow-list names this exact origin.
const DEV_PORT = 1420;

export default defineConfig({
  plugins: [react(), tailwind()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  // Vite's default host binds every interface. The desktop shell talks to
  // localhost only, and a dev server on the network is an open door.
  server: { port: DEV_PORT, strictPort: true, host: "127.0.0.1" },
  build: { target: "es2022", sourcemap: true },
  test: {
    // Pure logic only. A component test that needs a DOM declares it per file
    // with `// @vitest-environment happy-dom`, which keeps the default fast.
    environment: "node",
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
