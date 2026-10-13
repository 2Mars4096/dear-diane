export const WORKBENCH_FONTS = [
  { id: 'system', label: 'System', family: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif', description: 'Familiar and compact' },
  { id: 'humanist', label: 'Humanist', family: '"Avenir Next", Avenir, "Trebuchet MS", sans-serif', description: 'Open and rounded' },
  { id: 'bookish', label: 'Bookish', family: 'Charter, "Bitstream Charter", Georgia, serif', description: 'Serif lettering for long reading' },
  { id: 'mono', label: 'Monospace', family: 'ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace', description: 'Evenly spaced, terminal style' },
  { id: 'inter', label: 'Inter · Clean sans', family: '"Diane Inter", sans-serif', description: 'Clean workspace lettering' },
  { id: 'lora', label: 'Lora · Editorial serif', family: '"Diane Lora", Georgia, serif', description: 'Editorial reading' },
  { id: 'space', label: 'Space Grotesk · Modern', family: '"Diane Space Grotesk", sans-serif', description: 'Modern geometric sans' },
  { id: 'orbitron', label: 'Orbitron · Futuristic', family: '"Diane Orbitron", sans-serif', description: 'Geometric display lettering' },
  { id: 'monoton', label: 'Monoton · Neon tubing', family: '"Diane Monoton", monospace', description: 'Striped neon-style display lettering' },
  { id: 'tilt-neon', label: 'Tilt Neon · Neon sign', family: '"Diane Tilt Neon", sans-serif', description: 'Rounded neon-sign lettering' },
] as const;
export type WorkbenchFontId = typeof WORKBENCH_FONTS[number]['id'];
export function workbenchFont(id: unknown) { return WORKBENCH_FONTS.find(font => font.id === id) ?? WORKBENCH_FONTS[0]; }
export type WorkbenchTypography = { textFont?: WorkbenchFontId; headingFont?: WorkbenchFontId; highlightFont?: WorkbenchFontId };
export function applyWorkbenchFonts(settings: WorkbenchTypography = {}) {
  if (typeof document === 'undefined') return;
  for (const [role, id] of Object.entries({ text: settings.textFont, heading: settings.headingFont, highlight: settings.highlightFont })) {
    document.documentElement.style.setProperty(`--dan-font-${role}`, workbenchFont(id).family);
  }
}
