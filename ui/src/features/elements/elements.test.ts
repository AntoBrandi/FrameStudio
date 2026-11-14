import { describe, expect, it } from "vitest";
import { HANDLE, slugify } from "./ElementEditor";

// The handle preview must match what the backend makes (library/elements.py: slugify).
describe("element handles", () => {
  it("makes handles like the backend", () => {
    expect(slugify("Maya Ortiz")).toBe("maya-ortiz");
    expect(slugify("  Café  del Mar! ")).toBe("cafe-del-mar");
    expect(slugify("Maya Ortíz E2E")).toBe("maya-ortiz-e2e");
    expect(slugify("東京")).toBe("");
    expect(slugify("x".repeat(200))).toHaveLength(40);
    expect(slugify(`${"a".repeat(39)} b`)).toBe("a".repeat(39));
  });

  it("accepts only valid handles", () => {
    expect(HANDLE.test("maya_o")).toBe(true);
    expect(HANDLE.test("maya-ortiz-2")).toBe(true);
    expect(HANDLE.test("Maya")).toBe(false);
    expect(HANDLE.test("-maya")).toBe(false);
    expect(HANDLE.test("a".repeat(49))).toBe(false);
  });
});
