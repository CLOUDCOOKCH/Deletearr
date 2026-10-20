import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests/e2e",
  workers: 1,
  fullyParallel: false,
  timeout: 30000,
  use: {
    baseURL: process.env.DELETARR_TEST_URL || "http://127.0.0.1:8980",
    browserName: "chromium",
    launchOptions: {
      executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
      args: ["--no-sandbox", "--disable-dev-shm-usage"],
    },
    trace: "retain-on-failure",
  },
  reporter: [["list"]],
  outputDir: "test-results",
});
