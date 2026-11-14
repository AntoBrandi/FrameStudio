import type { FolderOut } from "./api";

export type FolderColor = FolderOut["color"];

export const FOLDER_COLORS: FolderColor[] = [
  "tungsten",
  "daylight",
  "green",
  "rose",
  "violet",
  "teal",
  "amber",
  "slate",
];

const VALUES: Record<FolderColor, string> = {
  tungsten: "var(--tungsten)",
  daylight: "var(--daylight)",
  green: "var(--ok)",
  rose: "#e66b8f",
  violet: "#a487f0",
  teal: "#3fb8b0",
  amber: "#e0b33c",
  slate: "var(--muted)",
};

export function folderColor(color: FolderColor): string {
  return VALUES[color] ?? VALUES.tungsten;
}
