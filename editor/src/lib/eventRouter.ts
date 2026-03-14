import { wsConnection } from "./wsConnection";
import { useAppStore } from "../store/useAppStore";

type ModeHandler = (event: any) => void;

interface EventRoute {
  eventType: string;
  modes: Record<string, ModeHandler>;
}

class EventRouter {
  private routes: EventRoute[] = [];
  private activeMode: string = "chat";
  private unsubscribers: Array<() => void> = [];

  registerRoute(eventType: string, mode: string, handler: ModeHandler) {
    let route = this.routes.find((r) => r.eventType === eventType);
    if (!route) {
      route = { eventType, modes: {} };
      this.routes.push(route);
    }
    route.modes[mode] = handler;
  }

  setActiveMode(mode: string) {
    this.activeMode = mode;
  }

  start() {
    const unsub = wsConnection.on("*", (event) => {
      const eventType = event.type ?? event.event;
      if (!eventType) return;

      const route = this.routes.find((r) => r.eventType === eventType);
      if (!route) return;

      const handler = route.modes[this.activeMode];
      if (handler) handler(event);

      const globalHandler = route.modes["*"];
      if (globalHandler) globalHandler(event);
    });

    this.unsubscribers.push(unsub);
  }

  stop() {
    this.unsubscribers.forEach((u) => u());
    this.unsubscribers = [];
  }
}

export const eventRouter = new EventRouter();

// ---------------------------------------------------------------------------
// Global events (all modes)
// ---------------------------------------------------------------------------

eventRouter.registerRoute("notification", "*", (e) => {
  useAppStore.getState().addNotification({
    type: e.severity ?? "info",
    title: e.title ?? "Notification",
    message: e.message ?? "",
  });
});

eventRouter.registerRoute("run_completed", "*", (e) => {
  useAppStore.getState().addNotification({
    type: "info",
    title: "Run Completed",
    message: e.name ?? "A workflow run has completed",
  });
});

// ---------------------------------------------------------------------------
// Chat mode events
// ---------------------------------------------------------------------------

eventRouter.registerRoute("llm_chunk", "chat", (e) => {
  window.dispatchEvent(new CustomEvent("dan:chat-chunk", { detail: e }));
});

eventRouter.registerRoute("tool_output", "chat", (e) => {
  window.dispatchEvent(new CustomEvent("dan:tool-output", { detail: e }));
});

// ---------------------------------------------------------------------------
// Research mode events (forward to existing research event router)
// ---------------------------------------------------------------------------

eventRouter.registerRoute("artifact_created", "research", (e) => {
  window.dispatchEvent(
    new CustomEvent("dan:engine-event-raw", { detail: e }),
  );
});

eventRouter.registerRoute("node_started", "research", (e) => {
  window.dispatchEvent(
    new CustomEvent("dan:engine-event-raw", { detail: e }),
  );
});

eventRouter.registerRoute("node_completed", "research", (e) => {
  window.dispatchEvent(
    new CustomEvent("dan:engine-event-raw", { detail: e }),
  );
});

// ---------------------------------------------------------------------------
// Code mode events
// ---------------------------------------------------------------------------

eventRouter.registerRoute("file_changed", "development", (e) => {
  window.dispatchEvent(new CustomEvent("dan:file-changed", { detail: e }));
});

eventRouter.registerRoute("diagnostics", "development", (e) => {
  window.dispatchEvent(new CustomEvent("dan:diagnostics", { detail: e }));
});
