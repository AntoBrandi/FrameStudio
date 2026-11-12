import http from "node:http";
import type { AddressInfo } from "node:net";
import { expect, test } from "./fixtures";

// The same UI in a plain browser (decision D9), signed in like a remote client.

test("M0.2: a browser without the token gets the sign-in form, then the app", async ({ page, backend }) => {
  await page.goto(backend.url);
  await expect(page.getByRole("heading", { name: "Sign in to this FrameStudio" })).toBeVisible();
  await page.getByLabel("Access token").fill("not-the-token");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toHaveText("That token is not right.");
  await page.getByLabel("Access token").fill(backend.token);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("main").getByText("Backend: connected")).toBeVisible();
});

test("M0.1: the sign-in link opens the app in a browser", async ({ page, backend }) => {
  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/image`);
  await expect(page).toHaveURL(`${backend.url}/image`);
  await expect(page.getByTestId("prompt-input")).toBeVisible();
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Home" }).click();
  await expect(page.getByRole("main").getByText("Backend: connected")).toBeVisible();
});

test("M0.2: another local site cannot use the API, even with the session cookie", async ({ page, backend }) => {
  // Sign in, so the browser holds the SameSite=Strict session cookie for 127.0.0.1.
  await page.goto(`${backend.url}/auth/login?token=${backend.token}`);
  await expect(page.getByRole("main").getByText("Backend: connected")).toBeVisible();

  // A page on another port of the same host is same-site, so the browser sends
  // the cookie along; only the Origin check stands between it and the API.
  const server = http.createServer((_req, res) => {
    res.writeHead(200, { "Content-Type": "text/html" });
    res.end("<!doctype html><title>other site</title>");
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  try {
    await page.goto(`http://127.0.0.1:${port}/`);
    await page.evaluate(async (url) => {
      await fetch(`${url}/api/jobs/fake`, {
        method: "POST",
        mode: "no-cors",
        credentials: "include",
        headers: { "Content-Type": "text/plain" },
        body: JSON.stringify({ kind: "local", seconds: 1 }),
      }).catch(() => undefined);
    }, backend.url);
  } finally {
    server.close();
  }
  const jobs = (await (await backend.api("/api/jobs")).json()) as { kind: string }[];
  expect(jobs.filter((job) => job.kind.startsWith("fake."))).toEqual([]);
});
