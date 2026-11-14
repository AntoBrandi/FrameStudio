import { fetchClient, type Schemas } from "@/api/client";

/** Prompt tokens (plan step M4.3). The token list is what a prompt is; its
 * text is a rendering. Text is turned into tokens only by the backend parser
 * (`POST /api/prompts/parse`), so pasted text, the API and the Director agree. */
export type TextToken = Schemas["TextToken"];
export type ElementToken = Schemas["ElementToken"];
export type MoveToken = Schemas["MoveToken"];
export type SkillToken = Schemas["SkillToken"];
export type Token = TextToken | ElementToken | MoveToken | SkillToken;

export const textToken = (text: string): TextToken => ({ type: "text", text });

/** Join adjacent text and drop empty text, as the backend does. */
export function mergeTokens(tokens: Token[]): Token[] {
  const out: Token[] = [];
  for (const token of tokens) {
    if (token.type === "text") {
      if (!token.text) continue;
      const last = out.at(-1);
      if (last?.type === "text") {
        out[out.length - 1] = textToken(last.text + token.text);
        continue;
      }
    }
    out.push(token);
  }
  return out;
}

export function renderTokens(tokens: Token[]): string {
  return tokens
    .map((token) => {
      switch (token.type) {
        case "text":
          return token.text;
        case "element":
          return `@${token.handle}`;
        case "move":
          return `#${token.name}`;
        case "skill":
          return `/${token.name}`;
      }
    })
    .join("");
}

export function tokensFromText(text: string): Token[] {
  return text ? [textToken(text)] : [];
}

/** Trim whitespace at both ends of a prompt, keeping its tokens. */
export function trimTokens(tokens: Token[]): Token[] {
  const out = mergeTokens(tokens);
  const first = out[0];
  if (first?.type === "text") out[0] = textToken(first.text.trimStart());
  const last = out.at(-1);
  if (last?.type === "text") out[out.length - 1] = textToken(last.text.trimEnd());
  return mergeTokens(out);
}

export const sameTokens = (a: Token[], b: Token[]) => JSON.stringify(a) === JSON.stringify(b);

/** True when the text could hold a token the parser would find. */
export const mayHoldTokens = (text: string) => /[@#/]/.test(text);

export interface Parsed {
  tokens: Token[];
  text: string;
  warnings: string[];
}

/** Text → tokens, or tokens → their canonical form, by the backend parser.
 * Falls back to plain text when the backend cannot be reached. */
export async function parsePrompt(input: { text: string } | { tokens: Token[] }, projectId?: string): Promise<Parsed> {
  const { data } = await fetchClient.POST("/api/prompts/parse", {
    body: { ...input, project_id: projectId ?? null },
  });
  if (data) return data as Parsed;
  const tokens = "text" in input ? tokensFromText(input.text) : input.tokens;
  return { tokens, text: renderTokens(tokens), warnings: [] };
}
