import { test, expect } from "@playwright/test";
import { readFileSync, mkdirSync } from "node:fs";
const origin = process.env.DELETARR_TEST_URL || "http://127.0.0.1:8980";
const password = "UI-test-only-password-1234";

test("Plex connection sign-in, server selection and responsive settings", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await signIn(page);
  await page.route("**/api/auth/plex/start?connect=true", (route) =>
    route.fulfill({ json: { url: "about:blank" } }),
  );
  await page.route("**/api/auth/plex/poll", (route) =>
    route.fulfill({
      json: {
        pending: false,
        servers: [
          {
            id: "browser-contract-server",
            name: "Test NAS",
            connections: ["http://192.168.1.235:32400"],
          },
        ],
      },
    }),
  );
  let submitted: any;
  await page.route("**/api/integrations/plex/connect", async (route) => {
    submitted = route.request().postDataJSON();
    await route.fulfill({ json: { ok: true } });
  });
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("button", { name: "Settings", exact: true })
    .click();
  await page.getByRole("button", { name: "Plex", exact: true }).click();
  await page
    .getByRole("button", { name: "Sign in with Plex", exact: true })
    .click();
  await expect(page.getByLabel("Your Plex server")).toHaveValue(
    "browser-contract-server",
  );
  await page
    .getByLabel("Explicitly trust this private/LAN integration destination")
    .first()
    .check();
  await expect(
    page.getByLabel("Use this Plex account to sign in to Deletarr"),
  ).toBeChecked();
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.screenshot({
    path: "test-results/plex-connect-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("button", { name: "Connect selected server" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "test-results/plex-connect-mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Connect selected server" }).click();
  await expect(page.getByText("Plex connected", { exact: true })).toBeVisible();
  expect(submitted).toEqual({
    server_id: "browser-contract-server",
    uri: "http://192.168.1.235:32400",
    allow_private: true,
    enable_login: true,
  });
});

async function signIn(page: any) {
  await page.goto("/");
  await expect(page.getByLabel("Username", { exact: true })).toBeVisible();
  const setup = await page.getByLabel("Installation setup token").count();
  await page.getByLabel("Username", { exact: true }).fill("preview-admin");
  await page.getByLabel("Password", { exact: true }).fill(password);
  if (setup)
    await page
      .getByLabel("Installation setup token")
      .fill(
        readFileSync(
          process.env.DELETARR_TEST_CONFIG ||
            "/tmp/deletarr-ui-review/setup-token",
          "utf8",
        ).trim(),
      );
  await page
    .getByRole("button", {
      name: setup ? "Create administrator" : "Sign in",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("heading", { name: "Room for what’s next.", exact: true }),
  ).toBeVisible();
}
test("real application: onboarding, CRUD, safe defaults, desktop and mobile", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await signIn(page);
  const state = await page.request.get("/api/auth/status");
  const csrf = (await state.json()).csrf;
  const settingsResponse = await page.request.get("/api/settings");
  expect(settingsResponse.ok(), await settingsResponse.text()).toBeTruthy();
  const settings = await settingsResponse.json();
  const { dry_run, live_enrolled, ...editable } = settings;
  expect(dry_run).toBe(true);
  await page.request.put("/api/settings", {
    headers: { Origin: origin, "X-CSRF-Token": csrf },
    data: { ...editable, theme: "dark" },
  });
  await page.reload();
  await page.setViewportSize({ width: 1440, height: 1050 });
  await expect(
    page.getByRole("heading", { name: "Storage overview" }),
  ).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  mkdirSync("docs/screenshots", { recursive: true });
  await page.screenshot({
    path: "docs/screenshots/dashboard-desktop.png",
    fullPage: true,
    animations: "disabled",
  });
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("button", { name: "Rules", exact: true })
    .click();
  await page.getByRole("button", { name: "Create rule", exact: true }).click();
  await page
    .getByLabel("Rule name", { exact: true })
    .fill("Preview watched movies");
  await page.getByRole("button", { name: "Save rule", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Preview watched movies" }).last(),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Simulate", exact: true })
    .first()
    .click();
  await expect(
    page.getByText("Preview only. No files are modified."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.screenshot({
    path: "docs/screenshots/rules-desktop.png",
    fullPage: true,
    animations: "disabled",
  });
  for (const name of [
    "Libraries",
    "Cleanup Queue",
    "History",
    "Statistics",
    "Notifications",
    "Settings",
  ]) {
    await page
      .getByRole("navigation", { name: "Main navigation" })
      .getByRole("button", { name, exact: true })
      .click();
    await expect(page.locator("main h1")).toBeVisible();
  }
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("button", { name: "Dashboard", exact: true })
    .click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "Open menu" })).toBeVisible();
  await expect
    .poll(() =>
      page.locator("aside").evaluate((e) => e.getBoundingClientRect().right),
    )
    .toBeLessThanOrEqual(0);
  const toast = page.getByRole("button", {
    name: "Dismiss notification",
    exact: true,
  });
  if (await toast.count()) await toast.click();
  await page.screenshot({
    path: "docs/screenshots/dashboard-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "Open menu" }).click();
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("button", { name: "Settings", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Deletion Safety", exact: true })
    .click();
  await expect(
    page.getByText("Dry-run is active. No files are deleted."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Storage", exact: true }).click();
  await page
    .getByRole("button", { name: "Find mapping suggestions", exact: true })
    .click();
  await expect(
    page.getByText(
      "No verified sample matches. Synchronize Plex metadata first or enter a mapping manually.",
    ),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
