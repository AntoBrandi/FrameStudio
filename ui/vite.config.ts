import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the browser talks only to this server, which proxies the API,
// so cookies, media and the event stream behave as they do when the backend
// serves the built UI itself.
const backend = process.env.FRAMESTUDIO_BACKEND_URL ?? "http://127.0.0.1:8790";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    host: "localhost",
    port: Number(process.env.FRAMESTUDIO_UI_PORT ?? 5173),
    strictPort: true,
    proxy: {
      "/api": { target: backend, changeOrigin: true },
      "/auth": { target: backend, changeOrigin: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true, sourcemap: true, target: "es2023", chunkSizeWarningLimit: 1500 },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
