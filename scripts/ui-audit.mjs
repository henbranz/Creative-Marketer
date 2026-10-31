/** Operator-only read-only screenshots. No providers, writes, or external hosts. */
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const modulePath = process.env.PLAYWRIGHT_MODULE;
const { chromium } = await import(
  modulePath ? pathToFileURL(modulePath).href : "playwright"
);
const base = process.env.UI_AUDIT_URL ?? "http://localhost:3000";
const out = process.env.UI_AUDIT_OUTPUT;
assert(out, "Set UI_AUDIT_OUTPUT to a private screenshot directory");
assert(["localhost", "127.0.0.1"].includes(new URL(base).hostname));
const tenantId = process.env.CM_TENANT_ID;
const credential = process.env.CM_API_TOKEN;
const productName = process.env.UI_AUDIT_PRODUCT_NAME;
assert(
  tenantId && credential && productName,
  "Local workspace identity and product name required",
);
const widths = [1440, 1280, 768, 390];
const tabs = [
  "Overview",
  "Brief",
  "Claims",
  "Assets",
  "Research",
  "Creatives",
  "Production",
  "Published",
  "Performance",
  "Insights",
  "Commerce",
];
await mkdir(out, { recursive: true, mode: 0o700 });
const browser = await chromium.launch({ headless: true });
const measurements = [];
const blocked = new Set();
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    reducedMotion: "reduce",
    serviceWorkers: "block",
  });
  await context.route("**/*", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (
      !["localhost", "127.0.0.1"].includes(url.hostname) ||
      !["GET", "HEAD", "OPTIONS"].includes(request.method())
    ) {
      blocked.add(`${request.method()} ${url.pathname}`);
      return route.abort();
    }
    return route.continue();
  });
  const page = await context.newPage();
  const pending = new Set();
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/v1/")) pending.add(r);
  });
  page.on("requestfinished", (r) => pending.delete(r));
  page.on("requestfailed", (r) => pending.delete(r));
  async function settle() {
    // React effects can begin after a click resolves. Require a quiet interval,
    // not merely the document's already-fired load/networkidle event.
    let quiet = 0;
    for (let i = 0; i < 120; i++) {
      await page.waitForTimeout(250);
      quiet = pending.size ? 0 : quiet + 1;
      if (quiet >= 8) {
        await page.evaluate(async () => {
          await document.fonts.ready;
          window.scrollTo(0, 0);
        });
        return;
      }
    }
    throw new Error("UI reads did not settle; capture refused");
  }
  await page.goto(base);
  await page.screenshot({
    path: path.join(out, "access-1440.png"),
    fullPage: true,
  });
  await page.evaluate(
    (session) => sessionStorage.setItem("cm-session", JSON.stringify(session)),
    { tenantId, credential },
  );
  await page.reload();
  await settle();
  await page.screenshot({
    path: path.join(out, "catalog-1440.png"),
    fullPage: true,
  });
  const card = page.locator(".product-card").filter({ hasText: productName });
  assert.equal(
    await card.count(),
    1,
    "Product name must identify exactly one card",
  );
  await card.click();
  await settle();
  for (const width of widths) {
    await page.setViewportSize({ width, height: 1000 });
    for (const tab of tabs) {
      await page
        .locator("nav.tabs")
        .getByRole("button", { name: tab, exact: true })
        .click();
      await settle();
      measurements.push(
        await page.evaluate(
          ({ tab, width }) => {
            const visible = [...document.querySelectorAll("main *")].filter(
              (e) =>
                e.getBoundingClientRect().width > 0 && e.textContent?.trim(),
            );
            return {
              tab,
              width,
              documentWidth: document.documentElement.scrollWidth,
              fontSizes: [
                ...new Set(visible.map((e) => getComputedStyle(e).fontSize)),
              ],
              content: document
                .querySelector(".product-content")
                ?.getBoundingClientRect()
                .toJSON(),
              answer: document
                .querySelector(".brief-card textarea")
                ?.getBoundingClientRect()
                .toJSON(),
              currentTabBorder: getComputedStyle(
                document.querySelector(".tabs button.active"),
              ).borderImageSource,
              clippedAssetControls: [
                ...document.querySelectorAll(".asset-card > button"),
              ].filter((button) => {
                const bounds = button.getBoundingClientRect();
                const card = button.parentElement.getBoundingClientRect();
                return bounds.left < card.left || bounds.right > card.right;
              }).length,
            };
          },
          { tab, width },
        ),
      );
      await page.screenshot({
        path: path.join(out, `${tab.toLowerCase()}-${width}.png`),
        fullPage: true,
      });
      if (process.env.UI_AUDIT_ASSERT_FOUNDATION === "1") {
        const current = measurements.at(-1);
        assert(
          current.documentWidth <= width,
          `${tab}: document overflow at ${width}`,
        );
        assert(
          current.fontSizes.every((size) => ["16px", "24px"].includes(size)),
          `${tab}: unexpected type size`,
        );
        assert.equal(
          current.currentTabBorder,
          "none",
          `${tab}: legacy gradient tab border`,
        );
        assert.equal(
          current.clippedAssetControls,
          0,
          `${tab}: clipped Asset actions`,
        );
      }
    }
  }
  await writeFile(
    path.join(out, "measurements.json"),
    JSON.stringify({ measurements, blocked: [...blocked] }, null, 2),
    { mode: 0o600 },
  );
  console.log(
    "Read-only capture complete. Screenshots contain private product content; review before sharing.",
  );
} finally {
  await browser.close();
}
