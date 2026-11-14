import { QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  $createRangeSelection,
  $createTextNode,
  $getRoot,
  $setSelection,
  createEditor,
  type LexicalEditor,
  ParagraphNode,
} from "lexical";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { makeQueryClient } from "@/app/App";
import { mockApi, requests } from "@/test/api-mock";
import { $readTokens, $writeTokens, PromptEditor, TokenNode } from "./PromptEditor";
import { mergeTokens, renderTokens, type Token, trimTokens } from "./tokens";

// M4.3: the prompt editor reads and writes token lists; chips for elements.

const maya: Token = { type: "element", handle: "maya", element_id: "e-maya" };
const ghost: Token = { type: "element", handle: "ghost", element_id: null };
const text = (value: string): Token => ({ type: "text", text: value });

function headless(): LexicalEditor {
  const editor = createEditor({
    nodes: [ParagraphNode, TokenNode],
    onError: (error) => {
      throw error;
    },
  });
  return editor;
}

function roundTrip(tokens: Token[]): Token[] {
  const editor = headless();
  editor.update(() => $writeTokens(tokens), { discrete: true });
  return editor.getEditorState().read($readTokens);
}

describe("prompt tokens", () => {
  it("helpers merge, render and trim", () => {
    expect(mergeTokens([text("a"), text(""), text("b"), maya, text("")])).toEqual([text("ab"), maya]);
    expect(renderTokens([maya, text(" on "), { type: "move", name: "orbit" }, text(" "), ghost])).toBe(
      "@maya on #orbit @ghost",
    );
    expect(trimTokens([text("  "), maya, text(" walks \n")])).toEqual([maya, text(" walks")]);
  });

  it("the editor keeps every kind of token, line breaks included", () => {
    const tokens: Token[] = [
      text("Shot 1: "),
      maya,
      text(" walks\nShot 2: "),
      ghost,
      text(" "),
      { type: "move", name: "dolly-in" },
      text(" "),
      { type: "skill", name: "storyboard" },
    ];
    expect(roundTrip(tokens)).toEqual(tokens);
    expect(roundTrip([])).toEqual([]);
    expect(roundTrip([text("\n\nx\n")])).toEqual([text("\n\nx\n")]);
  });

  it("a chip is one unit that survives JSON", () => {
    const editor = headless();
    editor.update(() => $writeTokens([maya]), { discrete: true });
    const json = JSON.stringify(editor.getEditorState().toJSON());
    expect(json).toContain('"elementId":"e-maya"');
    const copy = headless();
    copy.setEditorState(copy.parseEditorState(json));
    expect(copy.getEditorState().read($readTokens)).toEqual([maya]);
    editor.getEditorState().read(() => {
      const chip = $getRoot().getFirstDescendant() as TokenNode;
      expect(chip).toBeInstanceOf(TokenNode);
      expect(chip.getMode()).toBe("token");
      expect(chip.getTextContent()).toBe("@maya");
    });
  });
});

function Harness({ initial, projectId }: { initial: Token[]; projectId?: string }) {
  const [value, setValue] = useState(initial);
  return (
    <QueryClientProvider client={makeQueryClient()}>
      <PromptEditor
        value={value}
        onChange={setValue}
        onSubmit={() => undefined}
        projectId={projectId}
        label="Prompt"
        placeholder="Describe"
      />
      <output data-testid="tokens">{JSON.stringify(value)}</output>
    </QueryClientProvider>
  );
}

const editorOf = () =>
  (screen.getByTestId("prompt-input") as HTMLElement & { __lexicalEditor: LexicalEditor }).__lexicalEditor;

const ELEMENTS = [
  {
    id: "e-maya",
    handle: "maya",
    name: "Maya Ortiz",
    category: "character",
    status: "ready",
    version: 1,
    description: "",
    properties: {},
    project_id: null,
    folder_id: null,
    media: [],
    cover_url: "/api/assets/a1/thumb",
    used_in: 0,
    created_at: "2026-11-14T00:00:00Z",
    updated_at: "2026-11-14T00:00:00Z",
  },
];

describe("prompt editor", () => {
  it("shows chips for elements, marking unknown handles", async () => {
    render(<Harness initial={[maya, text(" meets "), ghost]} />);
    const chips = await screen.findAllByTestId("prompt-token");
    expect(chips.map((chip) => chip.textContent)).toEqual(["@maya", "@ghost"]);
    expect(chips[0]).not.toHaveAttribute("data-unknown");
    expect(chips[1]).toHaveAttribute("data-unknown", "true");
  });

  it("typing @ suggests elements with pictures; picking one makes a chip", async () => {
    mockApi({ "GET /api/elements": ELEMENTS });
    render(<Harness initial={[text("A shot of ")]} />);
    const editor = editorOf();
    act(() => {
      screen.getByTestId("prompt-input").focus();
      editor.update(
        () => {
          const paragraph = $getRoot().getFirstChildOrThrow() as ParagraphNode;
          const typed = $createTextNode("A shot of @ma");
          paragraph.getFirstChild()!.replace(typed);
          const selection = $createRangeSelection();
          selection.anchor.set(typed.getKey(), 13, "text");
          selection.focus.set(typed.getKey(), 13, "text");
          $setSelection(selection);
        },
        { discrete: true },
      );
    });
    const menu = await screen.findByTestId("element-suggestions");
    await waitFor(() => expect(menu.querySelector('[data-handle="maya"]')).not.toBeNull());
    expect(menu.querySelector("img")).toHaveAttribute("src", "/api/assets/a1/thumb");
    expect(requests).toContainEqual({ method: "GET", path: "/api/elements", body: undefined });
    fireEvent.mouseDown(menu.querySelector('[data-handle="maya"]')!);
    await waitFor(() =>
      expect(JSON.parse(screen.getByTestId("tokens").textContent!)).toEqual([text("A shot of "), maya, text(" ")]),
    );
  });

  it("pasted text goes through the backend parser", async () => {
    mockApi({
      "POST /api/prompts/parse": {
        tokens: [maya, text(" at dusk")],
        text: "@maya at dusk",
        warnings: [],
      },
    });
    render(<Harness initial={[]} projectId="p1" />);
    const input = screen.getByTestId("prompt-input");
    act(() => input.focus());
    // jsdom has no DataTransfer; the editor only reads plain text from it.
    const clipboardData = {
      types: ["text/plain"],
      getData: (type: string) => (type === "text/plain" ? "@maya at dusk" : ""),
    };
    fireEvent.paste(input, { clipboardData });
    await waitFor(() =>
      expect(JSON.parse(screen.getByTestId("tokens").textContent!)).toEqual([maya, text(" at dusk")]),
    );
    expect(requests).toContainEqual({
      method: "POST",
      path: "/api/prompts/parse",
      body: { text: "@maya at dusk", project_id: "p1" },
    });
  });
});
