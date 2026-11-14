import { QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  $createRangeSelection,
  $createTextNode,
  $getRoot,
  $setSelection,
  type LexicalEditor,
  type ParagraphNode,
} from "lexical";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { makeQueryClient } from "@/app/App";
import { PromptEditor } from "@/features/generate/PromptEditor";
import type { Token } from "@/features/generate/tokens";
import { mockApi } from "@/test/api-mock";
import { addMove, clearMoves, movesIn, removeMove, swapMoves } from "./moves";
import { ShotChips } from "./ShotPickers";

// M5.3: camera moves are ordered #move chips in the prompt; the picker adds,
// reorders and removes them, and typing # suggests them.

const text = (value: string): Token => ({ type: "text", text: value });
const move = (name: string): Token => ({ type: "move", name });
const term = (id: string, label: string) => ({ id, label, phrase: "", tags: "", negative: null, profiles: {} });
const VOCAB = {
  genres: [],
  eras: [],
  tempos: [],
  bodies: [],
  lenses: [],
  apertures: [],
  focal_lengths: [],
  moves: [
    term("dolly-in", "Dolly in"),
    term("dolly-out", "Dolly out"),
    term("orbit", "Drone orbit"),
    term("rack-focus", "Rack focus"),
  ],
  lighting: [],
  palettes: [],
  previews: {},
};

describe("move tokens", () => {
  it("add, swap, remove and clear keep the text around them", () => {
    let tokens: Token[] = [text("A diner at night")];
    tokens = addMove(tokens, "dolly-in");
    tokens = addMove(tokens, "rack-focus");
    expect(tokens).toEqual([text("A diner at night "), move("dolly-in"), text(" "), move("rack-focus"), text(" ")]);
    expect(movesIn(swapMoves(tokens, 0, 1))).toEqual(["rack-focus", "dolly-in"]);
    expect(swapMoves(tokens, 0, 5)).toBe(tokens);
    expect(removeMove(tokens, 0)).toEqual([text("A diner at night "), move("rack-focus"), text(" ")]);
    expect(clearMoves(tokens)).toEqual([text("A diner at night ")]);
    // Inline moves stay where they are when reordered.
    const inline = [text("Rain "), move("orbit"), text(" on glass "), move("dolly-in")];
    expect(swapMoves(inline, 0, 1)).toEqual([text("Rain "), move("dolly-in"), text(" on glass "), move("orbit")]);
  });
});

function Harness({ initial }: { initial: Token[] }) {
  const [tokens, setTokens] = useState<Token[]>(initial);
  const [client] = useState(makeQueryClient);
  const editor = {
    list: movesIn(tokens),
    add: (id: string) => setTokens((t) => addMove(t, id)),
    remove: (i: number) => setTokens((t) => removeMove(t, i)),
    swap: (a: number, b: number) => setTokens((t) => swapMoves(t, a, b)),
    clear: () => setTokens((t) => clearMoves(t)),
  };
  return (
    <QueryClientProvider client={client}>
      <ShotChips shot={null} onChange={() => undefined} video moves={editor} />
      <PromptEditor
        value={tokens}
        onChange={setTokens}
        onSubmit={() => undefined}
        moves={VOCAB.moves.map((m) => ({ id: m.id, label: m.label }))}
        label="Prompt"
        placeholder="Describe"
      />
      <output data-testid="tokens">{JSON.stringify(tokens)}</output>
    </QueryClientProvider>
  );
}

const tokensNow = () => JSON.parse(screen.getByTestId("tokens").textContent!) as Token[];

describe("moves picker", () => {
  it("adds moves in order, reorders and removes them", async () => {
    mockApi({ "GET /api/cinema/vocab": VOCAB });
    const user = userEvent.setup();
    render(<Harness initial={[text("A diner at night")]} />);
    await user.click(await screen.findByTestId("chip-moves"));
    const picker = await screen.findByTestId("moves-picker");
    await user.click(within(picker).getByRole("button", { name: "Add Dolly in" }));
    await user.click(within(picker).getByRole("button", { name: "Add Rack focus" }));
    expect(movesIn(tokensNow())).toEqual(["dolly-in", "rack-focus"]);
    const inShot = within(picker).getByTestId("moves-in-shot");
    expect(inShot).toHaveTextContent("1. Dolly in");
    await user.click(within(picker).getByRole("button", { name: "Move Rack focus earlier" }));
    expect(movesIn(tokensNow())).toEqual(["rack-focus", "dolly-in"]);
    await user.type(within(picker).getByTestId("moves-search"), "orb");
    expect(within(within(picker).getByTestId("move-library")).getAllByRole("button")).toHaveLength(1);
    await user.click(within(picker).getByRole("button", { name: "Remove Dolly in" }));
    expect(movesIn(tokensNow())).toEqual(["rack-focus"]);
    await user.click(within(picker).getByTestId("moves-done"));
    expect(screen.getByTestId("chip-moves")).toHaveAccessibleName("Moves: Rack focus");
    // The chips in the prompt are move chips.
    const chip = screen.getByTestId("prompt-input").querySelector('[data-kind="move"]');
    expect(chip).toHaveTextContent("#rack-focus");
    await user.click(screen.getByTestId("chip-moves-clear"));
    expect(movesIn(tokensNow())).toEqual([]);
  });

  it("typing # suggests moves; picking one makes a chip", async () => {
    mockApi({ "GET /api/cinema/vocab": VOCAB });
    render(<Harness initial={[text("Rain on glass ")]} />);
    const input = screen.getByTestId("prompt-input") as HTMLElement & { __lexicalEditor: LexicalEditor };
    act(() => {
      input.focus();
      input.__lexicalEditor.update(
        () => {
          const paragraph = $getRoot().getFirstChildOrThrow() as ParagraphNode;
          const typed = $createTextNode("Rain on glass #dol");
          paragraph.getFirstChildOrThrow().replace(typed);
          const selection = $createRangeSelection();
          selection.anchor.set(typed.getKey(), 18, "text");
          selection.focus.set(typed.getKey(), 18, "text");
          $setSelection(selection);
        },
        { discrete: true },
      );
    });
    const menu = await screen.findByTestId("move-suggestions");
    expect(
      within(menu)
        .getAllByRole("option")
        .map((o) => o.dataset.move),
    ).toEqual(["dolly-in", "dolly-out"]);
    fireEvent.mouseDown(within(menu).getByText("Dolly out"));
    await waitFor(() => expect(tokensNow()).toEqual([text("Rain on glass "), move("dolly-out"), text(" ")]));
  });
});
