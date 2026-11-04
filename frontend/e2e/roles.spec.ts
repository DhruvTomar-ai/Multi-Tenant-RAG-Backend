import { expect, test } from "@playwright/test";
import { id, login, loginInNewContext, NOT_FOUND, paths, seed, unique } from "./helpers";

test.describe("tenant admin", () => {
  test("sees People, and can create teams", async ({ page }) => {
    await login(page, "ava");
    await expect(page.getByTestId("context-badge")).toContainText("Tenant admin");
    await expect(page.getByRole("link", { name: "People" })).toBeVisible();

    const name = unique("Team");
    await page.goto(`${paths.tenant("acme")}/teams`);
    await page.getByRole("button", { name: "New team" }).click();
    await page.getByLabel("Name").fill(name);
    await page.getByRole("button", { name: "Create team" }).click();
    await expect(page.getByRole("link", { name: new RegExp(name) })).toBeVisible();

    await page.getByRole("button", { name: "New team" }).click();
    await page.getByLabel("Name").fill(name);
    await page.getByRole("button", { name: "Create team" }).click();
    await expect(page.getByRole("alert")).toHaveText("A team with this name already exists");
  });

  test("sees everyone in the tenant, and every team's documents", async ({ page }) => {
    await login(page, "ava", `${paths.tenant("acme")}/members`);
    await expect(page.getByText(seed().emails.vic)).toBeVisible();
    await expect(page.getByText(seed().emails.bo)).toHaveCount(0);
    await page.goto(paths.documents("acme"));
    await expect(page.getByRole("link", { name: "leave-policy.txt" })).toBeVisible();
    await expect(page.getByRole("link", { name: "incident-runbook.txt" })).toBeVisible();
  });

  test("can add a registered person, and gets a plain message for an unknown email", async ({ page }) => {
    await login(page, "bo", `${paths.tenant("globex")}/members`);
    await page.getByLabel("Email").fill("nobody-has-this@example.com");
    await page.getByRole("button", { name: "Add person" }).click();
    await expect(page.getByRole("alert")).toContainText("Check that they've created an account");

    await page.getByLabel("Email").fill(seed().emails.ben);
    await page.getByRole("button", { name: "Add person" }).click();
    await expect(page.getByRole("status").filter({ hasText: "is now a member" })).toBeVisible();
  });

  test("can edit tenant details", async ({ page }) => {
    await login(page, "bo", paths.tenant("globex"));
    await page.getByRole("button", { name: "Edit details" }).click();
    await page.getByLabel("Description").fill("Updated by a test.");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Updated by a test.")).toBeVisible();
  });
});

test.describe("team manager", () => {
  test("manages their team, without People or team creation", async ({ page }) => {
    await login(page, "mark", paths.team("acme", "acme_hr"));
    await expect(page.getByTestId("context-badge")).toContainText("Team: People Ops · Manager");
    await expect(page.getByRole("link", { name: "People" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Edit team" })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Deactivate" })).toBeVisible();

    await page.goto(`${paths.tenant("acme")}/members`);
    await expect(page.getByRole("heading", { name: NOT_FOUND })).toBeVisible();

    await page.goto(`${paths.tenant("acme")}/teams`);
    await expect(page.getByRole("button", { name: "New team" })).toBeDisabled();
    await expect(page.getByText("Only tenant admins can create teams.")).toBeVisible();
  });

  test("can add a tenant member by ID, change their role and remove them", async ({ page }) => {
    await login(page, "mark", paths.team("acme", "acme_hr"));
    await page.getByLabel("Or user ID").fill(String(id("eli")));
    await page.getByLabel("New member role").selectOption("viewer");
    await page.getByRole("button", { name: "Add to team" }).click();
    const row = page.getByTestId(`member-${seed().emails.eli}`);
    await expect(row).toBeVisible();

    await row.getByLabel(/Role for/).selectOption("member");
    await expect(page.getByRole("status").filter({ hasText: "Role updated." })).toBeVisible();

    await row.getByRole("button", { name: "Remove" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Remove" }).click();
    await expect(row).toHaveCount(0);
  });

  test("isn't offered the admin role", async ({ page }) => {
    await login(page, "mark", paths.team("acme", "acme_hr"));
    await expect(page.getByLabel("New member role").locator("option")).toHaveText(["Manager", "Member", "Viewer"]);
  });

  test("someone outside the tenant can't be added", async ({ page }) => {
    await login(page, "mark", paths.team("acme", "acme_hr"));
    await page.getByLabel("Or user ID").fill(String(id("bo")));
    await page.getByRole("button", { name: "Add to team" }).click();
    await expect(page.getByRole("alert")).toContainText("User is not a member of this tenant");
  });

  test("can deactivate and reactivate a team", async ({ page }) => {
    const name = unique("Short-lived");
    const admin = await loginInNewContext(page.context().browser()!, "ava", `${paths.tenant("acme")}/teams`);
    await admin.getByRole("button", { name: "New team" }).click();
    await admin.getByLabel("Name").fill(name);
    await admin.getByRole("button", { name: "Create team" }).click();
    await admin.getByRole("link", { name: new RegExp(name) }).click();
    await admin.getByRole("button", { name: "Deactivate" }).click();
    await expect(admin.getByText("This team is inactive")).toBeVisible();
    await admin.getByRole("button", { name: "Reactivate" }).click();
    await expect(admin.getByText("This team is inactive")).toHaveCount(0);
    await admin.context().close();
  });
});

test.describe("member", () => {
  test("can upload but not manage or share", async ({ page }) => {
    await login(page, "mia", paths.documents("acme"));
    await expect(page.getByRole("link", { name: "Upload document" })).toBeVisible();

    await page.goto(paths.document("acme", "acme_hr", "doc_leave"));
    await expect(page.getByRole("heading", { name: "leave-policy.txt" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Shared with" })).toHaveCount(0);

    await page.goto(paths.team("acme", "acme_hr"));
    await expect(page.getByRole("button", { name: "Edit team" })).toBeDisabled();
    await expect(page.getByText("Only team managers can add or remove members.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Add to team" })).toHaveCount(0);
  });
});

test.describe("viewer", () => {
  test("can read but not upload, and is told why", async ({ page }) => {
    await login(page, "vic", paths.documents("acme"));
    await expect(page.getByTestId("context-badge")).toContainText("People Ops · Viewer");
    await expect(page.getByRole("button", { name: "Upload document" })).toBeDisabled();
    await expect(page.getByText("Viewers can read documents but can't upload them.")).toBeVisible();
    await expect(page.getByRole("link", { name: "leave-policy.txt" })).toBeVisible();

    await page.goto(`${paths.documents("acme")}/upload`);
    await expect(page.getByText("You can't upload here.")).toBeVisible();

    await page.goto(paths.document("acme", "acme_hr", "doc_leave"));
    await expect(page.getByRole("heading", { name: "Shared with" })).toHaveCount(0);
  });

  test("is still refused by the backend if the hidden action is forced", async ({ page }) => {
    await login(page, "vic", paths.documents("acme"));
    const status = await page.evaluate(
      async ({ tenant, team }) => {
        const form = new FormData();
        form.append("file", new File(["forced"], "forced.txt", { type: "text/plain" }));
        const refresh = await fetch("/api/v1/auth/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: sessionStorage.getItem("rag.refresh") }) });
        const { access_token } = await refresh.json();
        return (await fetch(`/api/v1/tenants/${tenant}/teams/${team}/documents/upload`, { method: "POST", body: form, headers: { Authorization: `Bearer ${access_token}` } })).status;
      },
      { tenant: id("acme"), team: id("acme_hr") },
    );
    expect(status).toBe(403);
  });
});

test.describe("no team", () => {
  test("a grant gives one document only", async ({ page }) => {
    await login(page, "nia", paths.documents("acme"));
    await expect(page.getByTestId("context-badge")).toContainText("No team yet");
    await expect(page.getByRole("link", { name: "leave-policy.txt" })).toBeVisible();
    await expect(page.getByText("incident-runbook.txt")).toHaveCount(0);

    await page.getByRole("link", { name: "leave-policy.txt" }).click();
    await expect(page.getByRole("heading", { name: "leave-policy.txt" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Shared with" })).toHaveCount(0);

    await page.goto(paths.document("acme", "acme_hr", "doc_salary"));
    await expect(page.getByRole("heading", { name: NOT_FOUND })).toBeVisible();
  });
});
