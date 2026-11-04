import { expect, type Browser, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

interface Seed {
  password: string;
  ids: Record<string, number>;
  emails: Record<string, string>;
}

let cached: Seed | null = null;

export function seed(): Seed {
  cached ??= JSON.parse(readFileSync(path.join(process.cwd(), "e2e", ".seed.json"), "utf-8")) as Seed;
  return cached;
}

export const id = (key: string) => seed().ids[key];
export const NOT_FOUND = "Not found or you don't have access.";
export const unique = (prefix: string) => `${prefix}-${Date.now()}-${Math.floor(Math.random() * 10_000)}`;

export const paths = {
  tenant: (tenant: string) => `/t/${id(tenant)}`,
  documents: (tenant: string) => `/t/${id(tenant)}/documents`,
  document: (tenant: string, team: string, document: string) => `/t/${id(tenant)}/teams/${id(team)}/documents/${id(document)}`,
  team: (tenant: string, team: string) => `/t/${id(tenant)}/teams/${id(team)}`,
  chat: (tenant: string) => `/t/${id(tenant)}/chat`,
  search: (tenant: string) => `/t/${id(tenant)}/search`,
};

export async function login(page: Page, user: string, next = "/") {
  await page.goto(`/login?next=${encodeURIComponent(next)}`);
  await page.getByLabel("Email").fill(seed().emails[user]);
  await page.getByLabel("Password").fill(seed().password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

/** A second person in their own browser session, for checks that involve two users. */
export async function loginInNewContext(browser: Browser, user: string, next = "/") {
  const context = await browser.newContext({ baseURL: `http://127.0.0.1:${process.env.E2E_WEB_PORT ?? "4173"}` });
  const page = await context.newPage();
  await login(page, user, next);
  return page;
}

export async function newConversation(page: Page, tenant: string, options: { title?: string; scope?: string } = {}) {
  await page.goto(paths.chat(tenant));
  await page.getByRole("button", { name: "New conversation" }).click();
  if (options.title) await page.getByLabel("Title (optional)").fill(options.title);
  if (options.scope) await page.getByLabel("Team scope").selectOption({ label: options.scope });
  await page.getByRole("button", { name: "Start conversation" }).click();
  await expect(page.getByLabel("Your question")).toBeVisible();
}

export async function ask(page: Page, question: string) {
  await page.getByLabel("Your question").fill(question);
  await page.getByRole("button", { name: "Send" }).click();
}

export const LEAVE_QUESTION = "How many days of annual leave do employees get?";
