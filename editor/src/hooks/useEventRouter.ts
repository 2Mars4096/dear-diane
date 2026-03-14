import { useEffect } from "react";
import { useAppStore } from "../store/useAppStore";
import { wsConnection } from "../lib/wsConnection";
import { eventRouter } from "../lib/eventRouter";

/**
 * Connects the shared WebSocket, starts the event router, and keeps
 * the router's active mode in sync with the app store.
 * Mount once in AppShell.
 */
export function useEventRouter() {
  const activeMode = useAppStore((s) => s.activeMode);

  useEffect(() => {
    const serverUrl =
      window.location.origin.replace(/^http/, "ws") + "/ws";
    wsConnection.connect(serverUrl);
    eventRouter.start();

    return () => {
      eventRouter.stop();
      wsConnection.disconnect();
    };
  }, []);

  useEffect(() => {
    eventRouter.setActiveMode(activeMode);
  }, [activeMode]);
}
