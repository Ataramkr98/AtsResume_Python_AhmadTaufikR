import { createRequire } from "node:module";
import { mkdirSync, existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

// Playwright is resolved from wherever it happens to be installed (npx cache by default) so the
// project itself never gains a heavy devDependency it does not need at build time. Discovery is
// dynamic rather than hardcoded so the script runs on any machine and any OS.
const require = createRequire(import.meta.url);

function resolvePlaywright() {
  if (process.env.PLAYWRIGHT_PATH) return process.env.PLAYWRIGHT_PATH;
  const candidates = [];
  if (process.platform === "win32") {
    const localAppData = process.env.LOCALAPPDATA || join(homedir(), "AppData", "Local");
    const npxCache = join(localAppData, "npm-cache", "_npx");
    if (existsSync(npxCache)) {
      for (const entry of readdirSync(npxCache)) {
        candidates.push(join(npxCache, entry, "node_modules", "playwright"));
      }
    }
  } else {
    candidates.push(join(homedir(), ".npm", "_npx"));
  }
  candidates.push("playwright"); // a local install, if one exists
  for (const candidate of candidates) {
    try {
      require.resolve(candidate);
      return candidate;
    } catch {
      // Keep looking; an unresolvable candidate is simply not the right one.
    }
  }
  throw new Error(
    "Playwright not found. Install it (npx playwright install chromium) or set PLAYWRIGHT_PATH."
  );
}

const { chromium } = require(resolvePlaywright());

// The cached Playwright build and the downloaded Chromium build can drift apart, so prefer a
// browser that is actually on disk over the exact revision this Playwright release expects.
function resolveChromium() {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  const roots =
    process.platform === "win32"
      ? [join(process.env.LOCALAPPDATA || join(homedir(), "AppData", "Local"), "ms-playwright")]
      : [join(homedir(), ".cache", "ms-playwright")];
  const relative = [
    ["chrome-win64", "chrome.exe"],
    ["chrome-win", "chrome.exe"],
    ["chrome-linux", "chrome"],
    ["chrome-mac", "Chromium.app", "Contents", "MacOS", "Chromium"],
  ];
  for (const root of roots) {
    if (!existsSync(root)) continue;
    for (const dir of readdirSync(root).filter((d) => d.startsWith("chromium")).sort().reverse()) {
      for (const parts of relative) {
        const candidate = join(root, dir, ...parts);
        if (existsSync(candidate)) return candidate;
      }
    }
  }
  return undefined; // fall back to Playwright's own bundled browser
}

const BASE = process.env.BASE_URL || "http://127.0.0.1:8000";
const ORG = process.env.ORG_SLUG || "northstar-labs";
const OUT = "docs/screenshots";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ executablePath: resolveChromium() });

async function signIn(page) {
  await page.goto(`${BASE}/accounts/login/`, { waitUntil: "networkidle" });
  await page.fill("#id_username", "demo@example.com");
  await page.fill("#id_password", "demo123");
  await Promise.all([
    page.waitForURL((url) => !url.pathname.includes("/accounts/login/"), { timeout: 20000 }),
    page.click('button[type="submit"]'),
  ]);
}

async function shoot(page, path, name, { full = true, wait = 900 } = {}) {
  await page.goto(`${BASE}${path}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(wait); // let Chart.js and Lucide finish painting
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: full });
  console.log(`  saved ${name}.png  <- ${path}`);
}

// ---------------------------------------------------------------- desktop
const desktop = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 1,
});
const page = await desktop.newPage();
const errors = [];
page.on("console", (msg) => {
  if (msg.type() === "error") errors.push(msg.text());
});
page.on("pageerror", (err) => errors.push(String(err)));
page.on("response", (res) => {
  if (res.status() >= 400) errors.push(`HTTP ${res.status()} ${res.url()}`);
});

await shoot(page, "/", "landing", { full: false });
await signIn(page);
console.log("  signed in as demo@example.com");

await shoot(page, `/app/${ORG}/`, "dashboard", { full: false });
await shoot(page, `/app/${ORG}/jobs/`, "jobs", { full: false });
await shoot(page, `/app/${ORG}/pipeline/`, "pipeline", { full: false });
await shoot(page, `/app/${ORG}/interviews/`, "interviews", { full: false });
await shoot(page, `/app/${ORG}/tasks/`, "tasks", { full: false });
await shoot(page, `/app/${ORG}/reports/`, "reports");
await shoot(page, `/app/${ORG}/audit/`, "audit", { full: false });
await shoot(page, `/app/${ORG}/profile/`, "profile", { full: false });

// Candidate detail: click the first row of the results table (not the sidebar nav link).
await page.goto(`${BASE}/app/${ORG}/candidates/`, { waitUntil: "networkidle" });
await page.waitForTimeout(500);
await page.screenshot({ path: `${OUT}/candidates.png`, fullPage: false });
console.log("  saved candidates.png");
const firstCandidate = page.locator('tbody a[href*="/candidates/"]').first();
await Promise.all([page.waitForLoadState("networkidle"), firstCandidate.click()]);
await page.waitForTimeout(700);
await page.screenshot({ path: `${OUT}/candidate-detail.png`, fullPage: true });
console.log(`  saved candidate-detail.png  <- ${page.url().replace(BASE, "")}`);

// ---------------------------------------------------------------- mobile
// A separate context means a separate cookie jar, so the mobile viewport has to sign in too.
const mobile = await browser.newContext({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 2,
  isMobile: true,
  hasTouch: true,
});
const mpage = await mobile.newPage();
await mpage.goto(`${BASE}/accounts/login/`, { waitUntil: "networkidle" });
await mpage.screenshot({ path: `${OUT}/mobile-login.png`, fullPage: false });
console.log("  saved mobile-login.png");
await signIn(mpage);
await shoot(mpage, `/app/${ORG}/`, "mobile-dashboard", { full: false });
await shoot(mpage, `/app/${ORG}/pipeline/`, "mobile-pipeline", { full: false });
await shoot(mpage, `/app/${ORG}/candidates/`, "mobile-candidates", { full: false });

await mpage.goto(`${BASE}/app/${ORG}/`, { waitUntil: "networkidle" });
await mpage.waitForTimeout(600);
const toggle = mpage
  .locator('button[data-mobile-nav-toggle], button[aria-controls], [aria-label*="enu" i]')
  .first();
if (await toggle.count()) {
  await toggle.click();
  await mpage.waitForTimeout(600);
  await mpage.screenshot({ path: `${OUT}/mobile-navigation.png`, fullPage: false });
  console.log("  saved mobile-navigation.png");
} else {
  console.log("  !! no mobile navigation toggle found");
}

await browser.close();

if (errors.length) {
  console.log("\nBrowser console errors:");
  for (const e of [...new Set(errors)]) console.log("  ! " + e);
  process.exit(1);
}
console.log("\nNo browser console errors.");
