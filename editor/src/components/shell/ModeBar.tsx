import { useEffect } from "react";
import { useAppStore, MODE_CONFIGS } from "../../store/useAppStore";
import {
  MessageSquare,
  GraduationCap,
  Code,
  BarChart3,
  PenTool,
  Workflow,
  Command,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import NotificationCenter from "./NotificationCenter";

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
  const setGlobalPaletteVisible = useAppStore((s) => s.setGlobalPaletteVisible);

  const isElectron = typeof window !== "undefined" && "electronAPI" in window;
  const isMac = typeof navigator !== "undefined" && /Mac/.test(navigator.platform);

  return (
    <div className={`flex items-center gap-1 bg-gray-100 dark:bg-gray-900 border-b border-gray-200 dark:border-gray-800 px-3 h-10 flex-shrink-0 app-drag-region ${isElectron && isMac ? "pl-20" : ""}`}>
      <span className="text-gray-900 dark:text-white font-bold text-sm mr-3 tracking-wide select-none">DAN</span>
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
                  ? "bg-gray-200 text-gray-900 dark:bg-white/15 dark:text-white"
                  : mode.enabled
                    ? "text-gray-500 hover:text-gray-900 hover:bg-gray-200 dark:text-gray-400 dark:hover:text-white dark:hover:bg-white/5"
                    : "text-gray-400 dark:text-gray-600 cursor-not-allowed"
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

      {/* Right-side actions */}
      <div className="flex items-center gap-1">
        <button
          onClick={() => setGlobalPaletteVisible(true)}
          className="app-no-drag p-1.5 rounded text-gray-500 hover:text-gray-800 hover:bg-gray-200 dark:text-gray-400 dark:hover:text-gray-200 dark:hover:bg-white/10 transition-colors"
          title="Command Palette (⇧⌘P)"
        >
          <Command size={15} />
        </button>
        <NotificationCenter />
      </div>
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
