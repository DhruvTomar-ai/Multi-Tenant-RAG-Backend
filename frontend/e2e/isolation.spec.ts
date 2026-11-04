import { expect, test } from "@playwright/test";
import { ask, id, LEAVE_QUESTION, login, loginInNewContext, newConversation, NOT_FOUND, paths } from "./helpers";

test.describe("between tenants", () => {
  test("another tenant's pages say not found", async ({ page }) => {
    await login(page, "ava");
    for (const suffix of ["", "/documents", "/teams", "/search", "/chat"]) {
      await page.goto(`${paths.tenant("globex")}${suffix}`);
      await expect(page.getByRole("heading", { name: NOT_FOUND })).toBeVisible();
    }
  });

  test("another tenant's document URL says not found, and shows none of its content", async ({ page }) => {
    await login(page, "mia");
    await page.goto(paths.document("globex", "globex_sales", "doc_pricing"));
    await expect(page.getByRole("heading", { name: NOT_FOUND })).toBeVisible();
    await expect(page.getByText("globex-pricing")).toHaveCount(0);
  });

  test("a document reached through the wrong tenant looks the same as one that doesn't exist", async ({ page }) => {
    await login(page, "ava");
    await page.goto(`/t/${id("acme")}/teams/${id("globex_sales")}/documents/${id("doc_pricing")}`);
    await expect(page.getByRole("heading", { name: NOT_FOUND })).toBeVisible();
    const real = await page.getByRole("main").innerText();
    await page.goto(`/t/${id("acme")}/teams/${id("acme_hr")}/documents/2147483000`);
    await expect(page.getByRole("heading", { name: NOT_FOUND })).toBeVisible();
    expect(await page.getByRole("main").innerText()).toBe(real);
  });

  test("a tenant that doesn't exist looks the same as another tenant's", async ({ page }) => {
    await login(page, "ava");
    await page.goto("/t/2147483000");
    await expect(page.getByRole("heading", { name: NOT_FOUND })).toBeVisible();
    await page.goto("/t/not-a-number");
    await expect(page.getByRole("heading", { name: NOT_FOUND })).toBeVisible();
  });

  test("another tenant's team URL says not found", async ({ page }) => {
    await login(page, "ava");
    await page.goto(`/t/${id("acme")}/teams/${id("globex_sales")}`);
    await expect(page.getByRole("heading", { name: NOT_FOUND })).toBeVisible();
  });

  test("search only finds this tenant's documents", async ({ page }) => {
    await login(page, "ava", paths.search("acme"));
    await page.getByLabel("Search query").fill("enterprise pricing per month and discounts");
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await expect(page.getByTestId("search-result").first()).toBeVisible();
    await expect(page.getByText("globex-pricing.txt")).toHaveCount(0);
    await expect(page.getByText("5000 dollars")).toHaveCount(0);
  });
});

test.describe("switching tenants", () => {
  test("the switcher only offers your tenants", async ({ page }) => {
    await login(page, "mia");
    await expect(page.getByLabel("Switch tenant").locator("option")).toHaveText(["Acme Corp"]);
  });

  test("a person in two tenants sees each one's own data, and nothing carries over", async ({ page }) => {
    await login(page, "sam", paths.documents("acme"));
    await expect(page.getByTestId("context-badge")).toContainText("Tenant: Acme Corp");
    await expect(page.getByRole("link", { name: "leave-policy.txt" })).toBeVisible();

    // Hold the next tenant's data back, to catch anything left on screen in the meantime.
    let release = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    await page.route(`**/api/v1/tenants/${id("globex")}/documents*`, async (route) => {
      await held;
      await route.fallback();
    });
    await page.getByLabel("Switch tenant").selectOption({ label: "Globex Inc" });
    await expect(page.getByTestId("context-badge")).toContainText("Tenant: Globex Inc");
    await expect(page.getByTestId("context-badge")).toContainText("Sales · Manager");
    await page.getByRole("link", { name: "Documents", exact: true }).click();
    await expect(page.getByText("Loading documents…")).toBeVisible();
    await expect(page.getByText("leave-policy.txt")).toHaveCount(0);
    release();

    await expect(page.getByRole("link", { name: "globex-pricing.txt" })).toBeVisible();
    await expect(page.getByText("leave-policy.txt")).toHaveCount(0);

    await page.goBack();
    await page.goBack();
    await expect(page.getByTestId("context-badge")).toContainText("Tenant: Acme Corp");
    await expect(page.getByText("globex-pricing.txt")).toHaveCount(0);
  });

  test("a role in one tenant doesn't carry to the other", async ({ page }) => {
    await login(page, "sam", paths.team("globex", "globex_sales"));
    await expect(page.getByRole("button", { name: "Edit team" })).toBeEnabled();
    await page.goto(paths.team("acme", "acme_hr"));
    await expect(page.getByRole("button", { name: "Edit team" })).toBeDisabled();
    await expect(page.getByText("Only team managers can edit a team.")).toBeVisible();
  });
});

test.describe("between teams", () => {
  test("another team's document URL says not found", async ({ page }) => {
    await login(page, "eli");
    await page.goto(paths.document("acme", "acme_hr", "doc_salary"));
    await expect(page.getByRole("heading", { name: NOT_FOUND })).toBeVisible();
    await expect(page.getByText("salary-bands")).toHaveCount(0);
  });

  test("the document list only shows your team's documents", async ({ page }) => {
    await login(page, "eli", paths.documents("acme"));
    await expect(page.getByRole("link", { name: "incident-runbook.txt" })).toBeVisible();
    await expect(page.getByText("leave-policy.txt")).toHaveCount(0);
    await expect(page.getByText("salary-bands.txt")).toHaveCount(0);
  });

  test("the team list only shows your teams, and another team's page says not found", async ({ page }) => {
    await login(page, "eli", `${paths.tenant("acme")}/teams`);
    await expect(page.getByRole("link", { name: /Engineering/ })).toBeVisible();
    await expect(page.getByRole("link", { name: /People Ops/ })).toHaveCount(0);
    await page.goto(paths.team("acme", "acme_hr"));
    await expect(page.getByRole("heading", { name: NOT_FOUND })).toBeVisible();
  });

  test("search only finds your team's documents", async ({ page }) => {
    await login(page, "eli", paths.search("acme"));
    await page.getByLabel("Search query").fill(LEAVE_QUESTION);
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await page.waitForResponse((response) => response.url().endsWith("/search"));
    await expect(page.getByText("leave-policy.txt")).toHaveCount(0);
    await expect(page.getByText("24 days of paid annual leave")).toHaveCount(0);
  });

  test("chat can't answer from another team's documents", async ({ page }) => {
    await login(page, "eli");
    await newConversation(page, "acme");
    await ask(page, LEAVE_QUESTION);
    await expect(page.getByTestId("assistant-message")).toBeVisible();
    await expect(page.getByTestId("insufficient-evidence").or(page.getByTestId("answer"))).toBeVisible();
    await expect(page.getByText("24 days")).toHaveCount(0);
  });
});

test.describe("conversations", () => {
  test("someone else's conversation says not found, even for a tenant admin", async ({ page, browser }) => {
    await login(page, "mia");
    await newConversation(page, "acme", { title: "Mia's private question" });
    const url = page.url();

    const admin = await loginInNewContext(browser, "ava", url.replace(/^https?:\/\/[^/]+/, ""));
    await expect(admin.getByRole("heading", { name: NOT_FOUND })).toBeVisible();
    await expect(admin.getByText("Mia's private question")).toHaveCount(0);
    await admin.context().close();
  });
});
