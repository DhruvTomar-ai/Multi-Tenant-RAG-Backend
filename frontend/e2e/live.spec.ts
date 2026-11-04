import { expect, test } from "@playwright/test";
import { ask, LEAVE_QUESTION, login, newConversation } from "./helpers";

// Runs only with E2E_LIVE_GROQ=1 and GROQ_API_KEY set. It asks one question, to save quota.
test("real Groq: a grounded answer with a working citation", async ({ page }) => {
  test.skip(process.env.E2E_LIVE_GROQ !== "1" || !process.env.GROQ_API_KEY, "Needs E2E_LIVE_GROQ=1 and GROQ_API_KEY");
  await login(page, "mia");
  await newConversation(page, "acme");
  await ask(page, LEAVE_QUESTION);
  await expect(page.getByTestId("answer")).toContainText("24", { timeout: 60_000 });
  await page.getByRole("button", { name: /Source 1: leave-policy.txt/ }).click();
  await expect(page.getByTestId("citation-text")).toContainText("24 days of paid annual leave");
});
