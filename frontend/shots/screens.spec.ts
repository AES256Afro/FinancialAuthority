// Walks every main screen of the synthetic household and saves a picture of each for the README. The household is
// made up (backend/scripts/shots_server.py); no account, balance or merchant in these pictures is anyone's.
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";

const OUT = join(process.cwd(), "..", "docs", "screenshots");
const DESKTOP = { width: 1280, height: 820 };
const PHONE = { width: 412, height: 892 };

const VIEWS = ["river", "windows", "month", "flow", "envelopes", "board", "transit", "console"];

const SCREENS: { name: string; path: string }[] = [
  { name: "today", path: "/today" },
  { name: "bills", path: "/bills" },
  { name: "borrowing", path: "/borrowing" },
  { name: "debts", path: "/debts" },
  { name: "cover", path: "/cover" },
  { name: "accounts", path: "/accounts" },
  { name: "reports", path: "/reports" },
  { name: "outlook", path: "/outlook" },
  { name: "investments", path: "/investments" },
  { name: "move", path: "/move" },
  { name: "data-check", path: "/data-check" },
  { name: "confirm", path: "/confirm" },
  { name: "mail", path: "/mail" },
  { name: "chat", path: "/chat" },
  { name: "business", path: "/business" },
  { name: "write-offs", path: "/business/write-offs" },
  { name: "invoices", path: "/business/invoices" },
  { name: "miles", path: "/business/miles" },
  { name: "taxes", path: "/taxes" },
  { name: "appearance", path: "/settings/appearance" },
  { name: "connectors", path: "/settings/connectors" },
];

async function appearance(page: Page, change: Record<string, unknown>): Promise<void> {
  const now = await (await page.request.get("/api/v1/appearance")).json();
  const r = await page.request.put("/api/v1/appearance", { data: { ...now, ...change }, headers: { "X-Requested-With": "FinancialAuthority" } });
  expect(r.ok()).toBeTruthy();
}

async function shoot(page: Page, path: string, file: string, picture = false, heading?: string): Promise<void> {
  await page.goto(path);
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(700); // charts settle
  if (picture) {
    // Every home view leads with the one thing today; the view's own picture starts below that card.
    await page.evaluate(() => {
      const main = document.querySelector("main") ?? document.body;
      const label = [...main.querySelectorAll("*")].find((e) => e.children.length === 0 && /^the one thing today$/i.test((e.textContent ?? "").trim()));
      const card = label?.closest("section, .card");
      if (!card) return;
      let scroller: Element | null = card.parentElement;
      while (scroller && !(scroller.scrollHeight > scroller.clientHeight && /(auto|scroll)/.test(getComputedStyle(scroller).overflowY))) scroller = scroller.parentElement;
      (scroller ?? document.scrollingElement ?? document.documentElement).scrollBy(0, card.getBoundingClientRect().bottom + 6);
    });
    await page.waitForTimeout(300);
  }
  if (heading) {
    // On a phone the next steps come first; start the picture at the screen's own heading.
    await page.getByRole("heading", { name: heading }).first().evaluate((e) => e.scrollIntoView({ block: "start" }));
    await page.waitForTimeout(300);
  }
  await page.screenshot({ path: join(OUT, `${file}.png`) });
}

test.beforeAll(async ({ request }) => {
  mkdirSync(OUT, { recursive: true });
  // The server puts its rows in once it answers; the business is the last of them.
  await expect.poll(async () => (await (await request.get("/api/v1/business")).json()).businesses.length, { timeout: 120_000 }).toBe(1);
});

test("desktop screens", async ({ page }) => {
  await page.setViewportSize(DESKTOP);
  await page.emulateMedia({ reducedMotion: "reduce", colorScheme: "light" });
  await appearance(page, { look: "light", active: "river" });
  for (const s of SCREENS) await shoot(page, s.path, s.name);
  for (const v of VIEWS) {
    await appearance(page, { active: v });
    await shoot(page, "/", `view-${v}`, true);
  }
  await appearance(page, { look: "dark", active: "river" });
  await shoot(page, "/", "view-river-dark", true);
  await shoot(page, "/today", "today-dark");
  await appearance(page, { look: "light", active: "river" });
});

test("phone screens", async ({ browser }) => {
  const context = await browser.newContext({ viewport: PHONE, deviceScaleFactor: 2, isMobile: true, hasTouch: true, reducedMotion: "reduce" });
  const page = await context.newPage();
  await appearance(page, { look: "light", active: "river" });
  await shoot(page, "/today", "phone-today");
  await shoot(page, "/borrowing", "phone-borrowing", false, "Your borrowing plan");
  await shoot(page, "/", "phone-river");
  await appearance(page, { look: "dark" });
  await shoot(page, "/today", "phone-today-dark");
  await appearance(page, { look: "light" });
  await context.close();
});
