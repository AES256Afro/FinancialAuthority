// The README's screenshots: the built app over the synthetic household (backend/scripts/shots_server.py: a
// throwaway data folder, the demo's extras and a small business), each screen on a desktop and a few on a phone,
// written to docs/screenshots/. Nothing real is shown. Build first; `npm run shots` does both.
import { defineConfig } from "@playwright/test";

const port = Number(process.env.FA_SHOTS_PORT ?? 8012);
const local = !process.env.CI;

export default defineConfig({
  testDir: "shots",
  timeout: 300_000,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    ...(local && process.platform === "win32" ? { channel: "msedge" } : {}),
  },
  webServer: {
    command: "uv run --directory ../backend python scripts/shots_server.py",
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: false,
    timeout: 180_000,
    env: { FA_SHOTS_PORT: String(port) },
  },
});
