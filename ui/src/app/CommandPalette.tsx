import * as DialogPrimitive from "@radix-ui/react-dialog";
import { useNavigate } from "@tanstack/react-router";
import { Command } from "cmdk";
import { Cloud, Cpu, PanelLeft, PanelRight, SunMoon } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { $api } from "@/api/client";
import { NAV } from "./nav";
import { useShellStore } from "./shell-store";
import { useTheme } from "./theme";

function Item({ onSelect, icon, children }: { onSelect: () => void; icon: ReactNode; children: ReactNode }) {
  return (
    <Command.Item
      onSelect={onSelect}
      className="flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-sm aria-selected:bg-raised [&_svg]:size-4 [&_svg]:text-muted"
    >
      {icon}
      {children}
    </Command.Item>
  );
}

export function CommandPalette() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const open = useShellStore((s) => s.paletteOpen);
  const setOpen = useShellStore((s) => s.setPaletteOpen);
  const toggleInspector = useShellStore((s) => s.toggleInspector);
  const toggleSidebar = useShellStore((s) => s.toggleSidebar);
  const { toggle: toggleTheme } = useTheme();
  const fakeJob = $api.useMutation("post", "/api/jobs/fake");

  const run = (action: () => void) => () => {
    setOpen(false);
    action();
  };

  return (
    <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          className="fixed left-1/2 top-[18vh] z-50 w-[min(92vw,36rem)] -translate-x-1/2 overflow-hidden rounded-xl border border-line bg-flat shadow-2xl"
        >
          <DialogPrimitive.Title className="sr-only">{t("shell.openPalette")}</DialogPrimitive.Title>
          <Command label={t("shell.openPalette")} loop>
            <Command.Input
              autoFocus
              placeholder={t("palette.placeholder")}
              className="h-12 w-full border-b border-line bg-transparent px-4 text-base outline-none placeholder:text-muted"
            />
            <Command.List className="max-h-80 overflow-auto p-2">
              <Command.Empty className="px-3 py-6 text-center text-sm text-muted">{t("palette.empty")}</Command.Empty>
              <Command.Group
                heading={t("palette.go")}
                className="text-xs text-muted [&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-1.5"
              >
                {NAV.map((item) => (
                  <Item key={item.to} icon={<item.icon />} onSelect={run(() => void navigate({ to: item.to }))}>
                    {t("palette.goTo", { page: t(`nav.${item.key}`) })}
                  </Item>
                ))}
              </Command.Group>
              <Command.Group
                heading={t("palette.actions")}
                className="text-xs text-muted [&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-1.5"
              >
                <Item icon={<SunMoon />} onSelect={run(toggleTheme)}>
                  {t("palette.toggleTheme")}
                </Item>
                <Item icon={<PanelLeft />} onSelect={run(toggleSidebar)}>
                  {t("palette.toggleSidebar")}
                </Item>
                <Item icon={<PanelRight />} onSelect={run(toggleInspector)}>
                  {t("palette.toggleInspector")}
                </Item>
                <Item icon={<Cpu />} onSelect={run(() => fakeJob.mutate({ body: { kind: "local", seconds: 10 } }))}>
                  {t("palette.fakeLocal")}
                </Item>
                <Item icon={<Cloud />} onSelect={run(() => fakeJob.mutate({ body: { kind: "remote", seconds: 10 } }))}>
                  {t("palette.fakeRemote")}
                </Item>
              </Command.Group>
            </Command.List>
          </Command>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
