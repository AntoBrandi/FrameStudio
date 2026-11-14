import { mergeTokens, type Token, textToken } from "@/features/generate/tokens";

/** Camera moves live in the prompt as #move tokens (M5.3): their order there
 * is the order they happen in. These edit them in a token list. */

export function movesIn(tokens: Token[]): string[] {
  return tokens.flatMap((token) => (token.type === "move" ? [token.name] : []));
}

/** Add a move at the end of the prompt. */
export function addMove(tokens: Token[], id: string): Token[] {
  const last = tokens.at(-1);
  const gap = last && !(last.type === "text" && /\s$/.test(last.text)) ? [textToken(" ")] : [];
  return mergeTokens([...tokens, ...gap, { type: "move", name: id }, textToken(" ")]);
}

/** Remove the ``index``-th move (and one space beside it). */
export function removeMove(tokens: Token[], index: number): Token[] {
  let seen = -1;
  const out: Token[] = [];
  for (const token of tokens) {
    if (token.type === "move" && ++seen === index) continue;
    out.push(token);
  }
  return tidy(out);
}

/** Swap the ``a``-th and ``b``-th moves, leaving the text around them. */
export function swapMoves(tokens: Token[], a: number, b: number): Token[] {
  const names = movesIn(tokens);
  if (a < 0 || b < 0 || a >= names.length || b >= names.length) return tokens;
  [names[a], names[b]] = [names[b]!, names[a]!];
  let seen = -1;
  return tokens.map((token) => (token.type === "move" ? { ...token, name: names[++seen]! } : token));
}

export function clearMoves(tokens: Token[]): Token[] {
  return tidy(tokens.filter((token) => token.type !== "move"));
}

/** Merge text and close the double spaces a removed chip leaves. */
function tidy(tokens: Token[]): Token[] {
  return mergeTokens(tokens).map((token) =>
    token.type === "text" ? textToken(token.text.replace(/ {2,}/g, " ")) : token,
  );
}
