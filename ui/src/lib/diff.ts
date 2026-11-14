/** Word-level diff for showing what Enhance prompt changed (plan step M2.6). */

export interface DiffPart {
  kind: "same" | "added" | "removed";
  text: string;
}

/** Words with the whitespace that follows them, so joining gives the text back. */
export function tokens(text: string): string[] {
  return text.match(/\S+\s*|\s+/g) ?? [];
}

const norm = (token: string) => token.trim().toLowerCase();

/** The changes from `before` to `after`, word by word (longest common
 * subsequence; prompts are short enough for the quadratic table). */
export function diffWords(before: string, after: string): DiffPart[] {
  const a = tokens(before);
  const b = tokens(after);
  const rows = a.length + 1;
  const cols = b.length + 1;
  const table = new Uint16Array(rows * cols);
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      table[i * cols + j] =
        norm(a[i]!) === norm(b[j]!)
          ? table[(i + 1) * cols + j + 1]! + 1
          : Math.max(table[(i + 1) * cols + j]!, table[i * cols + j + 1]!);
    }
  }
  const parts: DiffPart[] = [];
  const push = (kind: DiffPart["kind"], text: string) => {
    const last = parts.at(-1);
    if (last && last.kind === kind) last.text += text;
    else parts.push({ kind, text });
  };
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (norm(a[i]!) === norm(b[j]!)) {
      push("same", b[j]!);
      i++;
      j++;
    } else if (table[(i + 1) * cols + j]! >= table[i * cols + j + 1]!) {
      push("removed", a[i++]!);
    } else {
      push("added", b[j++]!);
    }
  }
  while (i < a.length) push("removed", a[i++]!);
  while (j < b.length) push("added", b[j++]!);
  return parts;
}
