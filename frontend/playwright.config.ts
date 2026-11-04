import { defineConfig } from "@playwright/test";
import path from "node:path";

const python = process.env.E2E_PYTHON ?? path.resolve(process.cwd(), "..", ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const webPort = process.env.E2E_WEB_PORT ?? "4173";

export default defineConfig({
  testDir: "./e2e",
  // Live mode runs only the one real Groq test, to save quota. Normal runs never call real Groq.
  ...(process.env.E2E_LIVE_GROQ === "1" ? { testMatch: /live\.spec\.ts/ } : { testIgnore: /live\.spec\.ts/ }),
  // Tests share one seeded database and one embedding worker, so they run one at a time.
  workers: 1,
  fullyParallel: false,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  retries: 0,
  reporter: [["list"], ["json", { outputFile: "test-results/results.json" }]],
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: `"${python}" e2e/stack/run_stack.py`,
    url: `http://127.0.0.1:${webPort}/`,
    timeout: 300_000,
    reuseExistingServer: false,
    stdout: "pipe",
    stderr: "pipe",
  },
});
