import { useResearchStore } from "../store/useResearchStore";

export interface EngineEvent {
  type: string;
  payload: any;
  timestamp: number;
}

type EventHandler = (event: EngineEvent) => void;
const handlers: Record<string, EventHandler[]> = {};

export function registerEventHandler(
  eventType: string,
  handler: EventHandler,
): () => void {
  if (!handlers[eventType]) handlers[eventType] = [];
  handlers[eventType].push(handler);
  return () => {
    handlers[eventType] = handlers[eventType].filter((h) => h !== handler);
  };
}

export function dispatchEngineEvent(event: EngineEvent) {
  const eventHandlers = handlers[event.type] ?? [];
  for (const handler of eventHandlers) {
    try {
      handler(event);
    } catch (err) {
      console.error(`Event handler error for ${event.type}:`, err);
    }
  }

  window.dispatchEvent(
    new CustomEvent("dan:engine-event", { detail: event }),
  );
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const FURNACE_EVENT_TYPES = [
  "session_started",
  "session_completed",
  "session_failed",
  "session_paused",
  "session_resumed",
  "session_cancelled",
  "session_warning",
  "phase_started",
  "phase_completed",
  "phase_failed",
  "phase_skipped",
  "phase_progress",
  "source_status",
  "source_chunk",
  "budget_exceeded",
];

export function handleFurnaceSSEEvent(event: Record<string, unknown>): void {
  const type = event.type as string;
  if (!FURNACE_EVENT_TYPES.includes(type)) return;

  const store = useResearchStore.getState();
  const sessionId = event.session_id as string | undefined;
  if (sessionId) {
    const timestamp =
      typeof event.timestamp === "number"
        ? event.timestamp * 1000
        : Date.now();
    const phase =
      typeof event.phase === "string"
        ? event.phase
        : undefined;
    const sourceStatus =
      typeof event.status === "string"
        ? event.status
        : undefined;
    const sourceId =
      typeof event.source_id === "string"
        ? event.source_id
        : undefined;
    const statusMessage = (() => {
      if (type === "session_started") return "Session started";
      if (type === "session_resumed") return "Session resumed";
      if (type === "session_paused") return "Session paused";
      if (type === "session_cancelled") return "Session cancelled";
      if (type === "session_completed") return "Session completed";
      if (type === "session_failed") return `Session failed${event.error ? `: ${String(event.error)}` : ""}`;
      if (type === "phase_started" && phase) return `Running ${phase} phase`;
      if (type === "phase_completed" && phase) return `${phase} phase completed`;
      if (type === "phase_failed" && phase) return `${phase} phase failed`;
      if (type === "phase_skipped" && phase) return `${phase} phase skipped (cached)`;
      if (type === "phase_progress" && phase) {
        const chars = typeof event.chars === "number" ? event.chars : 0;
        const elapsed = typeof event.elapsed === "number" ? event.elapsed : 0;
        const done = event.done === true;
        if (done) return `${phase} phase: generation complete (${chars.toLocaleString()} chars, ${elapsed}s)`;
        return `${phase} phase: generating… ${chars.toLocaleString()} chars (${elapsed}s)`;
      }
      if (type === "source_chunk" && sourceId) {
        const startPg = typeof event.start_page === "number" ? event.start_page : 0;
        const endPg = typeof event.end_page === "number" ? event.end_page : 0;
        const totalPg = typeof event.total_pages === "number" ? event.total_pages : 0;
        return `Reading ${sourceId}: pages ${startPg}–${endPg}${totalPg ? ` of ${totalPg}` : ""}`;
      }
      if (type === "source_status" && sourceId && sourceStatus) {
        const idx = typeof event.source_index === "number" ? event.source_index : 0;
        const total = typeof event.total_sources === "number" ? event.total_sources : 0;
        const counter = idx && total ? ` (${idx}/${total})` : "";
        return `${sourceId}: ${sourceStatus}${counter}`;
      }
      if (type === "budget_exceeded") return "Budget exceeded - session paused";
      return undefined;
    })();
    const recentEvent = statusMessage;

    if (
      type === "session_started" ||
      type === "session_completed" ||
      type === "session_failed" ||
      type === "session_paused" ||
      type === "session_resumed" ||
      type === "session_cancelled"
    ) {
      const status =
        type === "session_started"
          ? "active"
          : type === "session_completed"
            ? "completed"
            : type === "session_failed" || type === "session_cancelled"
              ? "failed"
              : type === "session_paused"
                ? "paused"
                : "active";
      store.updateTrainingSessionFromBackend(sessionId, {
        session_id: sessionId,
        status,
        current_phase: phase,
        status_message: statusMessage,
        recent_events: recentEvent
          ? [
              recentEvent,
              ...(
                store.trainingSessions.find((t) => t.sessionId === sessionId || t.id === sessionId)?.recentEvents ??
                []
              ),
            ].slice(0, 6)
          : undefined,
        last_activity_at: timestamp,
      });
    }
    if (
      type === "phase_started" ||
      type === "phase_completed" ||
      type === "phase_failed" ||
      type === "phase_skipped" ||
      type === "source_status"
    ) {
      store.updateTrainingSessionFromBackend(sessionId, {
        current_phase: phase,
        status_message: statusMessage,
        recent_events: recentEvent
          ? [
              recentEvent,
              ...(
                store.trainingSessions.find((t) => t.sessionId === sessionId || t.id === sessionId)?.recentEvents ??
                []
              ),
            ].slice(0, 6)
          : undefined,
        last_activity_at: timestamp,
      });
    }
    if (type === "phase_progress" || type === "source_chunk") {
      const update: Record<string, unknown> = {
        status_message: statusMessage,
        last_activity_at: timestamp,
      };
      if (phase) update.current_phase = phase;
      store.updateTrainingSessionFromBackend(sessionId, update);
    }
    if (type === "budget_exceeded") {
      store.updateTrainingSessionFromBackend(sessionId, {
        status: "paused",
        status_message: statusMessage,
        recent_events: recentEvent
          ? [
              recentEvent,
              ...(
                store.trainingSessions.find((t) => t.sessionId === sessionId || t.id === sessionId)?.recentEvents ??
                []
              ),
            ].slice(0, 6)
          : undefined,
        total_cost_usd:
          typeof event.total_cost === "number" ? event.total_cost : undefined,
        last_activity_at: timestamp,
      });
    }
  }
  window.dispatchEvent(
    new CustomEvent("research:distillation-update", { detail: event }),
  );
  if (type === "session_completed" && event.artifact_dir) {
    window.dispatchEvent(
      new CustomEvent("research:distillation-output", {
        detail: event,
      }),
    );
  }
}

let initialized = false;

/**
 * Register all research-mode event handlers once.
 * Returns a teardown function that removes every handler registered here.
 */
export function initResearchEventRouting(): () => void {
  if (initialized) return () => {};
  initialized = true;

  const store = useResearchStore.getState;
  const unsubscribers: (() => void)[] = [];

  // artifact_created → document_section → writing pane
  unsubscribers.push(
    registerEventHandler("artifact_created", (event) => {
      const { artifactType, content, sectionTitle } = event.payload;
      if (artifactType !== "document_section") return;

      const current = store().documentContent;
      const sectionHeader = `## ${sectionTitle}\n\n`;
      const sectionRegex = new RegExp(
        `^## ${escapeRegex(sectionTitle)}\\n[\\s\\S]*?(?=^## |$)`,
        "m",
      );

      if (sectionRegex.test(current)) {
        store().setDocumentContent(
          current.replace(sectionRegex, sectionHeader + content + "\n\n"),
        );
      } else {
        store().setDocumentContent(
          current + (current ? "\n\n" : "") + sectionHeader + content,
        );
      }
    }),
  );

  // artifact_created → figure → figure gallery (cross-component via CustomEvent)
  unsubscribers.push(
    registerEventHandler("artifact_created", (event) => {
      const { artifactType, title, caption, url, cellId } = event.payload;
      if (artifactType !== "figure") return;
      window.dispatchEvent(
        new CustomEvent("research:new-figure", {
          detail: { title, caption, src: url, cellId },
        }),
      );
    }),
  );

  // artifact_created → reference → reference panel
  unsubscribers.push(
    registerEventHandler("artifact_created", (event) => {
      const { artifactType, ...paperData } = event.payload;
      if (artifactType !== "reference") return;
      store().addPaper({
        title: paperData.title ?? "Unknown",
        authors: paperData.authors ?? [],
        year: paperData.year ?? new Date().getFullYear(),
        abstract: paperData.abstract,
        doi: paperData.doi,
        filePath: paperData.filePath,
        status: "unread",
        tags: paperData.tags ?? [],
      });
    }),
  );

  // review_output → review panel
  unsubscribers.push(
    registerEventHandler("review_output", (event) => {
      window.dispatchEvent(
        new CustomEvent("research:review-comment", {
          detail: event.payload,
        }),
      );
    }),
  );

  // distillation_progress / distillation_output → distillation tab
  unsubscribers.push(
    registerEventHandler("distillation_progress", (event) => {
      window.dispatchEvent(
        new CustomEvent("research:distillation-update", {
          detail: event.payload,
        }),
      );
    }),
  );
  unsubscribers.push(
    registerEventHandler("distillation_output", (event) => {
      window.dispatchEvent(
        new CustomEvent("research:distillation-output", {
          detail: event.payload,
        }),
      );
    }),
  );

  // node_started → mark pipeline stage active
  unsubscribers.push(
    registerEventHandler("node_started", (event) => {
      const { nodeId, stageId } = event.payload;
      const pipeline = store().pipeline;
      const stage = pipeline.find(
        (s) => s.id === stageId || s.id === nodeId,
      );
      if (stage) {
        store().updateStage(stage.id, {
          status: "active",
          startedAt: Date.now(),
        });
      }
    }),
  );

  // node_completed → mark stage completed, auto-advance next stage
  unsubscribers.push(
    registerEventHandler("node_completed", (event) => {
      const { nodeId, stageId } = event.payload;
      const pipeline = store().pipeline;
      const stage = pipeline.find(
        (s) => s.id === stageId || s.id === nodeId,
      );
      if (!stage) return;

      store().updateStage(stage.id, {
        status: "completed",
        completedAt: Date.now(),
      });

      const idx = pipeline.findIndex((s) => s.id === stage.id);
      if (idx >= 0 && idx < pipeline.length - 1) {
        store().updateStage(pipeline[idx + 1].id, {
          status: "active",
          startedAt: Date.now(),
        });
      }
    }),
  );

  // llm_chunk → append to writing pane
  unsubscribers.push(
    registerEventHandler("llm_chunk", (event) => {
      const { target, chunk } = event.payload;
      if (target === "writing_pane" || target === "document") {
        const current = store().documentContent;
        store().setDocumentContent(current + chunk);
      }
    }),
  );

  // tool_output → auto-discover references from search results
  unsubscribers.push(
    registerEventHandler("tool_output", (event) => {
      const { toolName, results } = event.payload;
      if (toolName !== "web_search" && toolName !== "semantic_scholar") return;
      for (const result of results ?? []) {
        if (result.title && result.authors) {
          store().addPaper({
            title: result.title,
            authors: result.authors,
            year: result.year ?? new Date().getFullYear(),
            abstract: result.abstract,
            doi: result.doi,
            status: "unread",
            tags: ["auto-discovered"],
          });
        }
      }
    }),
  );

  // node_error → mark stage failed + send notification
  unsubscribers.push(
    registerEventHandler("node_error", (event) => {
      const { nodeId, stageId, error } = event.payload;
      const pipeline = store().pipeline;
      const stage = pipeline.find(
        (s) => s.id === stageId || s.id === nodeId,
      );
      if (stage) {
        store().updateStage(stage.id, { status: "failed", details: error });
      }
      window.dispatchEvent(
        new CustomEvent("dan:notification", {
          detail: { type: "error", title: "Pipeline Error", message: error },
        }),
      );
    }),
  );

  return () => {
    for (const unsub of unsubscribers) unsub();
    initialized = false;
  };
}
