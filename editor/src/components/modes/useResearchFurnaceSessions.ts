import { useCallback, useEffect, useRef } from "react";
import {
  furnaceConnectSSE,
  furnaceListSessions,
  type FurnaceSessionSummary,
} from "../../lib/api";
import { handleFurnaceSSEEvent } from "../../lib/researchEventRouter";
import {
  useResearchStore,
  type TrainingSession,
} from "../../store/useResearchStore";

const SESSION_SYNC_INTERVAL_MS = 15_000;
const SSE_RECONNECT_DELAY_MS = 2_000;

function normalizeSummaryStatus(
  status: string,
): TrainingSession["status"] {
  return status === "active" ? "running" : (status as TrainingSession["status"]);
}

function findMatchingSession(
  sessions: TrainingSession[],
  summary: FurnaceSessionSummary,
): TrainingSession | undefined {
  const normalizedName = (summary.name || "").trim().toLowerCase();
  const normalizedTopic = (summary.topic || "").trim().toLowerCase();

  return sessions.find((session) => {
    if (session.sessionId === summary.session_id) return true;
    if (session.sessionId) return false;
    const nameMatch = (session.name || "").trim().toLowerCase() === normalizedName;
    const topicMatch = (session.topic || "").trim().toLowerCase() === normalizedTopic;
    return nameMatch && topicMatch;
  });
}

function getReconnectStatusMessage(status: TrainingSession["status"]): string {
  return status === "paused"
    ? "Paused. Reconnecting live session updates..."
    : "Reconnecting live session updates...";
}

export interface ResearchFurnaceSessionsController {
  connectSessionSSE: (sessionId: string) => void;
  resolveSessionId: (
    seedId: string | undefined,
    name: string,
    topicValue: string,
  ) => Promise<string | undefined>;
}

export interface UseResearchFurnaceSessionsOptions {
  enabled?: boolean;
}

export function useResearchFurnaceSessions(
  options: UseResearchFurnaceSessionsOptions = {},
): ResearchFurnaceSessionsController {
  const enabled = options.enabled ?? true;
  const trainingSessions = useResearchStore((state) => state.trainingSessions);
  const addTrainingSession = useResearchStore((state) => state.addTrainingSession);
  const updateTrainingSession = useResearchStore((state) => state.updateTrainingSession);
  const sseRefs = useRef<Map<string, EventSource>>(new Map());
  const reconnectTimersRef = useRef<Map<string, number>>(new Map());

  const clearReconnectTimer = useCallback((sessionId: string) => {
    const timerId = reconnectTimersRef.current.get(sessionId);
    if (timerId !== undefined) {
      window.clearTimeout(timerId);
      reconnectTimersRef.current.delete(sessionId);
    }
  }, []);

  const closeAllConnections = useCallback(() => {
    for (const [, eventSource] of sseRefs.current) {
      eventSource.close();
    }
    sseRefs.current.clear();
    for (const [, timerId] of reconnectTimersRef.current) {
      window.clearTimeout(timerId);
    }
    reconnectTimersRef.current.clear();
  }, []);

  const upsertSessionSummary = useCallback(
    (summary: FurnaceSessionSummary) => {
      const existing = findMatchingSession(
        useResearchStore.getState().trainingSessions,
        summary,
      );

      if (!existing) {
        addTrainingSession({
          sessionId: summary.session_id,
          recipeId: summary.recipe_id || "",
          parentSessionId: summary.parent_session_id || undefined,
          familySessionId: summary.family_session_id || summary.session_id,
          variantLabel: summary.variant_label || undefined,
          tags: summary.tags ?? [],
          name: summary.name || summary.topic || "Untitled Session",
          topic: summary.topic || "",
          status: normalizeSummaryStatus(summary.status),
          targetPapers: Math.max(0, summary.source_count || 0),
          processedPapers: Math.max(0, summary.processed_count || 0),
          currentPhase: summary.current_phase,
          sourceCount: summary.source_count,
          totalCostUsd: summary.total_cost_usd,
          lastActivityAt: summary.updated_at ? summary.updated_at * 1000 : Date.now(),
          statusMessage: summary.current_phase
            ? `Current phase: ${summary.current_phase}`
            : undefined,
        });
        return;
      }

      updateTrainingSession(existing.id, {
        sessionId: summary.session_id,
        recipeId: summary.recipe_id || existing.recipeId,
        parentSessionId: summary.parent_session_id || existing.parentSessionId,
        familySessionId: summary.family_session_id || existing.familySessionId,
        variantLabel: summary.variant_label || existing.variantLabel,
        tags: summary.tags ?? existing.tags,
        name: summary.name || existing.name,
        topic: summary.topic || existing.topic,
        status: normalizeSummaryStatus(summary.status),
        targetPapers: summary.source_count || existing.targetPapers,
        processedPapers: summary.processed_count ?? existing.processedPapers,
        currentPhase: summary.current_phase || existing.currentPhase,
        sourceCount: summary.source_count ?? existing.sourceCount,
        totalCostUsd: summary.total_cost_usd ?? existing.totalCostUsd,
        lastActivityAt: summary.updated_at ? summary.updated_at * 1000 : Date.now(),
      });
    },
    [addTrainingSession, updateTrainingSession],
  );

  const connectSessionSSE = useCallback((sessionId: string) => {
    if (!enabled) return;
    if (sseRefs.current.has(sessionId)) return;

    clearReconnectTimer(sessionId);
    const eventSource = furnaceConnectSSE(
      sessionId,
      (event) => handleFurnaceSSEEvent(event as Record<string, unknown>),
      () => {
        sseRefs.current.delete(sessionId);
        const matchingSession = useResearchStore
          .getState()
          .trainingSessions.find(
            (session) =>
              session.sessionId === sessionId &&
              (session.status === "running" || session.status === "paused"),
          );

        if (!matchingSession) {
          clearReconnectTimer(sessionId);
          return;
        }

        useResearchStore.getState().updateTrainingSession(matchingSession.id, {
          statusMessage: getReconnectStatusMessage(matchingSession.status),
          lastActivityAt: Date.now(),
        });

        if (reconnectTimersRef.current.has(sessionId)) return;
        const timerId = window.setTimeout(() => {
          reconnectTimersRef.current.delete(sessionId);
          const stillActive = useResearchStore
            .getState()
            .trainingSessions.some(
              (session) =>
                session.sessionId === sessionId &&
                (session.status === "running" || session.status === "paused"),
            );
          if (!stillActive || sseRefs.current.has(sessionId)) return;
          connectSessionSSE(sessionId);
        }, SSE_RECONNECT_DELAY_MS);
        reconnectTimersRef.current.set(sessionId, timerId);
      },
    );

    sseRefs.current.set(sessionId, eventSource);
  }, [clearReconnectTimer, enabled]);

  useEffect(() => {
    if (!enabled) {
      closeAllConnections();
      return;
    }

    let cancelled = false;

    const syncSessions = async () => {
      try {
        const data = await furnaceListSessions();
        if (cancelled) return;
        for (const session of data.sessions) {
          upsertSessionSummary(session);
        }
      } catch {
        // Ignore transient refresh errors and keep the local session desk usable.
      }
    };

    void syncSessions();
    const intervalId = window.setInterval(syncSessions, SESSION_SYNC_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [closeAllConnections, enabled, upsertSessionSummary]);

  useEffect(() => {
    if (!enabled) {
      closeAllConnections();
      return;
    }

    const activeSessionIds = new Set(
      trainingSessions
        .filter(
          (session) =>
            (session.status === "running" || session.status === "paused") &&
            session.sessionId,
        )
        .map((session) => session.sessionId as string),
    );

    for (const [sessionId, eventSource] of sseRefs.current) {
      if (activeSessionIds.has(sessionId)) continue;
      eventSource.close();
      sseRefs.current.delete(sessionId);
      clearReconnectTimer(sessionId);
    }

    for (const sessionId of activeSessionIds) {
      if (
        sseRefs.current.has(sessionId) ||
        reconnectTimersRef.current.has(sessionId)
      ) {
        continue;
      }
      connectSessionSSE(sessionId);
    }
  }, [clearReconnectTimer, closeAllConnections, connectSessionSSE, enabled, trainingSessions]);

  useEffect(() => {
    return () => {
      closeAllConnections();
    };
  }, [closeAllConnections]);

  const resolveSessionId = useCallback(
    async (seedId: string | undefined, name: string, topicValue: string) => {
      if (seedId) return seedId;
      const listed = await furnaceListSessions();
      const normalizedName = (name || "").trim().toLowerCase();
      const normalizedTopic = (topicValue || "").trim().toLowerCase();
      const match = listed.sessions.find((session) => {
        if (session.name.trim().toLowerCase() === normalizedName) return true;
        return session.topic.trim().toLowerCase() === normalizedTopic;
      });
      return match?.session_id;
    },
    [],
  );

  return { connectSessionSSE, resolveSessionId };
}
