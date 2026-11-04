/** Local browser QA. GET-only backend access; preview POSTs are fulfilled fixtures. */
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE
    ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
    : "playwright"
);
const base = process.env.UI_AUDIT_URL ?? "http://localhost:3000";
const out = process.env.UI_AUDIT_OUTPUT;
const productName = process.env.UI_AUDIT_PRODUCT_NAME;
const tenantId = process.env.CM_TENANT_ID;
const credential = process.env.CM_API_TOKEN;
assert(
  out && productName && tenantId && credential,
  "Private output and local workspace context required",
);
assert(["localhost", "127.0.0.1"].includes(new URL(base).hostname));
await mkdir(out, { recursive: true, mode: 0o700 });
const fixtureId = "00000000-0000-4000-8000-000000000099";
const fixtureArt = await readFile(
  "apps/web/public/brand/creative-manager-mark.svg",
  "utf8",
);
const blocked = [];
let mockedGrants = 0;
let failGrants = true;
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    reducedMotion: "reduce",
    serviceWorkers: "block",
  });
  await context.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const local = ["localhost", "127.0.0.1"].includes(url.hostname);
    if (
      local &&
      request.method() === "GET" &&
      /^\/v1\/products\/[^/]+\/assets$/.test(url.pathname)
    ) {
      return route.fulfill({
        json: [
          {
            id: fixtureId,
            kind: "image",
            original_filename: "TEST FIXTURE — vector preview.svg",
            role: "other",
            status: "ready",
            can_edit: false,
            byte_size: 2048,
            width: 200,
            height: 180,
          },
        ],
      });
    }
    if (
      local &&
      request.method() === "POST" &&
      url.pathname === `/v1/assets/${fixtureId}/download`
    ) {
      mockedGrants++;
      if (failGrants)
        return route.fulfill({
          status: 503,
          json: { detail: "fixture unavailable" },
        });
      return route.fulfill({
        json: {
          url: `${base}/__coastal-fixture.svg`,
          expires_at: new Date(Date.now() + 60000).toISOString(),
        },
      });
    }
    if (
      local &&
      request.method() === "GET" &&
      url.pathname === "/__coastal-fixture.svg"
    ) {
      return route.fulfill({ contentType: "image/svg+xml", body: fixtureArt });
    }
    if (!local || !["GET", "HEAD", "OPTIONS"].includes(request.method())) {
      blocked.push(`${request.method()} ${url.pathname}`);
      return route.abort();
    }
    return route.continue();
  });
  const page = await context.newPage();
  await page.goto(base);
  await page.evaluate(
    (session) => sessionStorage.setItem("cm-session", JSON.stringify(session)),
    { tenantId, credential },
  );
  await page.reload();
  await page.locator(".product-card").filter({ hasText: productName }).click();
  await page
    .locator("nav.tabs")
    .getByRole("button", { name: "Assets", exact: true })
    .click();
  const preview = page.getByRole("button", { name: "Preview", exact: true });
  await preview.click();
  const dialog = page.getByRole("dialog", { name: "Asset details" });
  await dialog.getByRole("alert").waitFor();
  await page.screenshot({
    path: path.join(out, "asset-retry-1440.png"),
    fullPage: false,
  });
  failGrants = false;
  await dialog.getByRole("button", { name: "Retry", exact: true }).click();
  await dialog.getByRole("img").evaluate(async (img) => {
    await img.decode();
  });
  await page.screenshot({
    path: path.join(out, "asset-drawer-1440.png"),
    fullPage: false,
  });
  const close = dialog.getByRole("button", { name: "Close Asset details" });
  await close.focus();
  await page.keyboard.press("Tab");
  assert(
    await close.evaluate((e) => document.activeElement === e),
    "Native dialog must contain focus",
  );
  await page.keyboard.press("Escape");
  assert.equal(await dialog.count(), 0);
  assert(
    await preview.evaluate((e) => document.activeElement === e),
    "Return focus to Preview",
  );

  await page
    .getByRole("button", { name: "New product", exact: true })
    .first()
    .click();
  const create = page.getByRole("dialog");
  await create.waitFor();
  assert(
    await create
      .locator("[data-initial-focus]")
      .evaluate((e) => document.activeElement === e),
  );
  await page.keyboard.press("Escape");

  await page
    .locator("nav.tabs")
    .getByRole("button", { name: "Creatives", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Inspect concept", exact: true })
    .first()
    .click();
  await page.getByRole("dialog", { name: "Creative concept detail" }).waitFor();
  await page.screenshot({
    path: path.join(out, "concept-drawer-1440.png"),
    fullPage: false,
  });
  await page.keyboard.press("Escape");

  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .locator("nav.tabs")
    .getByRole("button", { name: "Assets", exact: true })
    .click();
  await preview.click();
  await dialog.getByRole("img").evaluate(async (img) => {
    await img.decode();
  });
  assert((await dialog.boundingBox()).width <= 390);
  assert.equal(
    (await dialog.boundingBox()).y,
    0,
    "Drawer starts at viewport top",
  );
  assert(
    await dialog.evaluate((e) => e.scrollWidth <= e.clientWidth),
    "No drawer overflow",
  );
  await page.screenshot({
    path: path.join(out, "asset-drawer-390.png"),
    fullPage: false,
  });
  await page.keyboard.press("Escape");
  // React development StrictMode may replay initial read effects on mount.
  assert(mockedGrants >= 3 && mockedGrants <= 5);
  assert.deepEqual(
    blocked,
    [],
    "Unexpected non-read requests; inspect before publishing QA",
  );
  await writeFile(
    path.join(out, "interactive-results.json"),
    JSON.stringify(
      {
        mockedGrants,
        actualPreviewPosts: 0,
        blocked,
        passed: [
          "retry",
          "original preview",
          "focus containment",
          "focus restoration",
          "Escape",
          "create initial focus",
          "concept drawer",
          "mobile drawer",
        ],
      },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  console.log(
    "Coastal interaction QA passed. Preview requests were mock-only; captures remain private.",
  );
} finally {
  await browser.close();
}
