import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// During `npm run dev` (outside Docker), proxy /api and /health to the backend container.
// Production builds are served by nginx, which handles routing on its own.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
});
