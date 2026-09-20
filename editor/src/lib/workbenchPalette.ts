export const WORKBENCH_PALETTES = [
  { id: "neutral", label: "Neutral / Linear-like", light: ["#FAFAFA", "#FFFFFF", "#E5E5E5", "#171717", "#737373", "#6366F1"], dark: ["#0D0D0D", "#171717", "#2A2A2A", "#F5F5F5", "#A3A3A3", "#8B8CF8"] },
  { id: "slate", label: "Slate / Developer tool", light: ["#F8FAFC", "#FFFFFF", "#CBD5E1", "#0F172A", "#64748B", "#4F46E5"], dark: ["#020617", "#0F172A", "#334155", "#F1F5F9", "#94A3B8", "#818CF8"] },
  { id: "midnight", label: "Midnight / Daylight blue", light: ["#F7F9FC", "#FFFFFF", "#D8E0EC", "#162033", "#66758A", "#2563EB"], dark: ["#080D18", "#101827", "#243047", "#E8EEF8", "#8C9AAF", "#60A5FA"] },
  { id: "purple", label: "Purple / AI-ish", light: ["#FBF9FD", "#FFFFFF", "#E5DDED", "#211827", "#75697D", "#9333EA"], dark: ["#0E0B14", "#181320", "#332A40", "#F3EEFA", "#A59AAF", "#C084FC"] },
  { id: "teal", label: "Teal / Technical", light: ["#F5FAF9", "#FFFFFF", "#CCE1DD", "#102523", "#607D79", "#0D9488"], dark: ["#071111", "#0D1B1B", "#244141", "#ECF8F6", "#8BA8A4", "#2DD4BF"] },
  { id: "warm", label: "Warm charcoal / Warm ivory", light: ["#FAF9F7", "#FFFFFF", "#E5E0DC", "#292421", "#7C726C", "#D97706"], dark: ["#12100F", "#1C1917", "#393330", "#FAF7F4", "#AAA29C", "#F59E0B"] },
  { id: "graphite", label: "Graphite + electric blue", light: ["#F7F8FA", "#FFFFFF", "#D9DCE2", "#1B1E24", "#69717D", "#2563EB"], dark: ["#101114", "#181A1F", "#30343C", "#F1F3F5", "#9299A3", "#4D9CFF"] },
  { id: "oled", label: "OLED / Crisp minimal", light: ["#F8F9F9", "#FFFFFF", "#DDE2E1", "#141716", "#69726F", "#0F9F8F"], dark: ["#050505", "#101010", "#292929", "#EEEEEE", "#888888", "#5EEAD4"] },
] as const;

export type WorkbenchPaletteId = typeof WORKBENCH_PALETTES[number]["id"];
export const PALETTE_ROLES = ["background", "surface", "border", "text", "muted", "accent"] as const;

export function workbenchPalette(id: unknown) {
  return WORKBENCH_PALETTES.find((palette) => palette.id === id) ?? WORKBENCH_PALETTES[5];
}

export function applyWorkbenchPalette(id: unknown, dark: boolean) {
  if (typeof document === "undefined") return;
  const palette = workbenchPalette(id);
  document.documentElement.dataset.workbenchPalette = palette.id;
  const colors = dark ? palette.dark : palette.light;
  PALETTE_ROLES.forEach((role, index) => document.documentElement.style.setProperty(`--dan-wb-${role}`, colors[index]));
}
