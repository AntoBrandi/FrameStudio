import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  globalSetup: "./e2e/global-setup.ts",
  use: { trace: "retain-on-failure" },
  projects: [
    { name: "electron", testMatch: /electron.*\.spec\.ts/ },
    {
      name: "browser",
      testMatch: /browser\.spec\.ts/,
      use: { channel: process.env.FRAMESTUDIO_E2E_CHANNEL || "chrome", headless: true },
    },
  ],
});
