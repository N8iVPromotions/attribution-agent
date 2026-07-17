import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build output goes into server/public so the Express server serves the SPA
// and the API from a single port (what Replit deployments expect).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../server/public",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:3000",
    },
  },
});
