import {
  Clapperboard,
  Film,
  FolderOpen,
  House,
  Image,
  type LucideIcon,
  Settings,
  UsersRound,
  Workflow,
} from "lucide-react";

export interface NavItem {
  to: "/" | "/image" | "/video" | "/studio" | "/assets" | "/elements" | "/canvas" | "/settings";
  key: "home" | "image" | "video" | "studio" | "assets" | "elements" | "canvas" | "settings";
  icon: LucideIcon;
}

/** Every top-level route, in navigation order. */
export const NAV: readonly NavItem[] = [
  { to: "/", key: "home", icon: House },
  { to: "/image", key: "image", icon: Image },
  { to: "/video", key: "video", icon: Clapperboard },
  { to: "/studio", key: "studio", icon: Film },
  { to: "/assets", key: "assets", icon: FolderOpen },
  { to: "/elements", key: "elements", icon: UsersRound },
  { to: "/canvas", key: "canvas", icon: Workflow },
  { to: "/settings", key: "settings", icon: Settings },
];
