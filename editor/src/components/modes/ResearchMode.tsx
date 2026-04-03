import {
  lazy,
  Suspense,
  useCallback,
  useMemo,
  useState,
} from "react";
import { Allotment } from "allotment";
import { useResearchEvents } from "../../hooks/useResearchEvents";
import { useModeScopedWindowEvent } from "../../hooks/useModeScopedWindowEvent";
import {
  Loader2,
  MessageSquareText,
  PanelBottomClose,
  PanelBottomOpen,
  PanelRightClose,
  PanelRightOpen,
  X,
  Terminal as TerminalIcon,
} from "lucide-react";
import { useResearchStore } from "../../store/useResearchStore";
import { useAppStore } from "../../store/useAppStore";
import { useCodeStore } from "../../store/useCodeStore";
import TerminalPanel from "../code/TerminalPanel";
import ModeChatSidebar from "../shared/ModeChatSidebar";
import {
  ResearchModeContextPanel,
  ResearchModeFunctionRail,
  ResearchModePipelineProgress,
  ResearchModePrimaryPanel,
} from "./ResearchModeShell";
import { ResearchStatusStrip } from "./ResearchStatusStrip";
import { buildResearchModeChatContext } from "./researchModeChatContext";
import { useResearchAutoShowFurnace } from "./useResearchAutoShowFurnace";
import { useResearchFurnaceSessions } from "./useResearchFurnaceSessions";
import { useResearchModeShortcuts } from "./useResearchModeShortcuts";

const ResearchFurnacePanel = lazy(() => import("./ResearchFurnacePanel"));

function FurnacePanelLoader() {
  return (
    <div className="flex h-full items-center justify-center bg-gray-50 text-gray-500 dark:bg-[#1e1e1e] dark:text-gray-400">
      <div className="inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white px-3 py-2 text-sm shadow-sm dark:border-gray-800 dark:bg-gray-900/60">
        <Loader2 size={14} className="animate-spin" />
        Loading Furnace desk...
      </div>
    </div>
  );
}

export default function ResearchMode() {
  useResearchEvents();
  useResearchModeShortcuts();
  const { pipelineActive } = useResearchAutoShowFurnace();
  const activeMode = useAppStore((state) => state.activeMode);
  const furnaceSessions = useResearchFurnaceSessions({
    enabled: activeMode === "research",
  });

  const [showChatSidebar, setShowChatSidebar] = useState(false);
  const primaryTab = useResearchStore((state) => state.primaryTab);
  const papers = useResearchStore((state) => state.papers);
  const activePaperId = useResearchStore((state) => state.activePaperId);
  const trainingSessions = useResearchStore((state) => state.trainingSessions);
  const showPipeline = useResearchStore((state) => state.showPipeline);
  const showContextPanel = useResearchStore((state) => state.showContextPanel);
  const togglePipeline = useResearchStore((state) => state.togglePipeline);
  const toggleContextPanel = useResearchStore((state) => state.toggleContextPanel);
  const showTerminal = useCodeStore((state) => state.showTerminal);
  const toggleTerminal = useCodeStore((state) => state.toggleTerminal);
  const toggleChatSidebar = useCallback(() => {
    setShowChatSidebar((value) => !value);
  }, []);
  const closeChatSidebar = useCallback(() => {
    setShowChatSidebar(false);
  }, []);
  const researchModeChatContext = useCallback(
    () => buildResearchModeChatContext(useResearchStore.getState()),
    [],
  );

  const activePaperTitle = useMemo(
    () => papers.find((paper) => paper.id === activePaperId)?.title ?? null,
    [activePaperId, papers],
  );
  const runningTrainingCount = useMemo(
    () =>
      trainingSessions.filter(
        (session) => session.status === "running" || session.status === "paused",
      ).length,
    [trainingSessions],
  );

  useModeScopedWindowEvent("research", "app:toggleModeChatSidebar", toggleChatSidebar);

  return (
    <div className="flex h-full bg-gray-50 text-gray-900 dark:bg-[#1e1e1e] dark:text-gray-200">
      <div className="flex w-56 shrink-0 flex-col border-r border-gray-200 bg-white dark:border-gray-800 dark:bg-[#1e1e1e]">
        <ResearchModeFunctionRail />

        <div className="flex shrink-0 items-center gap-1 border-t border-gray-200 px-2 py-1 dark:border-gray-800">
          {pipelineActive && (
            <button
              onClick={togglePipeline}
              title={showPipeline ? "Hide pipeline" : "Show pipeline"}
              className="flex items-center gap-1 text-[10px] text-gray-500 transition-colors hover:text-gray-900 dark:hover:text-gray-300"
            >
              {showPipeline ? "Hide" : "Show"} Pipeline
            </button>
          )}
          <span className="flex-1" />
          <button
            onClick={toggleChatSidebar}
            title={showChatSidebar ? "Hide AI chat (⌘J)" : "Show AI chat (⌘J)"}
            className={`flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] font-medium transition-colors ${
              showChatSidebar
                ? "border-blue-500/30 bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-300"
                : "border-transparent text-blue-500 hover:bg-blue-50 hover:text-blue-600 dark:text-blue-400 dark:hover:bg-white/5 dark:hover:text-blue-300"
            }`}
          >
            <MessageSquareText size={11} />
            Chat
          </button>
          <button
            onClick={toggleContextPanel}
            title={showContextPanel ? "Hide context drawer (⌘I)" : "Show context drawer (⌘I)"}
            className={`flex items-center gap-1 text-[10px] transition-colors ${
              showContextPanel
                ? "text-blue-500 dark:text-blue-400"
                : "text-gray-500 hover:text-gray-900 dark:hover:text-gray-300"
            }`}
          >
            {showContextPanel ? <PanelRightClose size={11} /> : <PanelRightOpen size={11} />}
            Context
          </button>
          <button
            onClick={toggleTerminal}
            title={showTerminal ? "Hide terminal (⌘`)" : "Show terminal (⌘`)"}
            className="text-gray-500 transition-colors hover:text-gray-900 dark:hover:text-gray-300"
          >
            {showTerminal ? (
              <PanelBottomClose size={13} />
            ) : (
              <PanelBottomOpen size={13} />
            )}
          </button>
        </div>

        {pipelineActive && showPipeline && <ResearchModePipelineProgress />}
      </div>

      <div className="flex min-w-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          <ResearchStatusStrip
            primaryTab={primaryTab}
            activePaperTitle={activePaperTitle}
            paperCount={papers.length}
            runningTrainingCount={runningTrainingCount}
            showContextPanel={showContextPanel}
          />
          <Allotment vertical>
            <Allotment.Pane minSize={200}>
              <Allotment>
                <Allotment.Pane minSize={300}>
                  <ResearchModePrimaryPanel
                    furnacePanel={(
                      <Suspense fallback={<FurnacePanelLoader />}>
                        <ResearchFurnacePanel sessionsController={furnaceSessions} />
                      </Suspense>
                    )}
                  />
                </Allotment.Pane>
                {showContextPanel && (
                  <Allotment.Pane preferredSize={320} minSize={200}>
                    <ResearchModeContextPanel />
                  </Allotment.Pane>
                )}
              </Allotment>
            </Allotment.Pane>

            {showTerminal && (
              <Allotment.Pane preferredSize={200} minSize={100}>
                <div className="flex h-full flex-col border-t border-gray-200 dark:border-gray-800">
                  <div className="flex shrink-0 items-center border-b border-gray-200 bg-gray-50 px-2 py-0.5 dark:border-gray-800 dark:bg-[#252526]">
                    <span className="flex items-center gap-1.5 text-[10px] text-gray-400">
                      <TerminalIcon size={11} /> Terminal
                    </span>
                    <span className="flex-1" />
                    <button
                      onClick={toggleTerminal}
                      className="p-0.5 text-gray-500 transition-colors hover:text-gray-900 dark:hover:text-gray-300"
                      title="Close terminal"
                    >
                      <X size={12} />
                    </button>
                  </div>
                  <div className="min-h-0 flex-1">
                    <TerminalPanel />
                  </div>
                </div>
              </Allotment.Pane>
            )}
          </Allotment>
        </div>
        {showChatSidebar && (
          <div className="w-[350px] min-w-[250px] max-w-[500px] shrink-0 border-l border-gray-200 dark:border-gray-800">
            <ModeChatSidebar
              mode="research"
              onClose={closeChatSidebar}
              contextProvider={researchModeChatContext}
            />
          </div>
        )}
      </div>
    </div>
  );
}
