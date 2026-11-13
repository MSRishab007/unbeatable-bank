import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";

// The app is served by the gateway from services/gateway/static, next to the classic UI in
// static/classic. `npm run build` writes there; `npm run dev` proxies /api to a local gateway.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "/",
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: {
    outDir: "../services/gateway/static",
    emptyOutDir: false,          // static/classic and static/vendor live there too
    assetsDir: "assets",
    assetsInlineLimit: 0,        // the CSP allows fonts and images only from this origin, not data: URIs
    chunkSizeWarningLimit: 900,
    minify: process.env.NOMINIFY ? false : "esbuild",
  },
  server: { proxy: { "/api": "http://localhost:8080", "/classic": "http://localhost:8080" } },
});
