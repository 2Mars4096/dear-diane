import { BookOpen, FileText, MessageSquareText, Settings, X } from "lucide-react";
import { useEffect } from "react";

import type { MainTab } from "./mainTabState";
export { closeMainTab, type MainTab } from "./mainTabState";

/** Tab bar for the main column: the conversation plus any open documents, all closable. */
export function MainTabs({ tabs, active, onSelect, onClose }: { tabs: MainTab[]; active: string; onSelect: (id: string) => void; onClose: (id: string) => void }) {
  const mac = /Mac|iPhone|iPad/.test(navigator.platform);
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      const modifiers = mac ? event.metaKey && event.ctrlKey && !event.altKey : event.ctrlKey && event.altKey && !event.metaKey;
      if (event.code !== "KeyW" || !modifiers || event.shiftKey || event.repeat || event.isComposing || event.defaultPrevented) return;
      if (tabs.length <= 1 || document.querySelector("dialog[open], [role=dialog][aria-modal=true]")) return;
      event.preventDefault();
      onClose(active);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [active, mac, onClose, tabs.length]);
  return <div className="wb-main-tabs" role="tablist" aria-label="Open tabs">
    {tabs.map((tab) => <div key={tab.id} className="wb-main-tab" data-active={tab.id === active || undefined}>
      <button type="button" role="tab" aria-selected={tab.id === active} title={tab.title ?? tab.label} onClick={() => onSelect(tab.id)}
        onAuxClick={(event) => { if (event.button === 1) onClose(tab.id); }}>
        {tab.kind === "papers" ? <BookOpen size={13} aria-hidden="true" /> : (tab.kind === "pdf" || tab.kind === "file") ? <FileText size={13} aria-hidden="true" /> : tab.kind === "settings" ? <Settings size={13} aria-hidden="true" /> : <MessageSquareText size={13} aria-hidden="true" />}
        <span>{tab.label}</span>
      </button>
      {tabs.length > 1 && <button type="button" className="wb-main-tab-close" aria-label={`Close ${tab.label}`} title={`Close tab (${mac ? "⌃⌘W" : "Ctrl+Alt+W"})`} aria-keyshortcuts={tab.id === active ? mac ? "Control+Meta+W" : "Control+Alt+W" : undefined} onClick={() => onClose(tab.id)}><X size={12} /></button>}
    </div>)}
  </div>;
}
