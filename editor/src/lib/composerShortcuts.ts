/** Keyboard submission is independent of the button's selected placement. */
export function composerEnterPlacement(event: {
  key: string; shiftKey: boolean; altKey: boolean; metaKey: boolean; ctrlKey: boolean;
  isComposing?: boolean; keyCode?: number;
}, hasActiveRun: boolean): "steer" | "queue" | null {
  if (event.key !== "Enter" || event.shiftKey || event.altKey || event.isComposing || event.keyCode === 229) return null;
  return hasActiveRun && (event.metaKey || event.ctrlKey) ? "queue" : "steer";
}
