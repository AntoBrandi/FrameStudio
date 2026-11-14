import { LexicalComposer } from "@lexical/react/LexicalComposer";
import { useLexicalComposerContext } from "@lexical/react/LexicalComposerContext";
import { ContentEditable } from "@lexical/react/LexicalContentEditable";
import { LexicalErrorBoundary } from "@lexical/react/LexicalErrorBoundary";
import { HistoryPlugin } from "@lexical/react/LexicalHistoryPlugin";
import { OnChangePlugin } from "@lexical/react/LexicalOnChangePlugin";
import { PlainTextPlugin } from "@lexical/react/LexicalPlainTextPlugin";
import {
  LexicalTypeaheadMenuPlugin,
  MenuOption,
  useBasicTypeaheadTriggerMatch,
} from "@lexical/react/LexicalTypeaheadMenuPlugin";
import {
  $applyNodeReplacement,
  $createLineBreakNode,
  $createParagraphNode,
  $createTextNode,
  $getRoot,
  $getSelection,
  $isElementNode,
  $isLineBreakNode,
  $isRangeSelection,
  $isTextNode,
  BLUR_COMMAND,
  COMMAND_PRIORITY_CRITICAL,
  COMMAND_PRIORITY_HIGH,
  COMMAND_PRIORITY_LOW,
  COMMAND_PRIORITY_NORMAL,
  type EditorConfig,
  type EditorState,
  KEY_ENTER_COMMAND,
  type LexicalEditor,
  type LexicalNode,
  type NodeKey,
  PASTE_COMMAND,
  type SerializedTextNode,
  type Spread,
  TextNode,
} from "lexical";
import { MapPin, Move, Package, Sparkles, UserRound } from "lucide-react";
import { useEffect, useEffectEvent, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { $api } from "@/api/client";
import type { ElementOut } from "@/features/elements/api";
import { cn } from "@/lib/utils";
import { mayHoldTokens, mergeTokens, parsePrompt, renderTokens, sameTokens, textToken, type Token } from "./tokens";

/** The prompt box (plan steps M2.5, M4.3): a Lexical plain-text editor whose
 * contents are a token list. `@element` (and later `#move`, `/skill`) tokens
 * are chips; typing `@` suggests elements, and pasted text goes through the
 * backend parser so its handles become chips too. */

type Kind = "element" | "move" | "skill";
const SIGIL: Record<Kind, string> = { element: "@", move: "#", skill: "/" };

type SerializedTokenNode = Spread<{ kind: Kind; name: string; elementId: string | null }, SerializedTextNode>;

/** A token chip. It behaves as one unit: the caret skips it and Backspace
 * removes all of it. */
export class TokenNode extends TextNode {
  __kind: Kind;
  __name: string;
  __elementId: string | null;

  static getType(): string {
    return "prompt-token";
  }

  static clone(node: TokenNode): TokenNode {
    return new TokenNode(node.__kind, node.__name, node.__elementId, node.__key);
  }

  static importJSON(json: SerializedTokenNode): TokenNode {
    return $createTokenNode(json.kind, json.name, json.elementId);
  }

  constructor(kind: Kind, name: string, elementId: string | null = null, key?: NodeKey) {
    super(`${SIGIL[kind]}${name}`, key);
    this.__kind = kind;
    this.__name = name;
    this.__elementId = elementId;
  }

  exportJSON(): SerializedTokenNode {
    return { ...super.exportJSON(), kind: this.__kind, name: this.__name, elementId: this.__elementId };
  }

  createDOM(config: EditorConfig): HTMLElement {
    const dom = super.createDOM(config);
    const unknown = this.__kind === "element" && !this.__elementId;
    dom.className = cn(
      "rounded px-0.5 font-medium",
      unknown
        ? "bg-danger/10 text-danger underline decoration-dotted"
        : this.__kind === "move"
          ? "bg-daylight/15 text-daylight"
          : "bg-tungsten/15 text-tungsten",
    );
    dom.dataset.testid = "prompt-token";
    dom.dataset.kind = this.__kind;
    if (unknown) dom.dataset.unknown = "true";
    return dom;
  }

  token(): Token {
    const self = this.getLatest();
    if (self.__kind === "element") return { type: "element", handle: self.__name, element_id: self.__elementId };
    return { type: self.__kind, name: self.__name };
  }

  isTextEntity(): true {
    return true;
  }

  canInsertTextBefore(): boolean {
    return false;
  }

  canInsertTextAfter(): boolean {
    return false;
  }
}

export function $createTokenNode(kind: Kind, name: string, elementId: string | null = null): TokenNode {
  const node = new TokenNode(kind, name, elementId);
  node.setMode("token").toggleDirectionless();
  return $applyNodeReplacement(node);
}

const $isTokenNode = (node: LexicalNode | null | undefined): node is TokenNode => node instanceof TokenNode;

/** The editor's contents as tokens (call inside a read or update). */
export function $readTokens(): Token[] {
  const out: Token[] = [];
  $getRoot()
    .getChildren()
    .forEach((block, index) => {
      if (index > 0) out.push(textToken("\n"));
      if (!$isElementNode(block)) return;
      for (const child of block.getChildren()) {
        if ($isTokenNode(child)) out.push(child.token());
        else if ($isLineBreakNode(child)) out.push(textToken("\n"));
        else out.push(textToken(child.getTextContent()));
      }
    });
  return mergeTokens(out);
}

/** Editor nodes for tokens: chips, text and line breaks. */
export function $nodesFor(tokens: Token[]): LexicalNode[] {
  const nodes: LexicalNode[] = [];
  for (const token of tokens) {
    if (token.type === "text") {
      token.text.split("\n").forEach((part, index) => {
        if (index > 0) nodes.push($createLineBreakNode());
        if (part) nodes.push($createTextNode(part));
      });
    } else if (token.type === "element") {
      nodes.push($createTokenNode("element", token.handle, token.element_id ?? null));
    } else {
      nodes.push($createTokenNode(token.type, token.name));
    }
  }
  return nodes;
}

/** Replace the editor's contents with ``tokens`` (call inside an update). */
export function $writeTokens(tokens: Token[]): void {
  const root = $getRoot();
  root.clear();
  const paragraph = $createParagraphNode();
  paragraph.append(...$nodesFor(tokens));
  root.append(paragraph);
  paragraph.selectEnd();
}

const readState = (state: EditorState): Token[] => state.read($readTokens);

/** Keeps the editor in step with `value` when it changes from outside
 * (Enhance prompt, Recreate, a parsed paste), without fighting the user's
 * own typing. */
function ValuePlugin({ value }: { value: Token[] }) {
  const [editor] = useLexicalComposerContext();
  useEffect(() => {
    if (sameTokens(readState(editor.getEditorState()), value)) return;
    editor.update(() => $writeTokens(value));
  }, [editor, value]);
  return null;
}

/** Enter sends; Shift+Enter (or Enter while composing text) adds a line. */
function SubmitPlugin({ onSubmit }: { onSubmit: () => void }) {
  const [editor] = useLexicalComposerContext();
  useEffect(
    () =>
      editor.registerCommand(
        KEY_ENTER_COMMAND,
        (event: KeyboardEvent | null) => {
          if (!event || event.shiftKey || event.isComposing) return false;
          event.preventDefault();
          onSubmit();
          return true;
        },
        // Below the @ menu and its guard (CRITICAL, HIGH): Enter picks there first.
        COMMAND_PRIORITY_NORMAL,
      ),
    [editor, onSubmit],
  );
  return null;
}

/** Pasted text with possible tokens goes through the backend parser. */
function PastePlugin({ projectId }: { projectId?: string }) {
  const [editor] = useLexicalComposerContext();
  useEffect(
    () =>
      editor.registerCommand(
        PASTE_COMMAND,
        (event) => {
          const text = "clipboardData" in event ? (event.clipboardData?.getData("text/plain") ?? "") : "";
          if (!text || !mayHoldTokens(text)) return false;
          event.preventDefault();
          void parsePrompt({ text }, projectId).then(({ tokens }) =>
            editor.update(() => {
              const selection = $getSelection();
              if ($isRangeSelection(selection)) selection.insertNodes($nodesFor(tokens));
            }),
          );
          return true;
        },
        COMMAND_PRIORITY_HIGH,
      ),
    [editor, projectId],
  );
  return null;
}

function BlurPlugin({ onBlur }: { onBlur?: () => void }) {
  const [editor] = useLexicalComposerContext();
  useEffect(
    () =>
      editor.registerCommand(
        BLUR_COMMAND,
        () => {
          onBlur?.();
          return false;
        },
        COMMAND_PRIORITY_LOW,
      ),
    [editor, onBlur],
  );
  return null;
}

type Sigil = "@" | "#";
const QUERY_AT_CARET: Record<Sigil, RegExp> = {
  "@": /(?:^|[\s(])@([A-Za-z0-9_-]*)$/,
  "#": /(?:^|[\s(])#([A-Za-z0-9_-]*)$/,
};

/** The "@query" (or "#query") typed just before the caret: its node and where it starts. */
function $queryAtCaret(sigil: Sigil = "@"): { node: TextNode; start: number; end: number; query: string } | null {
  const selection = $getSelection();
  if (!$isRangeSelection(selection) || !selection.isCollapsed()) return null;
  const node = selection.anchor.getNode();
  if (!$isTextNode(node) || $isTokenNode(node)) return null;
  const end = selection.anchor.offset;
  const found = QUERY_AT_CARET[sigil].exec(node.getTextContent().slice(0, end));
  if (!found) return null;
  const query = found[1] ?? "";
  return { node, start: end - query.length - 1, end, query: query.toLowerCase() };
}

/** Replace the query just before the caret with a chip and a space. False
 * when there is no query at the caret (yet). */
function $replaceQuery(sigil: Sigil, chip: () => TokenNode): boolean {
  const at = $queryAtCaret(sigil);
  if (!at) return false;
  const parts = at.node.splitText(at.start, at.end);
  const typed = parts[at.start === 0 ? 0 : 1];
  if (!typed) return false;
  const node = chip();
  typed.replace(node);
  const space = $createTextNode(" ");
  node.insertAfter(space);
  space.selectEnd();
  return true;
}

/** Put a chip in place of the query. Chrome inserts typed characters before
 * the editor's state catches up, so an Enter right after fast typing can see
 * an older caret: try again for a few frames. */
function pickToken(editor: LexicalEditor, sigil: Sigil, chip: () => TokenNode, tries = 6): void {
  editor.update(() => {
    if (!$replaceQuery(sigil, chip) && tries > 0) {
      setTimeout(() => pickToken(editor, sigil, chip, tries - 1), 16);
    }
  });
}

function pickElement(editor: LexicalEditor, element: ElementOut): void {
  pickToken(editor, "@", () => $createTokenNode("element", element.handle, element.id));
}

const matches = (element: ElementOut, query: string) =>
  element.handle.includes(query) || element.name.toLowerCase().includes(query);

class ElementOption extends MenuOption {
  constructor(readonly element: ElementOut) {
    super(element.id);
  }
}

const ICONS = { character: UserRound, location: MapPin, prop: Package, auto: Sparkles } as const;
// The default punctuation, less - and _ (they belong in handles).
const PUNCTUATION = "\\.,\\+\\*\\?\\$\\@\\|#{}\\(\\)\\^\\[\\]\\\\/!%'\"~=<>:;";

/** Typing `@` suggests elements (the project's and the global ones), with
 * their pictures; picking one puts its chip in the prompt. */
function MentionsPlugin({ projectId }: { projectId?: string }) {
  const { t } = useTranslation();
  const [editor] = useLexicalComposerContext();
  const [query, setQuery] = useState<string | null>(null);
  const trigger = useBasicTypeaheadTriggerMatch("@", { minLength: 0, maxLength: 48, punctuation: PUNCTUATION });
  const { data: found = [], isFetching } = $api.useQuery(
    "get",
    "/api/elements",
    { params: { query: { ...(projectId ? { project_id: projectId } : {}), ...(query ? { q: query } : {}) } } },
    { enabled: query !== null, placeholderData: (previous) => previous },
  );
  // Results of an earlier query stay on screen until the new ones arrive, so
  // they are filtered by the current query too (Enter must pick a match).
  const options = useMemo(() => {
    const q = (query ?? "").toLowerCase();
    return found
      .filter((element) => matches(element, q))
      .slice(0, 8)
      .map((element) => new ElementOption(element));
  }, [found, query]);

  // Enter while an "@query" is being typed picks an element, never sends the
  // prompt. The menu handles it when its state has caught up; when typing is
  // faster than that, this picks the first match for the text actually typed,
  // or waits for the results if they are still loading.
  const pendingPick = useRef(false);
  const pick = useEffectEvent((): "picked" | "loading" | "none" => {
    const at = editor.getEditorState().read($queryAtCaret);
    if (!at) return "none";
    const first = found.find((element) => matches(element, at.query));
    if (first) {
      pickElement(editor, first);
      return "picked";
    }
    return isFetching ? "loading" : "none";
  });
  useEffect(() => {
    return editor.registerCommand(
      KEY_ENTER_COMMAND,
      (event: KeyboardEvent | null) => {
        if (!event || event.shiftKey || event.isComposing) return false;
        const outcome = pick();
        if (outcome === "none") return false; // nothing matches: Enter sends
        event.preventDefault();
        pendingPick.current = outcome === "loading";
        return true;
      },
      COMMAND_PRIORITY_HIGH,
    );
  }, [editor]);
  useEffect(() => {
    if (!pendingPick.current) return;
    const at = editor.getEditorState().read($queryAtCaret);
    const first = at && found.find((element) => matches(element, at.query));
    if (first) pickElement(editor, first);
    if (!at || first || !isFetching) pendingPick.current = false;
  }, [editor, found, isFetching]);

  return (
    <LexicalTypeaheadMenuPlugin<ElementOption>
      triggerFn={trigger}
      // Above Enter-to-send while the menu is open: Enter picks the element.
      commandPriority={COMMAND_PRIORITY_CRITICAL}
      onQueryChange={setQuery}
      options={options}
      onSelectOption={(option, _node, closeMenu) => {
        // The "@query" before the caret is found again here: the plugin's own
        // match can lag behind fast typing.
        pickElement(editor, option.element);
        closeMenu();
      }}
      menuRenderFn={(anchor, { selectedIndex, selectOptionAndCleanUp, setHighlightedIndex }) =>
        anchor.current && query !== null
          ? createPortal(
              <div
                className="absolute bottom-full left-0 z-50 mb-1 w-72 overflow-hidden rounded-xl border border-line bg-flat shadow-2xl"
                data-testid="element-suggestions"
              >
                {options.length === 0 ? (
                  <p className="px-3 py-2 text-xs text-muted">{t("tokens.noElements", { query })}</p>
                ) : (
                  <ul role="listbox" aria-label={t("tokens.suggestions")}>
                    {options.map((option, index) => {
                      const element = option.element;
                      const Icon = ICONS[element.category];
                      return (
                        <li
                          key={option.key}
                          ref={(node) => option.setRefElement(node)}
                          role="option"
                          aria-selected={selectedIndex === index}
                          tabIndex={-1}
                          onMouseEnter={() => setHighlightedIndex(index)}
                          onMouseDown={(event) => {
                            event.preventDefault();
                            selectOptionAndCleanUp(option);
                          }}
                          className={cn(
                            "flex cursor-pointer items-center gap-2 px-2 py-1.5",
                            selectedIndex === index && "bg-raised",
                          )}
                          data-handle={element.handle}
                        >
                          <span className="flex size-8 shrink-0 items-center justify-center overflow-hidden rounded-md bg-raised">
                            {element.cover_url ? (
                              <img src={element.cover_url} alt="" className="size-full object-cover" />
                            ) : (
                              <Icon className="size-4 text-muted" aria-hidden />
                            )}
                          </span>
                          <span className="flex min-w-0 flex-col">
                            <span className="truncate text-sm">{element.name}</span>
                            <span className="truncate font-mono text-xs text-tungsten">@{element.handle}</span>
                          </span>
                          {projectId && !element.project_id ? (
                            <span className="ml-auto text-[10px] uppercase text-muted">{t("tokens.global")}</span>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>,
              anchor.current,
            )
          : null
      }
    />
  );
}

export interface MoveChoice {
  id: string;
  label: string;
}

class MoveOption extends MenuOption {
  constructor(readonly move: MoveChoice) {
    super(move.id);
  }
}

const moveMatches = (move: MoveChoice, query: string) =>
  move.id.includes(query) || move.label.toLowerCase().includes(query);

/** Typing # suggests camera moves (M5.3); picking one puts its chip in the
 * prompt. Moves are local data, so Enter can always pick at once. */
function MovesPlugin({ moves }: { moves: MoveChoice[] }) {
  const { t } = useTranslation();
  const [editor] = useLexicalComposerContext();
  const [query, setQuery] = useState<string | null>(null);
  const trigger = useBasicTypeaheadTriggerMatch("#", { minLength: 0, maxLength: 48, punctuation: PUNCTUATION });
  const options = useMemo(() => {
    const q = (query ?? "").toLowerCase();
    return moves
      .filter((move) => moveMatches(move, q))
      .slice(0, 8)
      .map((move) => new MoveOption(move));
  }, [moves, query]);
  const pick = (move: MoveChoice) => pickToken(editor, "#", () => $createTokenNode("move", move.id));
  const guard = useEffectEvent((): boolean => {
    const at = editor.getEditorState().read(() => $queryAtCaret("#"));
    const first = at && moves.find((move) => moveMatches(move, at.query));
    if (!first) return false; // not a move (a colour such as #ff0000): Enter sends
    pick(first);
    return true;
  });
  useEffect(
    () =>
      editor.registerCommand(
        KEY_ENTER_COMMAND,
        (event: KeyboardEvent | null) => {
          if (!event || event.shiftKey || event.isComposing) return false;
          if (!guard()) return false;
          event.preventDefault();
          return true;
        },
        COMMAND_PRIORITY_HIGH,
      ),
    [editor],
  );

  return (
    <LexicalTypeaheadMenuPlugin<MoveOption>
      triggerFn={trigger}
      commandPriority={COMMAND_PRIORITY_CRITICAL}
      onQueryChange={setQuery}
      options={options}
      onSelectOption={(option, _node, closeMenu) => {
        pick(option.move);
        closeMenu();
      }}
      menuRenderFn={(anchor, { selectedIndex, selectOptionAndCleanUp, setHighlightedIndex }) =>
        anchor.current && query !== null && options.length > 0
          ? createPortal(
              <div
                className="absolute bottom-full left-0 z-50 mb-1 w-64 overflow-hidden rounded-xl border border-line bg-flat shadow-2xl"
                data-testid="move-suggestions"
              >
                <ul role="listbox" aria-label={t("tokens.moves")}>
                  {options.map((option, index) => (
                    <li
                      key={option.key}
                      ref={(node) => option.setRefElement(node)}
                      role="option"
                      aria-selected={selectedIndex === index}
                      tabIndex={-1}
                      onMouseEnter={() => setHighlightedIndex(index)}
                      onMouseDown={(event) => {
                        event.preventDefault();
                        selectOptionAndCleanUp(option);
                      }}
                      className={cn(
                        "flex cursor-pointer items-center gap-2 px-3 py-1.5",
                        selectedIndex === index && "bg-raised",
                      )}
                      data-move={option.move.id}
                    >
                      <Move className="size-3.5 shrink-0 text-daylight" aria-hidden />
                      <span className="flex-1 truncate text-sm">{option.move.label}</span>
                      <span className="font-mono text-[11px] text-daylight">#{option.move.id}</span>
                    </li>
                  ))}
                </ul>
              </div>,
              anchor.current,
            )
          : null
      }
    />
  );
}

export function PromptEditor({
  value,
  onChange,
  onSubmit,
  onBlur,
  projectId,
  moves,
  label,
  placeholder,
  maxLength,
  className,
}: {
  value: Token[];
  onChange: (tokens: Token[]) => void;
  onSubmit: () => void;
  /** When the editor loses focus (the composer tidies the tokens then). */
  onBlur?: () => void;
  /** Whose elements `@` suggests and pasted handles name (with the global ones). */
  projectId?: string;
  /** Camera moves `#` suggests (video only, M5.3). */
  moves?: MoveChoice[];
  label: string;
  placeholder: string;
  maxLength?: number | null;
  className?: string;
}) {
  const config = {
    namespace: "framestudio-prompt",
    nodes: [TokenNode],
    onError: (error: Error) => {
      throw error;
    },
  };
  const length = renderTokens(value).length;
  const over = maxLength != null && length > maxLength;
  return (
    <LexicalComposer initialConfig={config}>
      <div className={cn("relative", className)}>
        <PlainTextPlugin
          contentEditable={
            <ContentEditable
              aria-label={label}
              aria-placeholder={placeholder}
              aria-invalid={over || undefined}
              placeholder={
                <div className="pointer-events-none absolute left-1 top-0 text-[15px] leading-relaxed text-muted">
                  {placeholder}
                </div>
              }
              className="max-h-40 min-h-[3.25rem] overflow-y-auto px-1 text-[15px] leading-relaxed outline-none"
              data-testid="prompt-input"
            />
          }
          ErrorBoundary={LexicalErrorBoundary}
        />
        <HistoryPlugin />
        <OnChangePlugin ignoreSelectionChange onChange={(state) => onChange(readState(state))} />
        <ValuePlugin value={value} />
        <SubmitPlugin onSubmit={onSubmit} />
        <PastePlugin projectId={projectId} />
        <BlurPlugin onBlur={onBlur} />
        <MentionsPlugin projectId={projectId} />
        {moves?.length ? <MovesPlugin moves={moves} /> : null}
        {maxLength != null && length > maxLength * 0.8 ? (
          <span
            className={cn("absolute bottom-0 right-1 font-mono text-[11px]", over ? "text-danger" : "text-muted")}
            aria-live="polite"
          >
            {length}/{maxLength}
          </span>
        ) : null}
      </div>
    </LexicalComposer>
  );
}
