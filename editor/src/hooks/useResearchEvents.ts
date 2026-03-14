import { useEffect } from "react";
import {
  initResearchEventRouting,
  dispatchEngineEvent,
  type EngineEvent,
} from "../lib/researchEventRouter";

/**
 * Initializes research event routing on mount and tears it down on unmount.
 * Also bridges raw engine events from other components (e.g. chat panel)
 * into the routing system.
 */
export function useResearchEvents() {
  useEffect(() => {
    const teardown = initResearchEventRouting();

    const handleRawEvent = (e: Event) => {
      const detail = (e as CustomEvent).detail as EngineEvent | undefined;
      if (detail?.type && detail?.payload) {
        dispatchEngineEvent(detail);
      }
    };

    window.addEventListener("dan:engine-event-raw", handleRawEvent);

    return () => {
      window.removeEventListener("dan:engine-event-raw", handleRawEvent);
      teardown();
    };
  }, []);
}
