/// <reference types="vitest/config" />
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The service worker's cache is named for the build (public/sw.js holds the placeholder): a new build's worker
// drops every older cache on activate, so hashed assets of an earlier build are not kept on the phone. The name
// is the entry chunk's content hash, so a rebuild of the same code keeps the same cache. Vite copies public/
// into the output before the bundle is written; the copy is stamped once the bundle is closed.
function stampServiceWorker(): Plugin {
  let outDir = "dist";
  let build = Date.now().toString(36);
  return {
    name: "fa-stamp-service-worker",
    apply: "build",
    configResolved(config) {
      outDir = resolve(config.root, config.build.outDir);
    },
    generateBundle(_options, bundle) {
      const entry = Object.keys(bundle).find((name) => /^assets\/index-[\w-]+\.js$/.test(name));
      if (entry) build = entry.replace(/^assets\/index-|\.js$/g, "");
    },
    closeBundle() {
      const file = resolve(outDir, "sw.js");
      if (!existsSync(file)) return;
      writeFileSync(file, readFileSync(file, "utf8").replace("__FA_BUILD__", build));
    },
  };
}

export default defineConfig({
  // The static demo is published as files beside one page, so its asset paths are relative.
  base: process.env.VITE_STATIC_DEMO === "1" ? "./" : "/",
  plugins: [react(), tailwindcss(), stampServiceWorker()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/mcp": "http://localhost:8000",
    },
  },
  build: {
    outDir: process.env.VITE_STATIC_DEMO === "1" ? "dist-demo" : "dist",
    sourcemap: false,
    // What the first page needs comes as one file. Left to itself the bundler split it into some twenty small shared
    // pieces (an icon, a formatter), and a phone on a slow line fetched them six at a time before the app could start.
    rolldownOptions: { output: { codeSplitting: { groups: [{ name: "app", tags: ["$initial"] }] } } },
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
