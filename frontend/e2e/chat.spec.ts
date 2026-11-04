import { expect, test } from "@playwright/test";
import { ask, id, LEAVE_QUESTION, login, loginInNewContext, newConversation, NOT_FOUND, paths, seed, unique } from "./helpers";

const AI_UNAVAILABLE = "The AI service is unavailable, try again.";
const isMessagePost = (url: string, method: string) => method === "POST" && /\/chat\/conversations\/\d+\/messages$/.test(url);

test.describe("answers", () => {
  test("an answer has clickable citations that show the source", async ({ page }) => {
    await login(page, "mia");
    await newConversation(page, "acme");
    await ask(page, LEAVE_QUESTION);
    await expect(page.getByTestId("answer")).toContainText("24 days");
    await expect(page.getByTestId("answer")).not.toContainText("[chunk:");
    await expect(page.getByRole("heading", { name: new RegExp(LEAVE_QUESTION.slice(0, 20)) })).toBeVisible();

    await page.getByRole("button", { name: /Source 1: leave-policy.txt/ }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("heading", { name: "leave-policy.txt" })).toBeVisible();
    await expect(dialog.getByTestId("citation-text")).toContainText("Up to five unused days");
    await dialog.getByRole("link", { name: "Open the document" }).click();
    await expect(page.getByRole("heading", { name: "leave-policy.txt" })).toBeVisible();
  });

  test("history is still there after a reload", async ({ page }) => {
    await login(page, "mia");
    await newConversation(page, "acme");
    await ask(page, LEAVE_QUESTION);
    await expect(page.getByTestId("answer")).toContainText("24 days");
    await page.reload();
    await expect(page.getByTestId("user-message")).toHaveText(LEAVE_QUESTION);
    await expect(page.getByTestId("answer")).toContainText("24 days");
  });

  test("a follow-up keeps the conversation going", async ({ page }) => {
    await login(page, "mia");
    await newConversation(page, "acme");
    await ask(page, LEAVE_QUESTION);
    await expect(page.getByTestId("answer")).toHaveCount(1);
    await ask(page, "Can unused days be carried forward?");
    await expect(page.getByTestId("answer")).toHaveCount(2);
    await expect(page.getByTestId("user-message")).toHaveCount(2);
  });

  test("model output containing HTML is shown as text and doesn't run", async ({ page }) => {
    await login(page, "mia");
    await newConversation(page, "acme");
    await ask(page, `${LEAVE_QUESTION} trigger-script`);
    await expect(page.getByTestId("answer")).toContainText("<script>");
    await expect(page.getByTestId("answer").locator("script, img")).toHaveCount(0);
    expect(await page.evaluate(() => (window as unknown as { __xss?: number }).__xss)).toBeUndefined();
  });
});

test.describe("insufficient evidence", () => {
  test("no documents to search", async ({ page }) => {
    const name = unique("nodocs");
    await page.goto("/register");
    await page.getByLabel("Full name").fill("No Documents");
    await page.getByLabel("Email").fill(`${name}@example.com`);
    await page.getByLabel("Password").fill(seed().password);
    await page.getByRole("button", { name: "Create account" }).click();
    await page.getByLabel("Name", { exact: true }).fill(name);
    await page.getByRole("button", { name: "Create tenant" }).click();
    await expect(page.getByTestId("context-badge")).toContainText("Tenant admin");
    await page.getByRole("link", { name: "Chat" }).click();
    await page.getByRole("button", { name: "New conversation" }).click();
    await page.getByRole("button", { name: "Start conversation" }).click();
    await ask(page, LEAVE_QUESTION);
    await expect(page.getByTestId("insufficient-evidence")).toContainText("I couldn't find this in your documents.");
  });

  test("documents that aren't related", async ({ page }) => {
    await login(page, "mia");
    await newConversation(page, "acme");
    await ask(page, "What is the capital of Australia?");
    await expect(page.getByTestId("insufficient-evidence")).toContainText("Your documents don't seem to cover this.");
  });

  test("the model decides the passages don't answer it", async ({ page }) => {
    await login(page, "mia");
    await newConversation(page, "acme");
    await ask(page, `${LEAVE_QUESTION} trigger-decline`);
    await expect(page.getByTestId("insufficient-evidence")).toContainText("The passages I found don't answer this.");
  });

  test("an answer with a made-up source isn't shown", async ({ page }) => {
    await login(page, "mia");
    await newConversation(page, "acme");
    await ask(page, `${LEAVE_QUESTION} trigger-no-citation`);
    await expect(page.getByTestId("insufficient-evidence")).toContainText("I couldn't back up an answer with a source.");
    await expect(page.getByText("A confident claim")).toHaveCount(0);
  });
});

test.describe("failures and retries", () => {
  test("a double-click sends one question", async ({ page }) => {
    await login(page, "mia");
    await newConversation(page, "acme");
    const posts: string[] = [];
    page.on("request", (request) => {
      if (isMessagePost(request.url(), request.method())) posts.push(request.postData() ?? "");
    });
    await page.getByLabel("Your question").fill(`${LEAVE_QUESTION} trigger-slow`);
    await page.getByRole("button", { name: "Send" }).dblclick();
    await expect(page.getByTestId("answer")).toContainText("24 days");
    expect(posts).toHaveLength(1);
    await expect(page.getByTestId("user-message")).toHaveCount(1);
    await page.reload();
    await expect(page.getByTestId("user-message")).toHaveCount(1);
  });

  test("the Enter key can't send twice either", async ({ page }) => {
    await login(page, "mia");
    await newConversation(page, "acme");
    const posts: string[] = [];
    page.on("request", (request) => {
      if (isMessagePost(request.url(), request.method())) posts.push(request.url());
    });
    const box = page.getByLabel("Your question");
    await box.fill(`${LEAVE_QUESTION} trigger-slow`);
    await box.press("Enter");
    await box.press("Enter");
    await expect(page.getByTestId("answer")).toBeVisible();
    expect(posts).toHaveLength(1);
  });

  test("a Groq outage says the AI is unavailable, and a retry doesn't duplicate the question", async ({ page }) => {
    await login(page, "mia");
    await newConversation(page, "acme");
    const ids: string[] = [];
    page.on("request", (request) => {
      if (isMessagePost(request.url(), request.method())) ids.push((request.postDataJSON() as { client_message_id: string }).client_message_id);
    });
    await ask(page, `${LEAVE_QUESTION} ${unique("trigger-once")}`);
    await expect(page.getByRole("alert")).toContainText(AI_UNAVAILABLE);

    await page.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByTestId("answer")).toContainText("24 days");
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(page.getByTestId("user-message")).toHaveCount(1);
    await expect(page.getByTestId("assistant-message")).toHaveCount(1);
    expect(ids).toHaveLength(2);
    expect(ids[0]).toBe(ids[1]);
    expect(ids[0]).toBeTruthy();
  });

  test("a 502 from Groq has the same friendly message", async ({ page }) => {
    await login(page, "mia");
    await newConversation(page, "acme");
    const response = page.waitForResponse((item) => isMessagePost(item.url(), item.request().method()));
    await ask(page, `${LEAVE_QUESTION} trigger-bad-request`);
    expect((await response).status()).toBe(502);
    await expect(page.getByRole("alert")).toContainText(AI_UNAVAILABLE);
  });

  test("a rate limit asks the user to wait", async ({ page }) => {
    await login(page, "mia");
    await newConversation(page, "acme");
    await ask(page, `${LEAVE_QUESTION} trigger-429`);
    await expect(page.getByRole("alert")).toContainText("Too many requests, wait a moment.");
  });

  test("a network failure can be retried with the same ID, and nothing doubles", async ({ page }) => {
    await login(page, "mia");
    await newConversation(page, "acme");
    const ids: string[] = [];
    let failed = false;
    await page.route(/\/chat\/conversations\/\d+\/messages$/, async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      ids.push((route.request().postDataJSON() as { client_message_id: string }).client_message_id);
      if (failed) return route.fallback();
      failed = true;
      await route.abort();
    });
    await ask(page, LEAVE_QUESTION);
    await expect(page.getByRole("alert")).toContainText("We couldn't reach the server");
    await expect(page.getByTestId("user-message")).toHaveText(LEAVE_QUESTION);

    await page.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByTestId("answer")).toContainText("24 days");
    await expect(page.getByTestId("user-message")).toHaveCount(1);
    expect(ids[0]).toBe(ids[1]);
  });
});

test.describe("managing conversations", () => {
  test("rename and archive, and an archived conversation can't take messages", async ({ page }) => {
    await login(page, "mia");
    await newConversation(page, "acme", { title: unique("Before") });
    const renamed = unique("After");
    await page.getByRole("button", { name: "Rename" }).click();
    await page.getByLabel("Title", { exact: true }).fill(renamed);
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.getByRole("heading", { name: renamed })).toBeVisible();
    await expect(page.getByRole("link", { name: renamed })).toBeVisible();

    await page.getByRole("button", { name: "Archive", exact: true }).click();
    await expect(page.getByText("This conversation is archived")).toBeVisible();
    await expect(page.getByLabel("Your question")).toHaveCount(0);
    await expect(page.getByRole("link", { name: renamed })).toHaveCount(0);
    await page.getByRole("button", { name: "Archived", exact: true }).click();
    await expect(page.getByRole("link", { name: renamed })).toBeVisible();

    await page.getByRole("button", { name: "Restore" }).click();
    await expect(page.getByLabel("Your question")).toBeVisible();
  });

  test("a team-scoped conversation only uses that team's documents", async ({ page }) => {
    await login(page, "ava");
    await newConversation(page, "acme", { scope: "Engineering only" });
    await expect(page.getByText("Engineering documents only")).toBeVisible();
    // The short runbook passes the relevance threshold, and the fake model always answers. So this
    // checks where the answer came from. A real model should decline, which live checks would show.
    await ask(page, LEAVE_QUESTION);
    await expect(page.getByTestId("answer")).toBeVisible();
    await expect(page.getByRole("button", { name: /Source 1: incident-runbook.txt/ })).toBeVisible();
    await expect(page.getByText("leave-policy.txt")).toHaveCount(0);
    await expect(page.getByText("24 days")).toHaveCount(0);
  });

  test("the team scope only offers teams you can read", async ({ page }) => {
    await login(page, "eli", paths.chat("acme"));
    await page.getByRole("button", { name: "New conversation" }).click();
    await expect(page.getByLabel("Team scope").locator("option")).toHaveText(["All documents I can read", "Engineering only"]);
  });
});

test("after access is revoked, an old answer is hidden and its source can't be opened", async ({ page, browser }) => {
  const documentPath = paths.document("acme", "acme_hr", "doc_salary");
  await login(page, "nia");
  await newConversation(page, "acme");
  const conversationUrl = page.url();

  const manager = await loginInNewContext(browser, "mark", documentPath);
  // Nia is in no team, so a manager can't pick her from a list and uses her ID.
  await manager.getByLabel("Or user ID").fill(String(id("nia")));
  await manager.getByRole("button", { name: "Give access" }).click();
  const grantRow = manager.getByRole("listitem").filter({ hasText: `User ID ${id("nia")}` });
  await expect(grantRow).toBeVisible();

  await ask(page, "What is the salary band for senior engineers?");
  await expect(page.getByTestId("answer")).toContainText("90000");

  await grantRow.getByRole("button", { name: "Revoke" }).click();
  await manager.getByRole("dialog").getByRole("button", { name: "Revoke access" }).click();
  await expect(manager.getByRole("dialog")).toHaveCount(0);
  await manager.context().close();

  await page.goto(conversationUrl);
  await expect(page.getByTestId("hidden-answer")).toHaveText("This answer's sources are no longer available to you.");
  await expect(page.getByText("90000")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Source 1/ })).toHaveCount(0);

  await page.goto(documentPath);
  await expect(page.getByRole("heading", { name: NOT_FOUND })).toBeVisible();
});
