import { expect, test } from "@playwright/test";
import { id, login, paths, seed, unique } from "./helpers";

test("a user can sign in and sees who they are acting as", async ({ page }) => {
  await login(page, "mia");
  await expect(page).toHaveURL(new RegExp(paths.tenant("acme")));
  const badge = page.getByTestId("context-badge");
  await expect(badge).toContainText("Tenant: Acme Corp");
  await expect(badge).toContainText("Tenant member");
  await expect(badge).toContainText("People Ops · Member");
});

test("a wrong password gets a plain message", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(seed().emails.mia);
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toHaveText("Incorrect email or password.");
  await expect(page).toHaveURL(/\/login/);
});

test("signing out ends the session and leaves nothing behind", async ({ page }) => {
  await login(page, "mia");
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
  expect(await page.evaluate(() => sessionStorage.length + localStorage.length)).toBe(0);
  await page.goto(paths.documents("acme"));
  await expect(page).toHaveURL(/\/login/);
});

test("opening a page while signed out returns there after signing in", async ({ page }) => {
  const target = paths.documents("acme");
  await page.goto(target);
  await expect(page).toHaveURL(/\/login\?next=/);
  await page.getByLabel("Email").fill(seed().emails.mia);
  await page.getByLabel("Password").fill(seed().password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(new RegExp(`${target}$`));
});

test("a login link can't redirect to another website", async ({ page }) => {
  await login(page, "mia", "//evil.example.com/");
  await expect(page).toHaveURL(new RegExp(paths.tenant("acme")));
});

test("when the session expires, the user goes to login and comes back to the same page", async ({ page }) => {
  await login(page, "mia");
  const target = paths.documents("acme");
  await page.goto(target);
  await expect(page.getByRole("heading", { name: "Documents" })).toBeVisible();

  // Every token is now refused, as it would be once both had expired.
  await page.route("**/api/v1/**", (route) => route.fulfill({ status: 401, json: { detail: "Invalid or expired token" } }));
  await page.getByRole("link", { name: "Search" }).click();
  await expect(page).toHaveURL(/\/login\?next=/);
  await expect(page.getByText("Your session has ended.")).toBeVisible();

  await page.unroute("**/api/v1/**");
  await page.getByLabel("Email").fill(seed().emails.mia);
  await page.getByLabel("Password").fill(seed().password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/search$/);
});

test("an expired access token is renewed without interrupting the user", async ({ page }) => {
  await login(page, "mia", paths.documents("acme"));
  let refused = false;
  await page.route(`**/api/v1/tenants/${id("acme")}/documents*`, async (route) => {
    if (refused) return route.fallback();
    refused = true;
    await route.fulfill({ status: 401, json: { detail: "Invalid or expired token" } });
  });
  const refresh = page.waitForResponse((response) => response.url().endsWith("/auth/refresh") && response.ok());
  await page.getByLabel("Processing status").selectOption("completed");
  await refresh;
  await expect(page.getByRole("link", { name: "leave-policy.txt" })).toBeVisible();
  await expect(page).toHaveURL(/\/documents/);
});

test("reloading keeps the session, and tokens stay out of long-term storage", async ({ page }) => {
  const messages: string[] = [];
  page.on("console", (message) => messages.push(message.text()));
  await login(page, "mia");
  await page.reload();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  expect(await page.evaluate(() => localStorage.length)).toBe(0);
  expect(await page.evaluate(() => document.cookie)).toBe("");
  expect(await page.evaluate(() => Object.keys(sessionStorage))).toEqual(["rag.refresh"]);
  const refresh = await page.evaluate(() => sessionStorage.getItem("rag.refresh"));
  expect(messages.join("\n")).not.toContain(refresh!);
});

test("a new user can register and create their own tenant", async ({ page }) => {
  const name = unique("tester");
  await page.goto("/register");
  await page.getByLabel("Full name").fill("New Tester");
  await page.getByLabel("Email").fill(`${name}@example.com`);
  await page.getByLabel("Password").fill(seed().password);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByText("You aren't part of a tenant yet.")).toBeVisible();
  await page.getByLabel("Name", { exact: true }).fill(`Tenant ${name}`);
  await page.getByRole("button", { name: "Create tenant" }).click();
  await expect(page.getByTestId("context-badge")).toContainText("Tenant admin");
});

test("registering with an email already in use gets a plain message", async ({ page }) => {
  await page.goto("/register");
  await page.getByLabel("Full name").fill("Duplicate");
  await page.getByLabel("Email").fill(seed().emails.mia);
  await page.getByLabel("Password").fill(seed().password);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("alert")).toHaveText("Email already registered");
});

test("a network failure shows a friendly message and retry, not an error dump", async ({ page }) => {
  await login(page, "mia", paths.documents("acme"));
  await expect(page.getByRole("heading", { name: "Documents" })).toBeVisible();
  await page.route(`**/api/v1/tenants/${id("acme")}/documents*`, (route) => route.abort());
  await page.getByLabel("Processing status").selectOption("failed");
  await expect(page.getByRole("alert")).toContainText("We couldn't reach the server");
  await page.unroute(`**/api/v1/tenants/${id("acme")}/documents*`);
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("the production build blocks inline scripts", async ({ page }) => {
  await page.goto("/login");
  const policy = await page.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute("content");
  expect(policy).toContain("script-src 'self'");
  const ran = await page.evaluate(() => {
    const script = document.createElement("script");
    script.textContent = "window.__inline = 1";
    document.head.append(script);
    return (window as unknown as { __inline?: number }).__inline;
  });
  expect(ran).toBeUndefined();
});

test("the app never calls the unauthenticated /users routes", async ({ page }) => {
  const called: string[] = [];
  page.on("request", (request) => {
    if (/\/api\/v1\/users/.test(request.url())) called.push(request.url());
  });
  await login(page, "ava");
  for (const suffix of ["", "/members", "/teams", "/documents", "/search", "/chat"]) {
    await page.goto(`${paths.tenant("acme")}${suffix}`);
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  }
  await page.goto(paths.team("acme", "acme_hr"));
  await expect(page.getByText("Members (")).toBeVisible();
  expect(called).toEqual([]);
});
