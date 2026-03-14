/**
 * Responsive layout hook: viewport width breakpoints for panel stacking and sidebar visibility.
 * Debounced for efficient resize handling.
 */
import { useState, useEffect, useRef } from "react";

const DEBOUNCE_MS = 150;

export function useResponsiveLayout() {
  const [width, setWidth] = useState(
    typeof window !== "undefined" ? window.innerWidth : 1200,
  );
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const handler = () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
      timeoutRef.current = setTimeout(() => {
        setWidth(window.innerWidth);
        timeoutRef.current = null;
      }, DEBOUNCE_MS);
    };
    window.addEventListener("resize", handler);
    return () => {
      window.removeEventListener("resize", handler);
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  return {
    isNarrow: width < 768,
    isMedium: width >= 768 && width < 1200,
    isWide: width >= 1200,
    shouldHideSidebar: width < 768,
    shouldStackPanels: width < 768,
  };
}
