// @vitest-environment happy-dom

import { beforeEach, describe, expect, it } from "vitest";

import { loadSession, saveSession } from "../sessionPersistence";
import { useCodeStore } from "../../store/useCodeStore";
import { useContentStore } from "../../store/useContentStore";
import { useResearchStore } from "../../store/useResearchStore";

describe("sessionPersistence", () => {
  beforeEach(() => {
    localStorage.clear();
    useCodeStore.setState({
      pinnedRoots: ["/repo"],
      openFiles: [],
      activeFilePath: null,
      showTerminal: true,
      showSidebar: true,
      activeSidebarPanel: "explorer",
      recentFiles: [],
      expandedDirs: {},
      currentBranch: "main",
    });
    useResearchStore.setState({
      primaryTab: "editor",
      contextTab: "references",
      secondaryTab: "code",
      activePaperId: null,
      papers: [],
      notes: [],
      documentContent: "",
      showPipeline: false,
      showSecondary: false,
      showContextPanel: false,
      activeRailSection: "library",
    });
    useContentStore.setState({
      activeProjectRoot: null,
      activePagePath: null,
      draftsByPath: {},
    });
  });

  it("hydrates a missing content block when restoring a legacy workspace session", () => {
    localStorage.setItem(
      "dan-session-ws-content",
      JSON.stringify({
        code: {
          pinnedRoots: ["/repo"],
          openFilePaths: [],
          activeFilePath: null,
          showTerminal: true,
          showSidebar: true,
          activeSidebarPanel: "explorer",
          recentFiles: [],
          expandedDirs: {},
          currentBranch: "main",
          savedAt: 1,
        },
        research: {
          primaryTab: "editor",
          contextTab: "references",
          secondaryTab: "code",
          activePaperId: null,
          papers: [],
          notes: [],
          documentContent: "",
          showPipeline: false,
          showSecondary: false,
          showContextPanel: false,
          activeRailSection: "library",
        },
        lastActiveMode: "content",
        savedAt: 2,
      }),
    );

    expect(loadSession("ws-content")).toMatchObject({
      lastActiveMode: "content",
      content: {
        activeProjectRoot: null,
        activePagePath: null,
        draftsByPath: {},
      },
    });
  });

  it("persists content-mode workspace state alongside other session data", () => {
    useContentStore.setState({
      activeProjectRoot: "/repo/kb",
      activePagePath: "/repo/kb/content/posts/editorial-desk/index.md",
      draftsByPath: {
        "/repo/kb/content/posts/editorial-desk/index.md": "draft body",
      },
    });

    saveSession("ws-content", "content");

    expect(loadSession("ws-content")).toMatchObject({
      lastActiveMode: "content",
      content: {
        activeProjectRoot: "/repo/kb",
        activePagePath: "/repo/kb/content/posts/editorial-desk/index.md",
        draftsByPath: {
          "/repo/kb/content/posts/editorial-desk/index.md": "draft body",
        },
      },
    });
  });
});
