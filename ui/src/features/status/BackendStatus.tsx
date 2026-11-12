import { useTranslation } from "react-i18next";
import { $api } from "@/api/client";
import { Tooltip } from "@/components/ui/misc";
import { cn } from "@/lib/utils";

/** Live backend reachability, from the public health endpoint. */
export function useBackendHealth() {
  return $api.useQuery("get", "/api/health", undefined, {
    refetchInterval: 5_000,
    retry: false,
    staleTime: 0,
  });
}

export function BackendStatus({ compact = false }: { compact?: boolean }) {
  const { t } = useTranslation();
  const { data, isError, isPending } = useBackendHealth();
  const state = isError ? "offline" : isPending ? "connecting" : "connected";
  const label = t(`status.${state}`);
  const dot = (
    <span
      aria-hidden
      className={cn(
        "inline-block size-2 rounded-full",
        state === "connected" && "bg-ok",
        state === "connecting" && "animate-pulse bg-muted",
        state === "offline" && "bg-danger",
      )}
    />
  );
  if (compact) {
    return (
      <Tooltip label={data ? `${label} · ${t("status.version", { version: data.version })}` : label}>
        <span
          role="status"
          aria-label={label}
          data-testid="backend-status"
          data-state={state}
          className="flex size-8 items-center justify-center"
        >
          {dot}
        </span>
      </Tooltip>
    );
  }
  return (
    <span
      role="status"
      data-testid="backend-status"
      data-state={state}
      className="inline-flex items-center gap-2 text-sm"
    >
      {dot}
      <span>{label}</span>
      {data ? <span className="font-mono text-xs text-muted">v{data.version}</span> : null}
    </span>
  );
}
