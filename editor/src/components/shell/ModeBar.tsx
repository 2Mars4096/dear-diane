import { useEffect } from "react";
import { useAppStore, MODE_CONFIGS } from "../../store/useAppStore";
import {
  MessageSquare,
  GraduationCap,
  Code,
  BarChart3,
  PenTool,
  Workflow,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

const ICON_MAP: Record<string, LucideIcon> = {
  MessageSquare,
  GraduationCap,
  Code,
  BarChart3,
  PenTool,
  Workflow,
};

export default function ModeBar() {
  const activeMode = useAppStore((s) => s.activeMode);
  const setMode = useAppStore((s) => s.setMode);

  const isElectron = typeof window !== "undefined" && "electronAPI" in window;
  const isMac = typeof navigator !== "undefined" && /Mac/.test(navigator.platform);

  return (
    <div className={`flex items-center gap-1 bg-gray-900 px-3 h-10 flex-shrink-0 app-drag-region ${isElectron && isMac ? "pl-20" : ""}`}>
      <span className="text-white font-bold text-sm mr-3 tracking-wide select-none">DAN</span>
      <div className="flex items-center gap-0.5">
        {MODE_CONFIGS.map((mode) => {
          const Icon = ICON_MAP[mode.icon];
          const isActive = activeMode === mode.id;
          return (
            <button
              key={mode.id}
              onClick={() => setMode(mode.id)}
              disabled={!mode.enabled}
              title={mode.enabled ? `${mode.label} (⌘${mode.shortcut})` : `${mode.label} (coming soon)`}
              className={`
                app-no-drag flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-all
                ${isActive
                  ? "bg-white/15 text-white"
                  : mode.enabled
                    ? "text-gray-400 hover:text-white hover:bg-white/5"
                    : "text-gray-600 cursor-not-allowed"
                }
              `}
            >
              {Icon && <Icon size={14} />}
              <span>{mode.label}</span>
            </button>
          );
        })}
      </div>
      <div className="flex-1" />
    </div>
  );
}

export function useModeShortcuts() {
  const setMode = useAppStore((s) => s.setMode);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!e.metaKey && !e.ctrlKey) return;
      const match = MODE_CONFIGS.find((m) => m.shortcut === e.key && m.enabled);
      if (match) {
        e.preventDefault();
        setMode(match.id);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [setMode]);
}
