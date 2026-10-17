// Browser runs (M47): the walks the owner and a second person take, on the synthetic household, with sign-in on.
// Each runs on a phone and on a desktop; a walk that adds something names it after the screen it ran on.
import { expect, test } from "@playwright/test";
import { noSideways, PASSWORD, signIn } from "./helpers";

test("a wrong password is refused and the right one opens the app", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Username").fill("owner");
  await page.getByLabel("Password", { exact: true }).fill("not the password at all");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText(/password|try again/i).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByLabel("Password", { exact: true })).toHaveCount(0);
});

test("Today leads with safe to spend", async ({ page }) => {
  await signIn(page);
  await page.goto("/today");
  await expect(page.locator("#safe-title")).toContainText(/safe to spend/i);
  await noSideways(page);
});

test("Needs you says what waits, picture first", async ({ page }) => {
  await signIn(page);
  await page.goto("/confirm");
  await expect(page.getByRole("heading", { name: /Needs you/ }).first()).toBeVisible();
  await expect(page.getByText(/waits|Nothing from your mail/).first()).toBeVisible();
  await noSideways(page);
});

test("a second person joins with their own password and sees only what is shared", async ({ page, browser }, info) => {
  await signIn(page);
  await page.goto("/settings/people");
  const username = `partner-${info.project.name}`;
  await page.getByLabel("Username").first().fill(username);
  await page.getByLabel("Their name").fill(`Example Partner ${info.project.name}`);
  await page.getByRole("button", { name: "Add them and make their link" }).click();
  const link = (await page.locator("code").filter({ hasText: "/join" }).first().textContent())?.trim() ?? "";
  expect(link).toContain("/join");
  await noSideways(page);

  const u = info.project.use;
  const theirs = await browser.newContext({ baseURL: u.baseURL, viewport: u.viewport, userAgent: u.userAgent, isMobile: u.isMobile, hasTouch: u.hasTouch, deviceScaleFactor: u.deviceScaleFactor });
  const them = await theirs.newPage();
  await them.goto(link.replace(/^https?:\/\/[^/]+/, u.baseURL ?? ""));
  await them.getByLabel("Password", { exact: true }).fill("plum river lantern seven");
  await them.getByLabel("The same password again").fill("plum river lantern seven");
  await them.getByRole("button", { name: "Set my password and sign in" }).click();
  await expect(them).toHaveURL(/\/shared/);
  await them.goto("/transactions");
  await expect(them).toHaveURL(/\/shared/); // the shared role is fenced to the shared view
  await theirs.close();
});

test("the moving day plan lists its steps", async ({ page }) => {
  await signIn(page);
  // The synthetic household has no move: the walk adds a next home first (once; the other screen finds it there).
  const plan = await (await page.request.get("/api/v1/move/plan")).json();
  if (!plan.move) {
    const on = new Date(Date.now() + 45 * 86_400_000).toISOString().slice(0, 10);
    const made = await page.request.post("/api/v1/housing", { headers: { "X-Requested-With": "FinancialAuthority" }, data: { name: "Example Next Place", payee: "Example Lettings", starts_on: on, due_day: 1, deposit: 300 } });
    expect(made.ok()).toBeTruthy();
  }
  await page.goto("/move");
  await expect(page.getByRole("heading", { name: "Moving day plan" })).toBeVisible();
  await expect(page.getByText(/Move day|moving day/i).first()).toBeVisible();
  await noSideways(page);
});

test("a receipt is added and kept", async ({ page }, info) => {
  await signIn(page);
  await page.goto("/documents");
  await page.getByRole("button", { name: "Add a document", exact: true }).click();
  await page.getByLabel(/^File/).setInputFiles({ name: `receipt-${info.project.name}.txt`, mimeType: "text/plain", buffer: Buffer.from("EXAMPLE HARDWARE\nTOTAL 14.06\n") });
  await page.getByLabel("What it is").selectOption({ label: "Receipt" });
  await page.getByRole("button", { name: /Add the (receipt|document)/ }).click();
  await expect(page.getByText(/Added “/)).toBeVisible();
  await noSideways(page);
});

test("calm shows Today one card at a time", async ({ page }) => {
  await signIn(page);
  await page.goto("/settings/appearance");
  const calm = page.getByLabel(/^Calm: one card at a time/);
  await calm.check();
  await expect(page.getByText(/Calm is on/)).toBeVisible();
  await page.goto("/today");
  await expect(page.getByRole("navigation", { name: "Today's cards" })).toBeVisible();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByText(/card 2 of/)).toBeVisible();
  await noSideways(page);
  await page.goto("/settings/appearance");
  await page.getByLabel(/^Calm: one card at a time/).uncheck();
  await expect(page.getByText(/Calm is off/)).toBeVisible();
});
