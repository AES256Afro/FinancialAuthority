import { expect, type Page } from "@playwright/test";

/** The test suite's own password (backend/tests/conftest.py), set by backend/scripts/dev_signin.py; nothing real. */
export const PASSWORD = process.env.FA_E2E_PASSWORD ?? "correct horse battery";

export async function signIn(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByLabel("Username").fill("owner");
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  // Signed in once the form is gone (the button alone turns into "Signing in…" before the answer comes).
  await expect(page.getByLabel("Password", { exact: true })).toHaveCount(0);
}

/** A page never pushes the screen sideways, on a phone or a desktop. */
export async function noSideways(page: Page): Promise<void> {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(over, "the page is wider than the screen").toBeLessThanOrEqual(1);
}
