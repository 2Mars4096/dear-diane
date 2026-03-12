/**
 * AppShell: top-level layout that wraps all mode workspaces.
 * Structure: ModeBar (top) → active mode workspace (center) → overlays.
 * Each mode is lazy-rendered but kept mounted to preserve state.
 */
import ModeBar, { useModeShortcuts } from "./ModeBar";
import ErrorBoundary from "./ErrorBoundary";
import { useAppStore } from "../../store/useAppStore";
import ChatMode from "../modes/ChatMode";
import OperationsMode from "../modes/OperationsMode";
import ToastContainer from "../ToastContainer";

const PLACEHOLDER_MODES = ["research", "development", "analytics", "content"] as const;
const MODE_LABELS: Record<string, string> = {
  research: "Research Mode",
  development: "Development Mode",
  analytics: "Analytics Mode",
  content: "Content Mode",
};

export default function AppShell() {
  const activeMode = useAppStore((s) => s.activeMode);
  useModeShortcuts();

  return (
    <div className="h-screen w-screen flex flex-col bg-white">
      <ModeBar />

      <div className="flex-1 min-h-0 relative">
        <div className={`absolute inset-0 ${activeMode === "chat" ? "" : "hidden"}`}>
          <ErrorBoundary name="Chat">
            <ChatMode />
          </ErrorBoundary>
        </div>
        <div className={`absolute inset-0 ${activeMode === "operations" ? "" : "hidden"}`}>
          <ErrorBoundary name="Operations">
            <OperationsMode />
          </ErrorBoundary>
        </div>

        {PLACEHOLDER_MODES.map((m) =>
          activeMode === m ? (
            <div key={m} className="flex items-center justify-center h-full text-gray-400">
              <div className="text-center">
                <p className="text-lg font-medium">{MODE_LABELS[m]}</p>
                <p className="text-sm mt-1">Coming soon</p>
              </div>
            </div>
          ) : null,
        )}
      </div>

      <ToastContainer />
    </div>
  );
}
