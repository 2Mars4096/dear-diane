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
