import { create } from "zustand";

interface ContentState {
  activeProjectRoot: string | null;
  setActiveProjectRoot: (root: string | null) => void;

  activePagePath: string | null;
  setActivePagePath: (path: string | null) => void;

  draftsByPath: Record<string, string>;
  setDraftForPath: (path: string, content: string) => void;
  clearDraftForPath: (path: string) => void;

  clearWorkspaceState: () => void;
}

export const useContentStore = create<ContentState>((set) => ({
  activeProjectRoot: null,
  setActiveProjectRoot: (root) =>
    set((state) => ({
      activeProjectRoot: root,
      activePagePath:
        root && state.activePagePath?.startsWith(root) ? state.activePagePath : null,
    })),

  activePagePath: null,
  setActivePagePath: (path) => set({ activePagePath: path }),

  draftsByPath: {},
  setDraftForPath: (path, content) =>
    set((state) => ({
      draftsByPath: {
        ...state.draftsByPath,
        [path]: content,
      },
    })),
  clearDraftForPath: (path) =>
    set((state) => {
      const next = { ...state.draftsByPath };
      delete next[path];
      return { draftsByPath: next };
    }),

  clearWorkspaceState: () =>
    set({
      activeProjectRoot: null,
      activePagePath: null,
      draftsByPath: {},
    }),
}));
