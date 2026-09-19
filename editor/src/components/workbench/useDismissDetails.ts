import { useEffect, useRef } from "react";

/** Dismiss on outside pointer interaction or keyboard focus leaving the panel. */
export function useDismissDetails() {
  const details = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const dismiss = (event: Event) => {
      const element = details.current;
      if (element?.open && event.target instanceof Node && !element.contains(event.target)) {
        element.open = false;
      }
    };
    document.addEventListener("pointerdown", dismiss, true);
    document.addEventListener("focusin", dismiss, true);
    return () => {
      document.removeEventListener("pointerdown", dismiss, true);
      document.removeEventListener("focusin", dismiss, true);
    };
  }, []);
  return details;
}
