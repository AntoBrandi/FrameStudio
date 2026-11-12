import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { useState } from "react";
import { Toaster } from "sonner";
import { TooltipProvider } from "@/components/ui/misc";
import { AuthGate } from "@/features/auth/AuthGate";
import type { router as appRouter } from "./router";
import { useTheme } from "./theme";

/** Top-right, below the header: the bottom edge belongs to the docked composer. */
function ThemedToaster() {
  const { resolved } = useTheme();
  return <Toaster theme={resolved} position="top-right" offset={{ top: 60, right: 16 }} richColors closeButton />;
}

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 10_000, refetchOnWindowFocus: false, retry: 1 },
    },
  });
}

export function App({ router, queryClient }: { router: typeof appRouter; queryClient?: QueryClient }) {
  const [client] = useState(() => queryClient ?? makeQueryClient());
  return (
    <QueryClientProvider client={client}>
      <TooltipProvider delayDuration={400}>
        <AuthGate>
          <RouterProvider router={router} />
        </AuthGate>
        <ThemedToaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}
