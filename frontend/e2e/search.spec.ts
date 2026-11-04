import { expect, test } from "@playwright/test";
import { id, LEAVE_QUESTION, login, paths } from "./helpers";

test("results show the title, a score and a snippet, and link to the document", async ({ page }) => {
  await login(page, "mia", paths.search("acme"));
  await page.getByLabel("Search query").fill(LEAVE_QUESTION);
  await page.getByRole("button", { name: "Search", exact: true }).click();
  const first = page.getByTestId("search-result").first();
  await expect(first.getByRole("link", { name: "leave-policy.txt" })).toBeVisible();
  await expect(first.getByText(/Match 0\.\d\d/)).toBeVisible();
  await expect(first.getByText("24 days of paid annual leave")).toBeVisible();
  await first.getByRole("link", { name: "leave-policy.txt" }).click();
  await expect(page.getByRole("heading", { name: "leave-policy.txt" })).toBeVisible();
});

test("filters narrow the results", async ({ page }) => {
  await login(page, "ava", paths.search("acme"));
  await page.getByLabel("Search query").fill("policy for staff");
  await page.getByLabel("Team").selectOption({ label: "Engineering" });
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByTestId("search-result").first()).toBeVisible();
  await expect(page.getByRole("main").getByRole("link", { name: "leave-policy.txt" })).toHaveCount(0);

  await page.getByLabel("Team").selectOption({ label: "All teams" });
  await page.getByLabel("Department").fill("HR");
  await page.getByLabel("Access label").selectOption("confidential");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByRole("link", { name: "salary-bands.txt" })).toBeVisible();
  await expect(page.getByRole("main").getByRole("link", { name: "leave-policy.txt" })).toHaveCount(0);

  await page.getByLabel("Access label").selectOption("");
  await page.getByLabel("Document", { exact: true }).selectOption(String(id("doc_leave")));
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByRole("main").getByRole("link", { name: "leave-policy.txt" }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "salary-bands.txt" })).toHaveCount(0);
});

test("no matches gets a plain empty state", async ({ page }) => {
  await login(page, "mia", paths.search("acme"));
  await page.getByLabel("Search query").fill("anything at all");
  await page.getByLabel("Department").fill("A department that doesn't exist");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByText("No matching passages in the documents you can read.")).toBeVisible();
});

test("a server failure gets a friendly message and a retry", async ({ page }) => {
  await login(page, "mia", paths.search("acme"));
  await page.route("**/search", (route) => route.fulfill({ status: 500, body: "Internal Server Error" }));
  await page.getByLabel("Search query").fill(LEAVE_QUESTION);
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Something went wrong on our side.");
  await expect(page.getByText("Internal Server Error")).toHaveCount(0);
  await page.unroute("**/search");
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByTestId("search-result").first()).toBeVisible();
});
