import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const url = process.argv[2] || "http://127.0.0.1:3000";
const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = path.resolve("reports", "usability", timestamp);
fs.mkdirSync(outDir, { recursive: true });

const views = ["Overview", "Pipeline", "Tenants", "Reports", "Alerts", "Governance", "Audit"];
const checks = [];

function note(name, status, detail = "") {
  checks.push({ name, status, detail });
  const marker = status === "pass" ? "PASS" : status === "warn" ? "WARN" : "FAIL";
  console.log(`${marker} ${name}${detail ? ` - ${detail}` : ""}`);
}

function httpCredentials() {
  const username = process.env.ARIE_BASIC_AUTH_USER;
  const password = process.env.ARIE_BASIC_AUTH_PASSWORD;
  return username && password ? { username, password } : undefined;
}

async function waitForApp(page) {
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForLoadState("networkidle", { timeout: 60000 }).catch(() => {});
  await page.getByText("ARIE Command Center").first().waitFor({ timeout: 60000 });
}

async function visibleText(page) {
  return page.locator("body").innerText({ timeout: 15000 });
}

async function checkForRuntimeErrors(page, label) {
  const text = await visibleText(page);
  const badPatterns = [
    /Traceback/i,
    /ModuleNotFoundError/i,
    /ReferenceError/i,
    /TypeError/i,
    /Unhandled Runtime Error/i,
    /Application error/i,
    /Command Center authentication is not configured/i
  ];
  const found = badPatterns.find((pattern) => pattern.test(text));
  if (found) {
    note(`${label} runtime text`, "fail", `Matched ${found}`);
  } else {
    note(`${label} runtime text`, "pass");
  }
  return text;
}

async function clickView(page, name) {
  const button = page.getByRole("button", { name: new RegExp(name, "i") }).first();
  await button.waitFor({ timeout: 30000 });
  await button.click();
  await page.waitForTimeout(700);
}

async function runViewport(browser, viewportName, viewport) {
  const context = await browser.newContext({ viewport, httpCredentials: httpCredentials() });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", (msg) => {
    if (["error", "warning"].includes(msg.type())) {
      consoleErrors.push(`${msg.type()}: ${msg.text()}`);
    }
  });
  page.on("pageerror", (error) => consoleErrors.push(`pageerror: ${error.message}`));

  await waitForApp(page);
  note(`${viewportName} loads`, "pass", url);

  for (const view of views) {
    await clickView(page, view);
    await page.screenshot({
      path: path.join(outDir, `${viewportName}-${view.toLowerCase()}.png`),
      fullPage: true
    });
    const text = await checkForRuntimeErrors(page, `${viewportName} ${view}`);
    note(
      `${viewportName} ${view} visible`,
      text.includes(view) ? "pass" : "warn",
      text.includes(view) ? "" : "View label not found after navigation"
    );
  }

  if (consoleErrors.length) {
    note(`${viewportName} browser console`, "warn", consoleErrors.slice(0, 5).join(" | "));
  } else {
    note(`${viewportName} browser console`, "pass");
  }

  await context.close();
}

const browser = await chromium.launch({ headless: true });
try {
  await runViewport(browser, "desktop", { width: 1440, height: 1000 });
  await runViewport(browser, "mobile", { width: 390, height: 844 });
} finally {
  await browser.close();
}

const failed = checks.filter((check) => check.status === "fail");
const summary = {
  url,
  generated_at: new Date().toISOString(),
  output_dir: outDir,
  pass: checks.filter((check) => check.status === "pass").length,
  warn: checks.filter((check) => check.status === "warn").length,
  fail: failed.length,
  checks
};
fs.writeFileSync(path.join(outDir, "summary.json"), JSON.stringify(summary, null, 2));
console.log(`SUMMARY ${summary.pass} pass, ${summary.warn} warn, ${summary.fail} fail`);
console.log(`ARTIFACTS ${outDir}`);
if (failed.length) {
  process.exit(1);
}
