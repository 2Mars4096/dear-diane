import { useState, useEffect, useCallback } from "react";
import { useWorkspaceStore } from "../store/useWorkspaceStore";

interface WorkspaceMemory {
  frequentFiles: Array<{ path: string; count: number; lastAccess: number }>;
  frequentCommands: Array<{ command: string; count: number }>;
  recentSearches: string[];
  projectConventions: string[];
}

const EMPTY_MEMORY: WorkspaceMemory = {
  frequentFiles: [],
  frequentCommands: [],
  recentSearches: [],
  projectConventions: [],
};

const MAX_FILES = 50;
const MAX_COMMANDS = 30;
const MAX_SEARCHES = 20;

export function useWorkspaceMemory() {
  const [memory, setMemory] = useState<WorkspaceMemory>(EMPTY_MEMORY);
  const workspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const storageKey = `dan-workspace-memory-${workspaceId ?? "default"}`;

  useEffect(() => {
    try {
      const saved = localStorage.getItem(storageKey);
      if (saved) setMemory(JSON.parse(saved));
      else setMemory(EMPTY_MEMORY);
    } catch {
      setMemory(EMPTY_MEMORY);
    }
  }, [storageKey]);

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(memory));
    } catch {
      // localStorage full — silently drop
    }
  }, [memory, storageKey]);

  const trackFileAccess = useCallback((filePath: string) => {
    setMemory((prev) => {
      const files = [...prev.frequentFiles];
      const existing = files.find((f) => f.path === filePath);
      if (existing) {
        existing.count++;
        existing.lastAccess = Date.now();
      } else {
        files.push({ path: filePath, count: 1, lastAccess: Date.now() });
      }
      files.sort((a, b) => b.count - a.count);
      return { ...prev, frequentFiles: files.slice(0, MAX_FILES) };
    });
  }, []);

  const trackCommand = useCallback((command: string) => {
    setMemory((prev) => {
      const commands = [...prev.frequentCommands];
      const existing = commands.find((c) => c.command === command);
      if (existing) existing.count++;
      else commands.push({ command, count: 1 });
      commands.sort((a, b) => b.count - a.count);
      return { ...prev, frequentCommands: commands.slice(0, MAX_COMMANDS) };
    });
  }, []);

  const trackSearch = useCallback((query: string) => {
    setMemory((prev) => ({
      ...prev,
      recentSearches: [
        query,
        ...prev.recentSearches.filter((s) => s !== query),
      ].slice(0, MAX_SEARCHES),
    }));
  }, []);

  return { memory, trackFileAccess, trackCommand, trackSearch };
}
