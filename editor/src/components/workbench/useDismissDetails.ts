import { useEffect, useRef } from "react";

const CLOSE_DELAY = 180;

/**
 * Opens on mouse hover (click and keyboard still work) and dismisses on
 * outside pointer interaction, keyboard focus leaving, or the mouse leaving.
 */
export function useDismissDetails() {
  const details = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const element = details.current;
    let timer = 0;
    let hoverOpenedAt = 0;
    const dismiss = (event: Event) => {
      if (element?.open && event.target instanceof Node && !element.contains(event.target)) {
        element.open = false;
      }
    };
    const enter = (event: PointerEvent) => {
      if (!element || event.pointerType !== "mouse") return;
      window.clearTimeout(timer);
      if (!element.open) {
        element.open = true;
        hoverOpenedAt = Date.now();
      }
    };
    const leave = (event: PointerEvent) => {
      if (!element || event.pointerType !== "mouse") return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        // A native select menu or a focused field inside keeps the panel open.
        const active = document.activeElement;
        if (active && active !== element.querySelector("summary") && element.contains(active)) return;
        element.open = false;
      }, CLOSE_DELAY);
    };
    // Clicking a summary that hover just opened should keep it open, not toggle it shut.
    const click = (event: MouseEvent) => {
      if (element?.open && event.target instanceof Element && event.target.closest("summary")?.parentElement === element && Date.now() - hoverOpenedAt < 1500) {
        event.preventDefault();
        hoverOpenedAt = 0;
      }
    };
    document.addEventListener("pointerdown", dismiss, true);
    document.addEventListener("focusin", dismiss, true);
    element?.addEventListener("pointerenter", enter);
    element?.addEventListener("pointerleave", leave);
    element?.addEventListener("click", click);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("pointerdown", dismiss, true);
      document.removeEventListener("focusin", dismiss, true);
      element?.removeEventListener("pointerenter", enter);
      element?.removeEventListener("pointerleave", leave);
      element?.removeEventListener("click", click);
    };
  }, []);
  return details;
}
