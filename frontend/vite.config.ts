import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5180,
    strictPort: true,
    // The console today is served by the gateway (FastAPI) on :8080, which also
    // proxies /api/* to every backend service - see services/gateway/app/main.py.
    // Proxying the same way here means this dev server speaks to the real running
    // services with no CORS setup and no separate "point this at localhost:8081"
    // config scattered through the code - one base URL, /api, same as production.
    // Defaults to run_local.ps1's gateway port. Set FRAUD360_GATEWAY_URL to point at a
    // gateway running elsewhere (another port, or a shared dev server).
    proxy: {
      "/api": {
        target: process.env.FRAUD360_GATEWAY_URL ?? "http://127.0.0.1:8080",
        changeOrigin: true,
      },
    },
  },
})
