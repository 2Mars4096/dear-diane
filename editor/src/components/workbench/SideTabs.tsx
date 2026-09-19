import { X } from "lucide-react";

export type SideTab = "chat" | "files" | "preview" | "activity" | "team";
const TABS: { id: SideTab; label: string }[] = [
  { id: "chat", label: "Chat" },
  { id: "files", label: "Files" },
  { id: "preview", label: "Preview" },
  { id: "activity", label: "Activity" },
  { id: "team", label: "Team" },
];
const STORAGE_KEY = "dan.workbench.sideTab.v1";

export function readLastSideTab(): SideTab {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return TABS.some((tab) => tab.id === stored) ? stored as SideTab : "chat";
  } catch { return "chat"; }
}
export function rememberSideTab(tab: SideTab) {
  try { localStorage.setItem(STORAGE_KEY, tab); } catch { /* per-device convenience only */ }
}

/** One tab bar shared by every side panel, so switching tools never changes the frame. */
export function SideTabs({ active, onSelect, onClose, counts = {} }: {
  active: SideTab; onSelect: (tab: SideTab) => void; onClose: () => void; counts?: Partial<Record<SideTab, number>>;
}) {
  return <div className="wb-side-tabs" role="tablist" aria-label="Side panel">
    {TABS.map((tab) => <button key={tab.id} type="button" role="tab" aria-selected={active === tab.id} onClick={() => onSelect(tab.id)}>
      {tab.label}{counts[tab.id] ? <span>{counts[tab.id]}</span> : null}
    </button>)}
    <button type="button" className="wb-side-close" onClick={onClose} aria-label="Close side panel"><X size={15} /></button>
  </div>;
}
