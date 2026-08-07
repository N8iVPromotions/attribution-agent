import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const url = process.argv[2] || "http://127.0.0.1:8501";
const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = path.resolve("reports", "usability", timestamp);
fs.mkdirSync(outDir, { recursive: true });

const tabs = ["Pipeline", "Clients", "Outreach", "Observability"];
const checks = [];

function note(name, status, detail = "") {
  checks.push({ name, status, detail });
  const marker = status === "pass" ? "PASS" : status === "warn" ? "WARN" : "FAIL";
  console.log(`${marker} ${name}${detail ? ` - ${detail}` : ""}`);
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
    /NameError/i,
    /KeyError/i,
    /TypeError/i,
    /StreamlitAPIException/i,
    /Observability data unavailable/i,
  ];
  const found = badPatterns.find((pattern) => pattern.test(text));
  if (found) {
    note(`${label} runtime text`, "fail", `Matched ${found}`);
  } else {
    note(`${label} runtime text`, "pass");
  }
  return text;
}

async function clickTab(page, name) {
  const tab = page.getByRole("tab", { name });
  await tab.waitFor({ timeout: 30000 });
  await tab.click();
  await page.waitForTimeout(1200);
}

async function runViewport(browser, viewportName, viewport) {
  const context = await browser.newContext({ viewport });
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
  await page.screenshot({
    path: path.join(outDir, `${viewportName}-pipeline.png`),
    fullPage: true,
  });
  await checkForRuntimeErrors(page, `${viewportName} Pipeline`);
  const recentRuns = page.getByRole("button", { name: /Recent runs/i });
  if (await recentRuns.count()) {
    await recentRuns.first().click();
    await page.waitForTimeout(1000);
    await checkForRuntimeErrors(page, `${viewportName} Recent runs interaction`);
    note(`${viewportName} Recent runs button`, "pass");
  } else {
    note(`${viewportName} Recent runs button`, "warn", "Button not found");
  }

  for (const tabName of tabs) {
    await clickTab(page, tabName);
    await page.screenshot({
      path: path.join(outDir, `${viewportName}-${tabName.toLowerCase()}.png`),
      fullPage: true,
    });
    const text = await checkForRuntimeErrors(page, `${viewportName} ${tabName}`);
    if (!text.includes(tabName) && tabName !== "Pipeline") {
      note(`${viewportName} ${tabName} label`, "warn", "Tab name not visible after click");
    } else {
      note(`${viewportName} ${tabName} label`, "pass");
    }
  }

  await clickTab(page, "Clients");
  const businessName = page.getByLabel("Business name");
  if (await businessName.count()) {
    await businessName.first().fill("Usability Test Company");
    await checkForRuntimeErrors(page, `${viewportName} client form fill`);
    note(`${viewportName} client form fill`, "pass");
  } else {
    note(`${viewportName} client form fill`, "warn", "Business name input not found");
  }

  await clickTab(page, "Outreach");
  const prospectsText = await visibleText(page);
  if (prospectsText.includes("Alani Skin MD") && prospectsText.includes("Generate 3-Email Sequence")) {
    note(`${viewportName} outreach prospect selector`, "pass");
  } else {
    note(`${viewportName} outreach prospect selector`, "warn", "Expected outreach controls not visible");
  }

  const bodyText = await visibleText(page);
  for (const expected of ["Pipeline", "Clients", "Outreach", "Observability"]) {
    note(
      `${viewportName} has ${expected} navigation`,
      bodyText.includes(expected) ? "pass" : "fail"
    );
  }
  if (consoleErrors.length) {
    note(
      `${viewportName} browser console`,
      "warn",
      consoleErrors.slice(0, 5).join(" | ")
    );
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
  checks,
};
fs.writeFileSync(path.join(outDir, "summary.json"), JSON.stringify(summary, null, 2));
console.log(`SUMMARY ${summary.pass} pass, ${summary.warn} warn, ${summary.fail} fail`);
console.log(`ARTIFACTS ${outDir}`);
if (failed.length) {
  process.exit(1);
}
