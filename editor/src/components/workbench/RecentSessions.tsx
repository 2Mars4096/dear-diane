import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export type RecentSession = { id: string; workflowId: string; title: string; project: string; workspaceId: string; root: string };
const KEY = "dan.recentSessions.v1";
export const sessionKey = (session: Pick<RecentSession, "id" | "workflowId">) => `${session.workflowId}:${session.id}`;
export function visitSession(sessions: RecentSession[], session: RecentSession) {
  return [session, ...sessions.filter(row => sessionKey(row) !== sessionKey(session))].slice(0, 10);
}
export function previousSessions(sessions: RecentSession[], activeKey: string) {
  return sessions.filter(row => sessionKey(row) !== activeKey).slice(0, 9);
}
function stored(): RecentSession[] {
  try {
    // Old open tabs are deliberately not imported: their order is not visit history.
    const rows: unknown = JSON.parse(localStorage.getItem(KEY) || "[]");
    if (!Array.isArray(rows)) return [];
    const valid = rows.filter((row): row is RecentSession => row && ["id", "workflowId", "title", "project", "workspaceId", "root"].every(key => typeof row[key] === "string"));
    return [...new Map(valid.map(row => [sessionKey(row), row])).values()].slice(0, 10);
  } catch { return []; }
}

/** Mounted even with the sidebar hidden so keyboard navigation remains available. */
export default function RecentSessions({ active, host, titles, excluded, onSelect }: {
  active: RecentSession | null; host: HTMLElement | null; titles: Record<string, string>; excluded: string[]; onSelect: (session: RecentSession) => void;
}) {
  const [sessions, setSessions] = useState(stored);
  const [frozen, setFrozen] = useState<RecentSession[] | null>(null);
  const frozenRef = useRef<RecentSession[] | null>(null);
  const lastActive = useRef("");
  const activeKey = active ? sessionKey(active) : lastActive.current;
  useEffect(() => {
    if (!active) return;
    const entered = lastActive.current !== sessionKey(active);
    lastActive.current = sessionKey(active);
    setSessions(rows => entered ? visitSession(rows, active) : rows.map(row => sessionKey(row) === sessionKey(active) ? active : row));
  }, [active?.id, active?.workflowId, active?.title, active?.project, active?.workspaceId, active?.root]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { try { localStorage.setItem(KEY, JSON.stringify(sessions)); } catch { /* Navigation still works when storage is unavailable. */ } }, [sessions]);
  const choices = previousSessions(sessions.filter(row => !excluded.includes(sessionKey(row))), activeKey);
  const latest = useRef({ choices, onSelect }); latest.current = { choices, onSelect };
  useEffect(() => {
    const mac = /Mac|iPhone|iPad/.test(navigator.platform);
    const held = (event: KeyboardEvent) => mac ? event.metaKey : event.ctrlKey;
    const release = () => { frozenRef.current = null; setFrozen(null); };
    const freeze = () => {
      if (frozenRef.current === null) { frozenRef.current = [...latest.current.choices]; setFrozen(frozenRef.current); }
      return frozenRef.current;
    };
    const down = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.altKey || event.shiftKey || document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) return;
      if (!held(event)) return;
      if (event.key === (mac ? "Meta" : "Control")) { freeze(); return; }
      if (!/^[1-9]$/.test(event.key) || event.repeat) return;
      const session = freeze()[Number(event.key) - 1];
      if (session) { event.preventDefault(); latest.current.onSelect(session); }
    };
    const up = (event: KeyboardEvent) => { if (!held(event)) release(); };
    window.addEventListener("keydown", down); window.addEventListener("keyup", up); window.addEventListener("blur", release);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); window.removeEventListener("blur", release); };
  }, []);
  if (!host) return null;
  const rows = frozen ?? choices;
  const modifier = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "Ctrl+";
  return createPortal(<section className="wb-recent-sessions" aria-label="Recent sessions">
    <div className="wb-recent-heading">Recent sessions</div>
    {rows.length ? rows.map((session, index) => <button key={sessionKey(session)} type="button"
      title={`${session.project} · ${titles[sessionKey(session)] || session.title} (${modifier}${index + 1})`}
      onClick={() => onSelect(session)}>
      <span>{titles[sessionKey(session)] || session.title || "New session"}</span><kbd>{modifier}{index + 1}</kbd>
    </button>) : <p>Sessions you visit appear here. {modifier}1 returns to the previous one.</p>}
  </section>, host);
}
