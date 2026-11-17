import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";

// The app is served by the gateway from services/gateway/static, next to the classic UI in
// static/classic. `npm run build` writes there; `npm run dev` proxies /api to a local gateway.
// `--mode demo` (npm run demo, demo:build) is the offline demo: it bundles the mock bank and
// writes to web/demo-dist with relative paths, so it never touches the gateway's files.
export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss()],
  base: mode === "demo" ? "./" : "/",
  // Demo mode turns the mock bank on (the same switch as running with VITE_MOCK=1).
  define: mode === "demo" ? { "import.meta.env.VITE_MOCK": JSON.stringify("1") } : {},
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: {
    outDir: mode === "demo" ? "demo-dist" : "../services/gateway/static",
    emptyOutDir: mode === "demo",          // static/classic and static/vendor live there too
    assetsDir: "assets",
    assetsInlineLimit: 0,        // the CSP allows fonts and images only from this origin, not data: URIs
    chunkSizeWarningLimit: 900,
    minify: process.env.NOMINIFY ? false : "esbuild",
  },
  server: { proxy: { "/api": "http://localhost:8080", "/classic": "http://localhost:8080" } },
}));
