import { FileText, MessageSquareText, X } from "lucide-react";

export type MainTab = { id: string; kind: "chat" | "pdf"; label: string; title?: string };

/** Tab bar for the main column: the conversation plus any open documents, all closable. */
export function MainTabs({ tabs, active, onSelect, onClose }: { tabs: MainTab[]; active: string; onSelect: (id: string) => void; onClose: (id: string) => void }) {
  return <div className="wb-main-tabs" role="tablist" aria-label="Open tabs">
    {tabs.map((tab) => <div key={tab.id} className="wb-main-tab" data-active={tab.id === active || undefined}>
      <button type="button" role="tab" aria-selected={tab.id === active} title={tab.title ?? tab.label} onClick={() => onSelect(tab.id)}
        onAuxClick={(event) => { if (event.button === 1) onClose(tab.id); }}>
        {tab.kind === "pdf" ? <FileText size={13} aria-hidden="true" /> : <MessageSquareText size={13} aria-hidden="true" />}
        <span>{tab.label}</span>
      </button>
      {tabs.length > 1 && <button type="button" className="wb-main-tab-close" aria-label={`Close ${tab.label}`} onClick={() => onClose(tab.id)}><X size={12} /></button>}
    </div>)}
  </div>;
}

/** Closing the active tab activates its neighbour; the last tab cannot be closed. */
export function closeMainTab(tabs: MainTab[], active: string, id: string): { tabs: MainTab[]; active: string } {
  if (tabs.length <= 1) return { tabs, active };
  const index = tabs.findIndex((tab) => tab.id === id);
  if (index < 0) return { tabs, active };
  const next = tabs.filter((tab) => tab.id !== id);
  return { tabs: next, active: active === id ? next[Math.min(index, next.length - 1)].id : active };
}
