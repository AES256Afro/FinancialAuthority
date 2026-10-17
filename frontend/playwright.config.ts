// Browser runs (M47): the built app, served by the backend with sign-in on and the synthetic household
// (backend/scripts/dev_signin.py, a throwaway data folder, the test suite's own password). Each walk runs on a
// phone and on a desktop screen. Build the frontend first (npm run build), then: npm run e2e
// On Windows it drives the Edge already installed; in CI it drives the Chromium Playwright installs.
import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.FA_E2E_PORT ?? 8011);
const local = !process.env.CI;

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  retries: process.env.CI ? 1 : 0,
  workers: 1, // one server, one household: walks that change it run one after another
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: "retain-on-failure",
    ...(local && process.platform === "win32" ? { channel: "msedge" } : {}),
  },
  projects: [
    { name: "phone", use: { ...devices["Pixel 7"], ...(local && process.platform === "win32" ? { channel: "msedge" } : {}) } },
    { name: "desktop", use: { viewport: { width: 1280, height: 900 } } },
  ],
  webServer: {
    command: "uv run --directory ../backend python scripts/dev_signin.py",
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: { FA_DEV_PORT: String(port) },
  },
});
