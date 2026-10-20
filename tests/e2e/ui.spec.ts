import { test, expect } from "@playwright/test";
import { readFileSync, mkdirSync } from "node:fs";
const origin = process.env.DELETARR_TEST_URL || "http://127.0.0.1:8980";
const password = "UI-test-only-password-1234";

test("anime themes persist, remain dark under a light system preference, and preserve safety settings", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce", colorScheme: "light" });
  await page.setViewportSize({ width: 1440, height: 1050 });
  await signIn(page);
  const initial = await (await page.request.get("/api/settings")).json();
  for (const [value, name, background, accent] of [
    ["one-piece", "One Piece", "rgb(11, 22, 35)", "#f4c56a"],
    ["black-clover", "Black Clover", "rgb(11, 20, 18)", "#91e0ac"],
    ["highschool-dxd", "High School DxD", "rgb(24, 14, 25)", "#ef9fbe"],
  ]) {
    await page
      .getByRole("navigation", { name: "Main navigation" })
      .getByRole("button", { name: "Settings", exact: true })
      .click();
    await page.getByRole("button", { name: "Appearance", exact: true }).click();
    await page.getByRole("radio", { name: new RegExp(name) }).check();
    const saved = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/settings") && r.request().method() === "PUT",
    );
    await page
      .getByRole("button", { name: "Save settings", exact: true })
      .click();
    expect((await saved).ok()).toBeTruthy();
    await expect(page.locator("html")).toHaveAttribute("data-theme", value);
    await expect(page.locator("html")).toHaveCSS(
      "background-color",
      background,
    );
    const contrast = await page.evaluate(() => {
      const style = getComputedStyle(document.documentElement);
      const luminance = (hex: string) => {
        const channels = [1, 3, 5]
          .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
          .map((v) =>
            v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4,
          );
        return (
          channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722
        );
      };
      const ratio = (a: string, b: string) => {
        const x = luminance(a),
          y = luminance(b);
        return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
      };
      const token = (name: string) => style.getPropertyValue(name).trim();
      return {
        muted: ratio(token("--muted"), token("--surface")),
        body: ratio(token("--text"), token("--surface")),
        button: ratio(token("--accent-text"), token("--accent")),
        accent: token("--accent"),
        scheme: style.colorScheme,
      };
    });
    expect(contrast.muted).toBeGreaterThanOrEqual(4.5);
    expect(contrast.body).toBeGreaterThanOrEqual(4.5);
    expect(contrast.button).toBeGreaterThanOrEqual(4.5);
    expect(contrast.accent).toBe(accent);
    expect(contrast.scheme).toBe("dark");
    await expect
      .poll(() =>
        page
          .locator(".theme-preview img")
          .evaluateAll((images) =>
            images.every(
              (image) =>
                (image as HTMLImageElement).complete &&
                (image as HTMLImageElement).naturalWidth > 0,
            ),
          ),
      )
      .toBeTruthy();
    await page.getByRole("button", { name: "Dismiss notification" }).click();
    await page.screenshot({
      path: `test-results/theme-${value}-desktop.png`,
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect
      .poll(() =>
        page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      )
      .toBeTruthy();
    await page.screenshot({
      path: `test-results/theme-${value}-mobile.png`,
      fullPage: true,
    });
    await page.setViewportSize({ width: 1440, height: 1050 });
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", value);
    const settings = await (await page.request.get("/api/settings")).json();
    expect(settings.dry_run).toBe(initial.dry_run);
    expect(settings.live_enrolled).toBe(initial.live_enrolled);
    expect(settings.accent).toBe(initial.accent);
  }
});

test("cleanup queue displays posters, watch dates and missing-history states", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce", colorScheme: "dark" });
  await signIn(page);
  await page.route("**/api/candidates?*", (route) =>
    route.fulfill({
      json: {
        total: 2,
        items: [
          {
            id: "movie",
            media_id: "movie",
            title: "Queue movie",
            kind: "movie",
            library: "Movies",
            rule_name: "Watched 90 days",
            state: "PendingApproval",
            version: 1,
            files: [],
            logical_bytes: 0,
            reason: "{}",
            last_watched: 1751328000,
            history_visible: true,
            poster: "/api/media/movie/poster",
          },
          {
            id: "show",
            media_id: "show",
            title: "Queue series",
            kind: "show",
            library: "TV",
            rule_name: "Series rule",
            state: "PendingApproval",
            version: 1,
            files: [],
            logical_bytes: 0,
            reason: "{}",
            last_watched: null,
            history_visible: false,
            poster: "/api/media/show/poster",
          },
        ],
      },
    }),
  );
  await page.route("**/api/media/movie/poster", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="240"><rect width="160" height="240" fill="#455c40"/><text x="80" y="120" text-anchor="middle" fill="white" font-size="16">Poster fixture</text></svg>',
    }),
  );
  await page.route("**/api/media/show/poster", (route) =>
    route.fulfill({ status: 404 }),
  );
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("button", { name: "Cleanup Queue", exact: true })
    .click();
  await expect(page.getByAltText("Queue movie poster")).toBeVisible();
  await expect(page.getByText(/Last watched:.*2025/)).toBeVisible();
  await expect(
    page.getByText("Last watched: Unknown · History incomplete"),
  ).toBeVisible();
  await expect(page.getByLabel("Poster unavailable")).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.screenshot({
    path: "test-results/queue-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByAltText("Queue movie poster")).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBeTruthy();
  await page.screenshot({
    path: "test-results/queue-mobile.png",
    fullPage: true,
  });
  // Secondary decisions stay available in the compact action disclosure.
  const decisions: string[] = [];
  await page.route("**/api/candidates/movie/action", (route) => {
    const body = route.request().postDataJSON();
    expect(body.version).toBe(1);
    expect(body.days).toBe(7);
    decisions.push(body.action);
    return route.fulfill({ json: { ok: true } });
  });
  const movie = page.locator(".candidate").first();
  await movie.getByRole("button", { name: "Approve", exact: true }).click();
  await movie.getByText("More actions", { exact: true }).click();
  for (const name of ["Postpone 7 days", "Keep Forever", "Reject"]) {
    await movie.getByRole("button", { name, exact: true }).click();
  }
  await expect
    .poll(() => decisions)
    .toEqual(["approve", "postpone", "keep", "reject"]);
});

test("live-mode checkbox replaces typed phrase and errors remain visible", async ({
  page,
}) => {
  await signIn(page);
  let attempts = 0;
  await page.route("**/api/settings/live", (route) => {
    expect(route.request().postDataJSON()).toEqual({
      password,
      acknowledge_permanent_deletion: true,
    });
    attempts++;
    return route.fulfill(
      attempts === 1
        ? { status: 409, json: { error: "Administrator password invalid" } }
        : { json: { dry_run: false } },
    );
  });
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("button", { name: "Settings", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Deletion Safety", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Enable live deletion…", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByText("Type the full confirmation phrase"),
  ).toHaveCount(0);
  await dialog.getByLabel("Administrator password").fill(password);
  await dialog.getByRole("checkbox").check();
  await dialog
    .getByRole("button", { name: "Enable live deletion", exact: true })
    .click();
  await expect(
    dialog.getByText("Administrator password invalid"),
  ).toBeVisible();
  await dialog
    .getByRole("button", { name: "Enable live deletion", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
});

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
            connections: ["http://192.0.2.10:32400"],
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
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBeTruthy();
  await page.screenshot({
    path: "test-results/plex-connect-mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Connect selected server" }).click();
  await expect(page.getByText("Plex connected", { exact: true })).toBeVisible();
  expect(submitted).toEqual({
    server_id: "browser-contract-server",
    uri: "http://192.0.2.10:32400",
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
    page.getByRole("heading", { name: "Overview", exact: true }),
  ).toBeVisible();
}
test("real application: onboarding, CRUD, safe defaults, desktop and mobile", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await signIn(page);
  const currentRoute = page.url();
  await page.getByRole("link", { name: "Skip to content" }).focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(currentRoute);
  await expect(page.locator("main")).toBeFocused();
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
  await page
    .locator(".dashboard-disclosure > summary")
    .filter({ hasText: "Connections" })
    .click();
  await expect(
    page.getByRole("region", { name: "Integration status" }),
  ).toBeVisible();
  await page
    .locator(".dashboard-disclosure > summary")
    .filter({ hasText: "Connections" })
    .click();
  await expect(
    page.getByRole("region", { name: "Integration status" }),
  ).toBeHidden();
  await page.getByText("Library activity", { exact: false }).click();
  await expect(
    page.getByRole("heading", { name: "Recently watched" }),
  ).toBeVisible();
  await page.getByText("Library activity", { exact: false }).click();
  await page.evaluate(() => document.fonts.ready);
  mkdirSync("docs/screenshots", { recursive: true });
  await page.screenshot({
    path: "docs/screenshots/dashboard-desktop.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.evaluate(() => (document.documentElement.dataset.theme = "light"));
  await expect(page.locator("html")).toHaveCSS(
    "background-color",
    "rgb(244, 245, 247)",
  );
  await page.screenshot({
    path: "docs/screenshots/dashboard-light.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.evaluate(() => (document.documentElement.dataset.theme = "dark"));
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
    .getByLabel("Settings section", { exact: true })
    .selectOption("Deletion Safety");
  await expect(
    page.getByText("Dry-run is active. No files are deleted."),
  ).toBeVisible();
  await page
    .getByLabel("Settings section", { exact: true })
    .selectOption("Storage");
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

test("reconnect recovers from an initial authentication-status outage", async ({
  page,
}) => {
  let first = true;
  await page.route("**/api/auth/status", (route) => {
    if (first) {
      first = false;
      return route.fulfill({
        status: 503,
        json: { error: "Temporary sign-in outage" },
      });
    }
    return route.continue();
  });
  await page.goto("/");
  await expect(page.getByRole("alert")).toHaveText("Temporary sign-in outage");
  await page.getByRole("button", { name: "Reconnect", exact: true }).click();
  await expect(page.getByLabel("Username", { exact: true })).toBeVisible();
  await expect(page.getByText("Temporary sign-in outage")).toHaveCount(0);
});

test("dashboard polling clears a recovered request error", async ({ page }) => {
  await signIn(page);
  let requests = 0;
  await page.route("**/api/dashboard", (route) => {
    requests++;
    return requests === 1
      ? route.fulfill({
          status: 503,
          json: { error: "Temporary dashboard outage" },
        })
      : route.continue();
  });
  await page.reload();
  await expect(page.getByRole("alert")).toHaveText(
    "Temporary dashboard outage",
  );
  await expect(
    page.getByRole("heading", { name: "Overview", exact: true }),
  ).toBeVisible({ timeout: 12000 });
  await expect(page.getByText("Temporary dashboard outage")).toHaveCount(0);
});
