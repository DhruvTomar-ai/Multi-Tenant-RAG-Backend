import { expect, test, type Page } from "@playwright/test";
import { id, login, loginInNewContext, NOT_FOUND, paths, seed, unique } from "./helpers";

async function chooseFile(page: Page, name: string, content: string | Buffer, mimeType = "text/plain") {
  await page.getByLabel("File").setInputFiles({ name, mimeType, buffer: Buffer.isBuffer(content) ? content : Buffer.from(content) });
}

test("an upload goes from received to embedded, with its progress shown, and becomes searchable", async ({ page }) => {
  test.setTimeout(240_000);
  const marker = unique("zebra");
  const filename = `${marker}.txt`;
  await login(page, "mia", `${paths.documents("acme")}/upload`);
  await expect(page.getByText("Up to 25 MB.")).toBeVisible();
  await chooseFile(page, filename, `The ${marker} procurement rule says every purchase above 900 dollars needs two approvals from finance.`);
  await page.getByLabel("Department (optional)").fill("Procurement");
  await page.getByLabel("Tags (optional)").fill("Purchasing, Rules");
  await page.getByRole("button", { name: "Upload" }).click();

  await expect(page.getByRole("heading", { name: filename })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "Processing has started" })).toBeVisible();
  await expect(page.getByTestId("processing-status")).toContainText("Ready to search", { timeout: 200_000 });
  await expect(page.getByText("Procurement", { exact: true })).toBeVisible();
  await expect(page.getByText("purchasing, rules")).toBeVisible();

  await page.getByRole("button", { name: "Show text" }).click();
  await expect(page.getByText(`The ${marker} procurement rule`)).toBeVisible();

  await page.getByRole("link", { name: "Search" }).click();
  await page.getByLabel("Search query").fill(`${marker} purchase approvals`);
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByRole("link", { name: filename })).toBeVisible();
});

test("a document that isn't embedded yet says so", async ({ page }) => {
  await login(page, "mia");
  // Held at "processing", so the wording can be checked without racing the worker.
  await page.route(`**/documents/${id("doc_leave")}/status`, (route) =>
    route.fulfill({ json: { document_id: id("doc_leave"), status: "completed", version: 1, chunk_count: 10, latest_job_status: "completed", embedding_status: "processing", embedding_count: 3, embedding_attempts: 1, duplicate_of_document_id: null } }),
  );
  await page.goto(paths.document("acme", "acme_hr", "doc_leave"));
  await expect(page.getByTestId("processing-status")).toContainText("Embedding 3 of 10 chunks");
  await expect(page.getByText("This document isn't searchable yet.")).toBeVisible();
});

test("an unsupported file type is refused before upload, with the reason", async ({ page }) => {
  await login(page, "mia", `${paths.documents("acme")}/upload`);
  await chooseFile(page, "malware.exe", "not a document", "application/octet-stream");
  await expect(page.getByRole("alert")).toContainText("That file type isn't supported. Upload one of: .pdf, .docx, .txt.");
  await expect(page.getByRole("button", { name: "Upload" })).toBeDisabled();
});

test("an oversized file is refused before upload", async ({ page }) => {
  await login(page, "mia", `${paths.documents("acme")}/upload`);
  await chooseFile(page, "huge.txt", Buffer.alloc(26 * 1024 * 1024, 97));
  await expect(page.getByRole("alert")).toContainText("larger than the 25 MB limit");
});

test("the backend's own file checks reach the user in plain words", async ({ page }) => {
  await login(page, "mia", `${paths.documents("acme")}/upload`);
  await chooseFile(page, "fake.pdf", "this is not really a pdf", "application/pdf");
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByRole("alert")).toHaveText("File content is not a valid PDF");
});

test("reserved custom details are refused with a clear message, and bad JSON is caught", async ({ page }) => {
  await login(page, "mia", `${paths.documents("acme")}/upload`);
  await chooseFile(page, `${unique("meta")}.txt`, unique("metadata check"));
  await page.getByLabel("Custom details (optional)").fill("not json");
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByRole("alert")).toContainText("Enter this as JSON");

  await page.getByLabel("Custom details (optional)").fill('{"tenant_id": 2}');
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByRole("alert")).toContainText("reserved field");
});

test("uploading the same file twice links to the existing document", async ({ page }) => {
  const content = `A one-off document for the duplicate check. ${unique("dup")}`;
  const name = `${unique("dup")}.txt`;
  await login(page, "mia", `${paths.documents("acme")}/upload`);
  await chooseFile(page, name, content);
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();

  await page.goto(`${paths.documents("acme")}/upload`);
  await chooseFile(page, name, content);
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByRole("alert")).toContainText("This team already has this file.");
  await page.getByRole("link", { name: "Open the existing document" }).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();
});

test("the list filters by team, department and status", async ({ page }) => {
  await login(page, "ava", paths.documents("acme"));
  await page.getByLabel("Team").selectOption({ label: "Engineering" });
  await expect(page.getByRole("link", { name: "incident-runbook.txt" })).toBeVisible();
  await expect(page.getByText("leave-policy.txt")).toHaveCount(0);

  await page.getByLabel("Team").selectOption({ label: "All teams" });
  await page.getByLabel("Department").fill("HR");
  await page.getByLabel("Department").press("Enter");
  await expect(page.getByRole("link", { name: "salary-bands.txt" })).toBeVisible();
  await expect(page.getByText("incident-runbook.txt")).toHaveCount(0);

  await page.getByLabel("Processing status").selectOption("failed");
  await expect(page.getByText("No documents match these filters.")).toBeVisible();
});

test("a new tenant shows the empty state, with a way to upload", async ({ page }) => {
  await page.goto("/register");
  const name = unique("empty");
  await page.getByLabel("Full name").fill("Empty Tenant Owner");
  await page.getByLabel("Email").fill(`${name}@example.com`);
  await page.getByLabel("Password").fill(seed().password);
  await page.getByRole("button", { name: "Create account" }).click();
  await page.getByLabel("Name", { exact: true }).fill(name);
  await page.getByRole("button", { name: "Create tenant" }).click();
  await expect(page.getByTestId("context-badge")).toContainText("Tenant admin");
  await page.getByRole("link", { name: "Documents", exact: true }).click();
  await expect(page.getByText("No documents yet.")).toBeVisible();
  await page.getByRole("link", { name: "Teams" }).click();
  await expect(page.getByText("No teams yet.")).toBeVisible();
});

test("a document can be downloaded", async ({ page }) => {
  await login(page, "vic", paths.document("acme", "acme_hr", "doc_leave"));
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download" }).click();
  expect((await download).suggestedFilename()).toBe("leave-policy.txt");
});

test("a failed embedding can be retried, and the document becomes ready", async ({ page }) => {
  test.setTimeout(240_000);
  await login(page, "eli", paths.document("acme", "acme_eng", "doc_failed"));
  await expect(page.getByTestId("processing-status")).toContainText("Embedding failed");
  await page.getByRole("button", { name: "Retry embedding" }).click();
  await expect(page.getByTestId("processing-status")).toContainText("Ready to search", { timeout: 200_000 });
  await expect(page.getByRole("button", { name: "Retry embedding" })).toHaveCount(0);
});

test.describe("grants", () => {
  test("a manager shares a document, the person can open it, and revoking removes it again", async ({ page, browser }) => {
    const documentPath = paths.document("acme", "acme_hr", "doc_salary");
    await login(page, "mark", documentPath);
    await expect(page.getByRole("heading", { name: "Shared with" })).toBeVisible();

    const reader = await loginInNewContext(browser, "eli", documentPath);
    await expect(reader.getByRole("heading", { name: NOT_FOUND })).toBeVisible();

    await page.getByLabel("Or user ID").fill(String(id("eli")));
    await page.getByRole("button", { name: "Give access" }).click();
    await expect(page.getByRole("button", { name: "Revoke" })).toBeVisible();

    await reader.reload();
    await expect(reader.getByRole("heading", { name: "salary-bands.txt" })).toBeVisible();
    await expect(reader.getByRole("heading", { name: "Shared with" })).toHaveCount(0);

    await page.getByRole("button", { name: "Revoke" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Revoke access" }).click();
    await expect(page.getByText("Not shared with anyone outside the team.")).toBeVisible();

    await reader.reload();
    await expect(reader.getByRole("heading", { name: NOT_FOUND })).toBeVisible();
    await reader.context().close();
  });

  test("the seeded grant is listed by name for a tenant admin", async ({ page }) => {
    await login(page, "ava", paths.document("acme", "acme_hr", "doc_leave"));
    await expect(page.getByRole("heading", { name: "Shared with" })).toBeVisible();
    await expect(page.getByText(`Nia Newcomer (${seed().emails.nia})`)).toBeVisible();
  });

  test("someone outside the tenant can't be given access", async ({ page }) => {
    await login(page, "mark", paths.document("acme", "acme_hr", "doc_salary"));
    await page.getByLabel("Or user ID").fill(String(id("bo")));
    await page.getByRole("button", { name: "Give access" }).click();
    await expect(page.getByRole("alert")).toHaveText("User is not a member of this tenant");
  });
});

test("document text containing HTML is shown as text and doesn't run", async ({ page }) => {
  test.setTimeout(120_000);
  const content = `<img src=x onerror="window.__xss=1"><script>window.__xss=1</script> ${unique("markup")} is a safety note.`;
  await login(page, "mia", `${paths.documents("acme")}/upload`);
  await chooseFile(page, `${unique("html")}.txt`, content);
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByTestId("processing-status")).toContainText(/Waiting to create|Embedding|Ready/, { timeout: 100_000 });
  await page.getByRole("button", { name: "Show text" }).click();
  await expect(page.getByText('onerror="window.__xss=1"')).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { __xss?: number }).__xss)).toBeUndefined();
});
