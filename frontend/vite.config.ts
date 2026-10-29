import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";

// https://vite.dev/config/
export default defineConfig({
  server: {
    proxy: {
      "/api/meeting-assistant": {
        target: "http://localhost:8000",
        timeout: 330_000,
        proxyTimeout: 330_000,
      },
      "/proposals": {
        target: "http://localhost:8000",
        timeout: 150_000,
        proxyTimeout: 150_000,
      },
      "/meetings": {
        target: "http://localhost:8000",
        timeout: 360_000,
        proxyTimeout: 360_000,
      },
      "/ws/transcribe": {
        target: "http://localhost:8000",
        ws: true,
      },
    },
  },
  plugins: [react(), tailwindcss()],
});
