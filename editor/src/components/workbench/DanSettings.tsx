import { lazy, Suspense, useEffect, useRef } from "react";
import { X } from "lucide-react";
const DesktopUpdates = lazy(() => import("./DesktopUpdates").then(module => ({ default: module.DesktopUpdates })));
const TokenUsage = lazy(() => import("./TokenUsage").then(module => ({ default: module.TokenUsage })));
const SkillPool = lazy(() => import("./SkillPool").then(module => ({ default: module.SkillPool })));
const RemoteConnections = lazy(() => import("./RemoteConnections").then(module => ({ default: module.RemoteConnections })));
import { useSettingsStore } from "../../store/useSettingsStore";
import { NativeWorkerSettings, type WorkerProfiles } from "./NativeWorkers";
import { PALETTE_ROLES, WORKBENCH_PALETTES, workbenchPalette } from "../../lib/workbenchPalette";

export function DanSettings({ onClose, profiles, onProfilesChange, onPapers, page = false }: {
  onPapers?: () => void;
  onClose: () => void; profiles: WorkerProfiles; onProfilesChange: (profiles: WorkerProfiles) => void; page?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const settings = useSettingsStore();
  useEffect(() => {
    if (page) return;
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => previous?.focus();
  }, [page]);
  // As a main-column tab the same content renders in a page frame instead of a modal dialog.
  const Frame = (page ? "section" : "dialog") as "dialog";
  return <Frame onClick={(event) => { const box = event.currentTarget.getBoundingClientRect(); if (event.target === event.currentTarget && (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom)) onClose(); }} ref={dialog} className={page ? "wb-dan-settings wb-settings-page" : "wb-project-settings wb-dan-settings"} onCancel={onClose} aria-labelledby="dan-settings-title">
    <header><h2 id="dan-settings-title">DAN settings</h2>{!page && <button aria-label="Close DAN settings" onClick={onClose}><X size={18} /></button>}</header>
    <p>Preferences for this DAN app profile. Changes save automatically.</p>
    <h3>Appearance</h3>
    <label className="wb-appearance-field">Mode<select value={settings.theme} onChange={(event) => settings.updateSetting("theme", event.target.value as typeof settings.theme)}>
      <option value="system">System</option><option value="vs">Light</option><option value="vs-dark">Dark</option>
      {settings.theme === "hc-black" && <option value="hc-black">High contrast (legacy)</option>}
    </select></label>
    <label className="wb-appearance-field">Color scheme<select value={workbenchPalette(settings.workbenchColorScheme).id} onChange={(event) => settings.updateSetting("workbenchColorScheme", workbenchPalette(event.target.value).id)}>
      {WORKBENCH_PALETTES.map((palette) => <option key={palette.id} value={palette.id}>{palette.label}</option>)}
    </select></label>
    <div className="wb-palette-preview" aria-label="Current palette colors">{PALETTE_ROLES.map((role) => <span key={role} title={role === "text" ? "Primary text" : role === "muted" ? "Muted text" : role}><i style={{ background: `var(--dan-wb-${role})` }} aria-hidden="true" /><span>{role === "background" ? "Base" : role === "text" ? "Text" : role}</span></span>)}</div>
    <p>The same scheme follows light and dark mode.</p>
    <Suspense fallback={<p>Loading update controls…</p>}><DesktopUpdates /></Suspense>
    <Suspense fallback={<p>Loading remote connections…</p>}><RemoteConnections /></Suspense>
    {onPapers && <><h3>Papers</h3><button onClick={onPapers}>Open paper library</button><p>Manage paper sources and the sidebar entry in the library settings.</p></>}
    <h3>Notes appearance</h3>
    <label>Surface<select value={settings.workspaceSurfaceTheme} onChange={(event) => settings.updateSetting("workspaceSurfaceTheme", event.target.value as typeof settings.workspaceSurfaceTheme)}><option value="original">Original</option><option value="industrial">Industrial</option><option value="factory-worn">Factory worn</option></select></label>
    <label>Color mode<select value={settings.workspaceSurfaceTone} onChange={(event) => settings.updateSetting("workspaceSurfaceTone", event.target.value as typeof settings.workspaceSurfaceTone)}><option value="system">System</option><option value="day">Day</option><option value="night">Night</option></select></label>
    <NativeWorkerSettings profiles={profiles} onChange={onProfilesChange} />
    <Suspense fallback={<p>Loading skills…</p>}><SkillPool /></Suspense>
    <Suspense fallback={<p>Loading token usage…</p>}><TokenUsage /></Suspense>
    {!page && <footer><button onClick={onClose}>Done</button></footer>}
  </Frame>;
}
