import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Layers, PanelLeft, Plus, X } from "lucide-react";
import { directionKeys, heldDirectionSlot, reconcileSessionSlots, type NavigationSession } from "./navigation";

export interface NavigationProject {
  id: string;
  name: string;
  root: string;
  sessions: NavigationSession[];
}
interface Props {
  projects: NavigationProject[];
  activeProjectId: string | null;
  activeSessionId: string | null;
  onProject: (id: string) => void;
  onSession: (id: string) => void;
  onNewSession: () => void;
  onNewProject: () => void;
  onAllSessions: () => void;
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
}
const storageKey = "dan.workbench.sessionSlots.v1";
function readSlots(): Record<string, (string | null)[]> {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(storageKey) || "{}");
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    return Object.fromEntries(Object.entries(value).filter(([, slots]) =>
      Array.isArray(slots) && slots.every((id) => id === null || typeof id === "string")));
  } catch { return {}; }
}

export function WorkbenchNavigation(props: Props) {
  const [mode, setMode] = useState<"projects" | "sessions" | null>(null);
  const [index, setIndex] = useState(0);
  const [selectedSlot, setSelectedSlot] = useState(0);
  const [storedSlots, setStoredSlots] = useState(readSlots);
  const dialog = useRef<HTMLDialogElement>(null);
  const dialogContent = useRef<HTMLDivElement>(null);
  const restoreFocus = useRef<HTMLElement | null>(null);
  const lastWheel = useRef(0);
  const pointerStart = useRef<number | null>(null);
  const heldDirections = useRef(new Set<string>());
  useEffect(() => {
    const clear = () => heldDirections.current.clear();
    window.addEventListener("blur", clear);
    return () => window.removeEventListener("blur", clear);
  }, []);
  const project = props.projects.find((item) => item.id === props.activeProjectId);
  const sessions = project?.sessions ?? [];
  const slots = useMemo(() => reconcileSessionSlots(storedSlots[project?.id ?? ""] ?? [], sessions), [storedSlots, project?.id, sessions]);
  // Persist only while open: a transient empty list during API loading must not erase slots.
  useEffect(() => {
    if (mode !== "sessions" || !project || sessions.length === 0) return;
    if (JSON.stringify(storedSlots[project.id]) === JSON.stringify(slots)) return;
    const next = { ...storedSlots, [project.id]: slots };
    setStoredSlots(next);
    try { localStorage.setItem(storageKey, JSON.stringify(next)); } catch { /* In-memory navigation still works. */ }
  }, [mode, project, sessions.length, slots, storedSlots]);

  const open = (next: "projects" | "sessions") => {
    heldDirections.current.clear();
    restoreFocus.current = document.activeElement as HTMLElement;
    setIndex(Math.max(0, props.projects.findIndex((item) => item.id === props.activeProjectId)));
    setSelectedSlot(Math.max(0, slots.indexOf(props.activeSessionId)));
    setMode(next);
  };
  const close = () => { heldDirections.current.clear(); setMode(null); };
  useEffect(() => {
    const node = dialog.current;
    if (mode) { node?.showModal(); dialogContent.current?.focus(); }
    else if (node?.open) {
      node.close();
      restoreFocus.current?.focus();
    }
  }, [mode]);
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.code === "KeyB" && (event.metaKey || event.ctrlKey) && !event.shiftKey && !event.altKey && !event.repeat && !event.isComposing) {
        if (document.querySelector("dialog[open]")) return;
        event.preventDefault();
        props.onToggleSidebar();
        return;
      }
      if (!(event.metaKey || event.ctrlKey) || !event.shiftKey || event.altKey || event.repeat) return;
      if (event.code !== "KeyE" && event.code !== "KeyS") return;
      event.preventDefault();
      if (!mode) open(event.code === "KeyE" ? "projects" : "sessions");
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  });
  const move = (delta: number) => setIndex((current) => Math.max(0, Math.min(props.projects.length - 1, current + delta)));
  const commit = (slot = selectedSlot) => {
    if (mode === "projects") {
      const target = props.projects[index];
      if (!target) return;
      props.onProject(target.id);
    } else {
      const id = slots[slot];
      if (!id) return;
      props.onSession(id);
    }
    close();
  };
  const preview = props.projects[index];
  const selected = sessions.find((item) => item.id === slots[selectedSlot]);
  const modifier = typeof navigator !== "undefined" && /Mac/.test(navigator.platform) ? "⌘" : "Ctrl";
  return <>
    <div className="wb-navigation">
      <span className="wb-sidebar-toggle"><button onClick={props.onToggleSidebar} aria-label={props.sidebarOpen ? "Hide sidebar" : "Show sidebar"} title={`${props.sidebarOpen ? "Hide sidebar" : "Show sidebar"} (${modifier}B)`} aria-keyshortcuts="Meta+B Control+B" aria-expanded={props.sidebarOpen} aria-controls="wb-project-sidebar"><PanelLeft size={18} /></button></span>
      <button onClick={() => open("projects")} title={`Switch project (${modifier}⇧E)`} aria-keyshortcuts="Meta+Shift+E Control+Shift+E"><Layers size={17} /><span>{project?.name || "Projects"}</span><span className="wb-shortcut">{modifier}⇧E</span></button>
      <span className="wb-divider">/</span>
      <button onClick={() => open("sessions")} title={`Switch session (${modifier}⇧S)`} aria-keyshortcuts="Meta+Shift+S Control+Shift+S"><span>{sessions.find((item) => item.id === props.activeSessionId)?.title || "Sessions"}</span><span className="wb-shortcut">{modifier}⇧S</span></button>
      <button onClick={props.onNewSession} aria-label="New session" title="New session"><Plus size={18} /></button>
    </div>
    <dialog ref={dialog} className="wb-switcher" aria-labelledby="wb-switcher-title" onCancel={(event) => { event.preventDefault(); close(); }} onClick={(event) => { if (event.target === event.currentTarget) close(); }} onKeyUp={(event) => { heldDirections.current.delete(event.key.toLowerCase()); }} onKeyDown={(event) => {
      if (event.nativeEvent.isComposing || event.metaKey || event.ctrlKey || event.altKey) return;
      const directionKey = event.key.length === 1 ? event.key.toLowerCase() : event.key;
      if (event.key === "Tab") {
        const controls = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled):not([tabindex='-1'])"));
        const position = controls.indexOf(document.activeElement as HTMLButtonElement);
        if (controls.length && (position < 0 || (!event.shiftKey && position === controls.length - 1) || (event.shiftKey && position === 0))) {
          event.preventDefault();
          controls[event.shiftKey ? controls.length - 1 : 0].focus();
        }
        return;
      }
      if (event.key === "Escape") { event.preventDefault(); close(); return; }
      if (mode === "projects" && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "w", "a", "s", "d"].includes(directionKey)) {
        event.preventDefault(); move(["ArrowRight", "ArrowDown", "s", "d"].includes(directionKey) ? 1 : -1); dialogContent.current?.focus();
      } else if (mode === "sessions" && directionKeys[directionKey] !== undefined) {
        event.preventDefault();
        if (event.repeat) return;
        if (["q", "e", "z", "c"].includes(directionKey)) {
          setSelectedSlot(directionKeys[directionKey]);
        } else {
          heldDirections.current.add(event.key.toLowerCase());
          const slot = heldDirectionSlot(heldDirections.current);
          if (slot !== null) setSelectedSlot(slot);
        }
        dialogContent.current?.focus();
      } else if (mode === "sessions" && /^[1-8]$/.test(event.key)) {
        event.preventDefault(); commit(Number(event.key) - 1);
      } else if (event.key === "Enter" && !(event.target instanceof HTMLButtonElement)) {
        event.preventDefault(); commit();
      }
    }}>
      <div className="wb-switcher-content" tabIndex={-1} ref={dialogContent}>
        <div className="wb-switcher-heading"><div><p className="wb-eyebrow">{mode === "projects" ? "YOUR WORKSPACES" : project?.name || "CURRENT PROJECT"}</p><h2 id="wb-switcher-title">{mode === "projects" ? "Move between projects" : "Pick up a session"}</h2></div><button onClick={close} aria-label="Close switcher"><X size={20} /></button></div>
        {mode === "projects" ? <>
          <div className="wb-carousel" onWheel={(event) => {
            const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
            if (Math.abs(delta) < 12 || Date.now() - lastWheel.current < 220) return;
            lastWheel.current = Date.now(); move(delta > 0 ? 1 : -1);
          }} onTouchStart={(event) => { pointerStart.current = event.touches[0].clientX; }} onTouchEnd={(event) => {
            if (pointerStart.current !== null) {
              const delta = pointerStart.current - event.changedTouches[0].clientX;
              if (Math.abs(delta) > 35) move(delta > 0 ? 1 : -1);
              pointerStart.current = null;
            }
          }}>
            {props.projects.map((item, position) => {
              const offset = position - index;
              if (Math.abs(offset) > 2) return null;
              return <button key={item.id} className={`wb-project ${offset === 0 ? "is-current" : ""}`} tabIndex={offset === 0 ? 0 : -1} aria-label={`${item.name}${offset === 0 ? ", open project" : ", preview project"}`} style={{ transform: `translateX(calc(-50% + ${offset * 65}%)) translateZ(${-Math.abs(offset) * 130}px) rotateY(${offset * -9}deg)`, opacity: Math.max(.18, 1 - Math.abs(offset) * .36), zIndex: 3 - Math.abs(offset) }} onClick={() => { if (offset === 0) commit(); else setIndex(position); }}>
                <span className="wb-project-number">{String(position + 1).padStart(2, "0")} / {String(props.projects.length).padStart(2, "0")}</span>
                <h3>{item.name}</h3><p className="wb-project-root">{item.root || "No folder selected"}</p>
                <div className="wb-project-sessions">{[...item.sessions].sort((a, b) => b.createdAt.localeCompare(a.createdAt)).slice(0, 3).map((session) => <div key={session.id}>{session.title}</div>)}{!item.sessions.length && <div>A fresh space for your next idea.</div>}</div>
                <span className="wb-project-footer">{item.sessions.length} sessions <span>{item.id === props.activeProjectId ? "Current project" : "Enter to open"}</span></span>
              </button>;
            })}
            {!props.projects.length && <p>No projects yet. Create one to begin.</p>}
          </div>
          <div className="wb-carousel-controls"><button onClick={() => move(-1)} disabled={index === 0} aria-label="Previous project"><ChevronLeft size={18} /></button><span title={preview?.name || "Projects"}>{preview?.name || "Projects"}</span><button onClick={() => move(1)} disabled={index >= props.projects.length - 1} aria-label="Next project"><ChevronRight size={18} /></button></div>
        </> : <div className="wb-wheel">
          <div className="wb-wheel-center"><span className="wb-eyebrow">SESSION</span><strong>{selected?.title || "Empty slot"}</strong><button disabled={!selected} onClick={() => commit()}>Open session ↵</button></div>
          {slots.map((id, position) => {
            const session = sessions.find((item) => item.id === id);
            const angle = position * Math.PI / 4;
            return <button key={position} disabled={!session} className={`wb-wheel-slot ${selectedSlot === position ? "is-selected" : ""}`} style={{ left: `${50 + Math.sin(angle) * 37}%`, top: `${50 - Math.cos(angle) * 37}%` }} onFocus={() => setSelectedSlot(position)} onMouseEnter={() => setSelectedSlot(position)} onClick={() => commit(position)} aria-current={id && id === props.activeSessionId ? "page" : undefined} title={session?.title || "Empty slot"}><span className="wb-slot-number">{position + 1}</span><span>{session?.title || "Empty"}</span>{id && id === props.activeSessionId && <small>Current</small>}</button>;
          })}
        </div>}
        <footer className="wb-switcher-footer">{mode === "projects" ? <p>A / W previous · D / S next · Arrows or scroll · Enter to open · Esc to return</p> : <div className="wb-wheel-controls"><span><kbd>1–8</kbd> Open directly</span><span><kbd>↑ ↓ ← →</kbd> Aim · combine for diagonals</span><span><kbd>WASD</kbd> Aim · combine, or use <kbd>Q E Z C</kbd></span><span>Enter to open · Esc to return</span></div>}<button onClick={() => { close(); if (mode === "projects") props.onNewProject(); else props.onAllSessions(); }}>{mode === "projects" ? "+ New project" : "All sessions →"}</button></footer>
      </div>
    </dialog>
  </>;
}
