import { useEffect, useId, useRef, useState } from "react";
import { GitCompare, Check, ChevronDown, Activity, BookOpen, Library, Eye, FolderOpen, MessageSquareText, StickyNote, SquareTerminal, Users, X, type LucideIcon } from "lucide-react";

export type SideTab = "chat" | "notes" | "files" | "preview" | "activity" | "team" | "processes" | "literature" | "changes";
export type SideTabItem = { id: SideTab; label: string; count?: number; live?: boolean };

const ICONS: Record<SideTab, LucideIcon> = { chat: MessageSquareText, notes: StickyNote, files: FolderOpen, preview: Eye, activity: Activity, team: Users, processes: SquareTerminal, literature: Library, changes: GitCompare };
const IDS: SideTab[] = ["chat", "notes", "files", "preview", "activity", "team", "processes", "literature", "changes"];
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

const DESCRIPTIONS: Record<SideTab, string> = {
  changes: "Review changes since your last instruction",
  chat: "Talk with Diane",
  notes: "Notes and highlights for this document",
  files: "Browse project files",
  literature: "Import PDFs and build reading notes",
  preview: "Inspect the selected file",
  activity: "Follow task progress and tool use",
  team: "Inspect agents and their work",
  processes: "Manage commands and running services",
};

/** One current tool, with all context-available tools in a labeled switcher. */
export function SideTabs({ tabs, active, onSelect, onClose, reading = false }: {
  tabs: SideTabItem[]; active: SideTab; onSelect: (tab: SideTab) => void; onClose: () => void; reading?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const header = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const shown = tabs.some((tab) => tab.id === active) ? tabs : [...tabs, { id: active, label: active[0].toUpperCase() + active.slice(1) }];
  const selected = shown.find((tab) => tab.id === active)!;
  const Icon = reading && active === "chat" ? BookOpen : ICONS[active];
  const running = shown.find((tab) => tab.live && tab.id !== active);

  useEffect(() => {
    if (!open) return;
    menu.current?.querySelector<HTMLButtonElement>('[aria-checked="true"]')?.focus();
    const dismiss = (event: PointerEvent) => {
      if (!header.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open, active]);

  const closeMenu = () => { setOpen(false); trigger.current?.focus(); };
  return <div className="wb-side-tabs" ref={header} onBlur={(event) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
  }} onKeyDown={(event) => {
    if (open && event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeMenu(); }
  }}>
    <button ref={trigger} type="button" className="wb-side-switcher" aria-haspopup="menu" aria-expanded={open}
      aria-controls={open ? menuId : undefined} aria-label={`${selected.label}, switch side panel tool`}
      title="Switch side panel tool" onClick={() => setOpen(!open)} onKeyDown={(event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); setOpen(true); }
      }}>
      <Icon size={15} aria-hidden="true" />
      <span className="wb-side-current">{selected.label}</span>
      {selected.live ? <span className="wb-side-status">Running</span> : selected.count ? <span className="wb-side-count">{selected.count}</span> : null}
      <ChevronDown size={13} aria-hidden="true" />
    </button>
    {running && <button type="button" className="wb-side-running" title={`Open ${running.label}`} onClick={() => onSelect(running.id)}>
      <i className="wb-side-live" aria-hidden="true" /> Running
    </button>}
    <button type="button" className="wb-side-close" onClick={onClose} aria-label="Close side panel"><X size={15} /></button>
    {open && <div ref={menu} id={menuId} className="wb-side-menu" role="menu" aria-label="Side panel tools" onKeyDown={(event) => {
      const items = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]'));
      const index = items.indexOf(document.activeElement as HTMLButtonElement);
      const next = event.key === "ArrowDown" ? (index + 1) % items.length
        : event.key === "ArrowUp" ? (index - 1 + items.length) % items.length
        : event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : -1;
      if (next >= 0) { event.preventDefault(); event.stopPropagation(); items[next]?.focus(); }
    }}>
      {shown.map((tab) => {
        const ToolIcon = reading && tab.id === "chat" ? BookOpen : ICONS[tab.id];
        return <button key={tab.id} type="button" role="menuitemradio" aria-checked={active === tab.id} tabIndex={-1}
          onClick={() => { onSelect(tab.id); closeMenu(); }}>
          <ToolIcon size={16} aria-hidden="true" />
          <span className="wb-side-option"><span>{tab.label}</span><small>{reading && tab.id === "chat" ? "Discuss the open document" : DESCRIPTIONS[tab.id]}</small></span>
          {tab.live ? <span className="wb-side-status">Running</span> : tab.count ? <span className="wb-side-count">{tab.count}</span> : null}
          <Check size={14} className="wb-side-check" aria-hidden="true" style={{ visibility: active === tab.id ? "visible" : "hidden" }} />
        </button>;
      })}
    </div>}
  </div>;
}
