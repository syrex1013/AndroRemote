import path from "path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

// Dev-only: forward /api to the C2 web UI (default 127.0.0.1:8888, see --web-port).
// Override with VITE_API_TARGET when the web UI runs elsewhere.
const apiTarget = process.env.VITE_API_TARGET ?? "http://127.0.0.1:8888"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true },
    },
  },
  build: {
    outDir: "../androremote/web/dist",
    emptyOutDir: true,
  },
})
