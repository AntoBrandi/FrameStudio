import { LexicalComposer } from "@lexical/react/LexicalComposer";
import { useLexicalComposerContext } from "@lexical/react/LexicalComposerContext";
import { ContentEditable } from "@lexical/react/LexicalContentEditable";
import { LexicalErrorBoundary } from "@lexical/react/LexicalErrorBoundary";
import { HistoryPlugin } from "@lexical/react/LexicalHistoryPlugin";
import { OnChangePlugin } from "@lexical/react/LexicalOnChangePlugin";
import { PlainTextPlugin } from "@lexical/react/LexicalPlainTextPlugin";
import {
  $createParagraphNode,
  $createTextNode,
  $getRoot,
  COMMAND_PRIORITY_HIGH,
  type EditorState,
  KEY_ENTER_COMMAND,
} from "lexical";
import { useEffect } from "react";
import { cn } from "@/lib/utils";

/** The prompt box (plan step M2.5): a Lexical plain-text editor. Prompts are
 * plain text today; `@element` and `#move` tokens (M4) become custom nodes in
 * this editor, so the text and the token list come from one place. */

function textOf(state: EditorState): string {
  return state.read(() => $getRoot().getTextContent());
}

/** Keeps the editor in step with `value` when it changes from outside
 * (Enhance prompt, Recreate), without fighting the user's own typing. */
function ValuePlugin({ value }: { value: string }) {
  const [editor] = useLexicalComposerContext();
  useEffect(() => {
    if (textOf(editor.getEditorState()) === value) return;
    editor.update(() => {
      const root = $getRoot();
      root.clear();
      const paragraph = $createParagraphNode();
      if (value) paragraph.append($createTextNode(value));
      root.append(paragraph);
      paragraph.selectEnd();
    });
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
        COMMAND_PRIORITY_HIGH,
      ),
    [editor, onSubmit],
  );
  return null;
}

export function PromptEditor({
  value,
  onChange,
  onSubmit,
  label,
  placeholder,
  maxLength,
  className,
}: {
  value: string;
  onChange: (text: string) => void;
  onSubmit: () => void;
  label: string;
  placeholder: string;
  maxLength?: number | null;
  className?: string;
}) {
  const config = {
    namespace: "framestudio-prompt",
    onError: (error: Error) => {
      throw error;
    },
  };
  const over = maxLength != null && value.length > maxLength;
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
        <OnChangePlugin ignoreSelectionChange onChange={(state) => onChange(textOf(state))} />
        <ValuePlugin value={value} />
        <SubmitPlugin onSubmit={onSubmit} />
        {maxLength != null && value.length > maxLength * 0.8 ? (
          <span
            className={cn("absolute bottom-0 right-1 font-mono text-[11px]", over ? "text-danger" : "text-muted")}
            aria-live="polite"
          >
            {value.length}/{maxLength}
          </span>
        ) : null}
      </div>
    </LexicalComposer>
  );
}
