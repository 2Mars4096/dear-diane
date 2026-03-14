/**
 * Layout persistence: panel sizes and collapse state per mode (and optionally per workspace).
 * Stored in localStorage under dan-panel-layouts.
 */
import { useState, useCallback } from "react";

const LAYOUT_KEY = "dan-panel-layouts";

export interface LayoutState {
  panelSizes: Record<string, number>;
  collapsedPanels: string[];
}

function getStorageKey(modeId: string, workspaceId?: string): string {
  return workspaceId ? `${modeId}:${workspaceId}` : modeId;
}

export function saveLayoutState(
  modeId: string,
  state: Partial<LayoutState>,
  workspaceId?: string,
) {
  try {
    const all = JSON.parse(localStorage.getItem(LAYOUT_KEY) ?? "{}");
    const key = getStorageKey(modeId, workspaceId);
    all[key] = { ...(all[key] ?? {}), ...state };
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(all));
  } catch {
    // Ignore localStorage errors (quota, private mode)
  }
}

export function loadLayoutState(
  modeId: string,
  workspaceId?: string,
): LayoutState | null {
  try {
    const all = JSON.parse(localStorage.getItem(LAYOUT_KEY) ?? "{}");
    const key = getStorageKey(modeId, workspaceId);
    return all[key] ?? null;
  } catch {
    return null;
  }
}

export function usePanelLayoutPersistence(
  modeId: string,
  workspaceId?: string,
) {
  const [state, setState] = useState<LayoutState>(
    () =>
      loadLayoutState(modeId, workspaceId) ?? {
        panelSizes: {},
        collapsedPanels: [],
      },
  );

  const updateSize = useCallback(
    (panelId: string, size: number) => {
      setState((prev) => {
        const next = {
          ...prev,
          panelSizes: { ...prev.panelSizes, [panelId]: size },
        };
        saveLayoutState(modeId, next, workspaceId);
        return next;
      });
    },
    [modeId, workspaceId],
  );

  const toggleCollapse = useCallback(
    (panelId: string) => {
      setState((prev) => {
        const collapsed = prev.collapsedPanels.includes(panelId)
          ? prev.collapsedPanels.filter((id) => id !== panelId)
          : [...prev.collapsedPanels, panelId];
        const next = { ...prev, collapsedPanels: collapsed };
        saveLayoutState(modeId, next, workspaceId);
        return next;
      });
    },
    [modeId, workspaceId],
  );

  return { state, updateSize, toggleCollapse };
}
