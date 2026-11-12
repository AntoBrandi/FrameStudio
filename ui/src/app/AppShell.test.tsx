import { createMemoryHistory } from "@tanstack/react-router";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { requests } from "@/test/api-mock";
import { App, makeQueryClient } from "./App";
import { NAV } from "./nav";
import { makeRouter } from "./router";

function renderApp(path = "/") {
  const router = makeRouter(createMemoryHistory({ initialEntries: [path] }));
  render(<App router={router as never} queryClient={makeQueryClient()} />);
  return router;
}

describe("app shell", () => {
  it("shows the backend as connected on the home page", async () => {
    renderApp();
    const main = await screen.findByRole("main");
    expect(await within(main).findByText("Backend: connected")).toBeInTheDocument();
  });

  it("links to every route from the main navigation", async () => {
    renderApp();
    const nav = await screen.findByRole("navigation", { name: "Main" });
    for (const item of NAV.filter((entry) => entry.key !== "settings")) {
      expect(within(nav).getByRole("link", { name: new RegExp(item.key, "i") })).toBeInTheDocument();
    }
    expect(screen.getByRole("link", { name: "Settings" })).toBeInTheDocument();
  });

  it("reaches each route by mouse", async () => {
    const user = userEvent.setup();
    const router = renderApp();
    const nav = await screen.findByRole("navigation", { name: "Main" });
    for (const item of NAV.filter((entry) => entry.key !== "settings")) {
      await user.click(within(nav).getByRole("link", { name: new RegExp(item.key, "i") }));
      await waitFor(() => expect(router.state.location.pathname).toBe(item.to));
    }
    await user.click(screen.getByRole("link", { name: "Settings" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/settings"));
  });

  it("reaches each route from the keyboard through the command palette", async () => {
    const user = userEvent.setup();
    const router = renderApp();
    await screen.findByRole("navigation", { name: "Main" });
    for (const item of NAV) {
      await user.keyboard("{Control>}k{/Control}");
      const input = await screen.findByPlaceholderText("Type a command or search…");
      await user.type(input, `Go to ${item.key}`);
      await user.keyboard("{Enter}");
      await waitFor(() => expect(router.state.location.pathname).toBe(item.to));
    }
  });

  it("toggles the theme and saves it", async () => {
    const user = userEvent.setup();
    renderApp();
    const toggle = await screen.findByTestId("theme-toggle");
    expect(document.documentElement.dataset.theme).toBe("dark");
    await user.click(toggle);
    expect(document.documentElement.dataset.theme).toBe("light");
    await waitFor(() =>
      expect(requests).toContainEqual({ method: "PATCH", path: "/api/settings", body: { theme: "light" } }),
    );
  });

  it("toggles the sidebar and inspector with shortcuts", async () => {
    const user = userEvent.setup();
    renderApp();
    await screen.findByRole("navigation", { name: "Main" });
    expect(screen.getByRole("complementary", { name: "Library" })).toBeInTheDocument();
    await user.keyboard("{Control>}b{/Control}");
    expect(screen.queryByRole("complementary", { name: "Library" })).not.toBeInTheDocument();
    await user.keyboard("{Control>}.{/Control}");
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
    await act(async () => {
      await user.keyboard("{Control>}.{/Control}");
    });
    expect(screen.queryByRole("complementary", { name: "Inspector" })).not.toBeInTheDocument();
  });
});
