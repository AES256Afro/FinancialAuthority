/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  // The static demo is published as files beside one page, so its asset paths are relative.
  base: process.env.VITE_STATIC_DEMO === "1" ? "./" : "/",
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/mcp": "http://localhost:8000",
    },
  },
  build: { outDir: process.env.VITE_STATIC_DEMO === "1" ? "dist-demo" : "dist", sourcemap: false },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
