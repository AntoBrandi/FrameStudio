import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { makeQueryClient } from "@/app/App";
import { resolveTheme } from "@/app/theme";
import { mockApi } from "@/test/api-mock";
import { AuthGate } from "./AuthGate";
import { useAuthStore } from "./store";

function renderGate() {
  render(
    <QueryClientProvider client={makeQueryClient()}>
      <AuthGate>
        <p>the app</p>
      </AuthGate>
    </QueryClientProvider>,
  );
}

describe("auth gate", () => {
  it("renders the app until the backend asks for a token", () => {
    act(() => useAuthStore.getState().setNeedsToken(false));
    renderGate();
    expect(screen.getByText("the app")).toBeInTheDocument();
  });

  it("signs in with a pasted token", async () => {
    let accepted = false;
    mockApi({
      "POST /api/auth/session": async (request: Request) => {
        const body = (await request.json()) as { token: string };
        accepted = body.token === "secret-token";
        return new Response(null, { status: accepted ? 204 : 401 });
      },
    });
    act(() => useAuthStore.getState().setNeedsToken(true));
    const user = userEvent.setup();
    renderGate();
    await user.type(screen.getByLabelText("Access token"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That token is not right.");
    await user.clear(screen.getByLabelText("Access token"));
    await user.type(screen.getByLabelText("Access token"), "secret-token");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(screen.getByText("the app")).toBeInTheDocument());
    expect(accepted).toBe(true);
  });
});

describe("theme", () => {
  it("resolves the system theme from the media query", () => {
    expect(resolveTheme("system", true)).toBe("light");
    expect(resolveTheme("system", false)).toBe("dark");
    expect(resolveTheme("light", false)).toBe("light");
  });
});
