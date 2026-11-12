import { useQueryClient } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { type FormEvent, type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchClient } from "@/api/client";
import { Button } from "@/components/ui/button";
import { inputClass } from "@/components/ui/misc";
import { useAuthStore } from "./store";

/** Shows a token form when the backend answers 401 (a browser that has not
 * signed in yet, or a remote backend). The desktop shell signs in by itself. */
export function AuthGate({ children }: { children: ReactNode }) {
  const needsToken = useAuthStore((s) => s.needsToken);
  if (!needsToken) return <>{children}</>;
  return <TokenForm />;
}

function TokenForm() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const setNeedsToken = useAuthStore((s) => s.setNeedsToken);
  const [token, setToken] = useState("");
  const [wrong, setWrong] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    const { response } = await fetchClient.POST("/api/auth/session", { body: { token: token.trim() } });
    setBusy(false);
    if (response.status === 204) {
      setNeedsToken(false);
      await queryClient.invalidateQueries();
    } else {
      setWrong(true);
    }
  }

  return (
    <main className="grid min-h-full place-items-center p-6">
      <form
        onSubmit={submit}
        className="flex w-full max-w-md flex-col gap-4 rounded-2xl border border-line bg-flat p-6"
        aria-labelledby="auth-title"
      >
        <KeyRound className="size-6 text-tungsten" aria-hidden />
        <h1 id="auth-title" className="font-display text-3xl font-semibold uppercase">
          {t("auth.title")}
        </h1>
        <p className="text-sm text-muted">{t("auth.body")}</p>
        <code className="rounded-md bg-raised px-2 py-1 font-mono text-xs">framestudio token</code>
        <label className="flex flex-col gap-1.5 text-sm font-medium">
          {t("auth.label")}
          <input
            className={inputClass}
            type="password"
            autoComplete="off"
            value={token}
            onChange={(e) => {
              setToken(e.target.value);
              setWrong(false);
            }}
            aria-invalid={wrong}
            required
          />
        </label>
        {wrong ? (
          <p role="alert" className="text-sm text-danger">
            {t("auth.wrong")}
          </p>
        ) : null}
        <Button type="submit" variant="primary" disabled={busy || !token.trim()}>
          {t("auth.submit")}
        </Button>
      </form>
    </main>
  );
}
