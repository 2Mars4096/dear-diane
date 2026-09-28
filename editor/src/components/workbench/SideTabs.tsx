import { Activity, BookOpen, Library, Eye, FolderOpen, MessageSquareText, StickyNote, SquareTerminal, Users, X, type LucideIcon } from "lucide-react";

export type SideTab = "chat" | "notes" | "files" | "preview" | "activity" | "team" | "processes" | "literature";
export type SideTabItem = { id: SideTab; label: string; count?: number; live?: boolean };

const ICONS: Record<SideTab, LucideIcon> = { chat: MessageSquareText, notes: StickyNote, files: FolderOpen, preview: Eye, activity: Activity, team: Users, processes: SquareTerminal, literature: Library };
const IDS: SideTab[] = ["chat", "notes", "files", "preview", "activity", "team", "processes", "literature"];
const STORAGE_KEY = "dan.workbench.sideTab.v1";

export function readLastSideTab(): SideTab {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return IDS.includes(stored as SideTab) ? stored as SideTab : "chat";
  } catch { return "chat"; }
}
export function rememberSideTab(tab: SideTab) {
  try { localStorage.setItem(STORAGE_KEY, tab); } catch { /* per-device convenience only */ }
}

/**
 * Tabs are supplied by the caller and change with context (Team appears once a team
 * is working, Preview once something is selected). The open tab always stays listed.
 */
export function SideTabs({ tabs, active, onSelect, onClose, reading = false }: {
  tabs: SideTabItem[]; active: SideTab; onSelect: (tab: SideTab) => void; onClose: () => void; reading?: boolean;
}) {
  const shown = tabs.some((tab) => tab.id === active) ? tabs : [...tabs, { id: active, label: active[0].toUpperCase() + active.slice(1) }];
  return <div className="wb-side-tabs">
    <div className="wb-side-track" role="tablist" aria-label="Side panel">
      {shown.map((tab) => {
        const Icon = reading && tab.id === "chat" ? BookOpen : ICONS[tab.id];
        return <button key={tab.id} type="button" role="tab" aria-selected={active === tab.id} title={tab.label} onClick={() => onSelect(tab.id)}>
          <Icon size={13} aria-hidden="true" />
          <span className="wb-side-label">{tab.label}</span>
          {tab.live ? <i className="wb-side-live" aria-label="running" /> : tab.count ? <span className="wb-side-count">{tab.count}</span> : null}
        </button>;
      })}
    </div>
    <button type="button" className="wb-side-close" onClick={onClose} aria-label="Close side panel"><X size={15} /></button>
  </div>;
}
