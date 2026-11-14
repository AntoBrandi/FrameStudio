import { describe, expect, it } from "vitest";
import { diffWords, tokens } from "./diff";

describe("word diff", () => {
  it("keeps the text when joined back", () => {
    const text = "a red door,  at dusk\nwith rain";
    expect(tokens(text).join("")).toBe(text);
  });

  it("marks added and removed words", () => {
    const parts = diffWords("a red door at night", "a weathered red door at dusk, rain on the glass");
    expect(
      parts
        .filter((p) => p.kind !== "removed")
        .map((p) => p.text)
        .join(""),
    ).toBe("a weathered red door at dusk, rain on the glass");
    expect(
      parts
        .filter((p) => p.kind !== "added")
        .map((p) => p.text)
        .join(""),
    ).toBe("a red door at night");
    expect(parts).toContainEqual({ kind: "added", text: "weathered " });
    expect(parts.find((p) => p.kind === "removed")?.text).toBe("night");
  });

  it("handles empty sides", () => {
    expect(diffWords("", "new prompt")).toEqual([{ kind: "added", text: "new prompt" }]);
    expect(diffWords("old", "")).toEqual([{ kind: "removed", text: "old" }]);
    expect(diffWords("same", "same")).toEqual([{ kind: "same", text: "same" }]);
  });
});
