import { NotificationSetting } from "./WorkNotifications";
import { lazy, Suspense, useEffect, useRef } from "react";
import { X } from "lucide-react";
const DesktopUpdates = lazy(() => import("./DesktopUpdates").then(module => ({ default: module.DesktopUpdates })));
const TokenUsage = lazy(() => import("./TokenUsage").then(module => ({ default: module.TokenUsage })));
const ApiProviders = lazy(() => import("./ApiProviders").then(module => ({ default: module.ApiProviders })));
const SkillPool = lazy(() => import("./SkillPool").then(module => ({ default: module.SkillPool })));
const RemoteConnections = lazy(() => import("./RemoteConnections").then(module => ({ default: module.RemoteConnections })));
import { useSettingsStore } from "../../store/useSettingsStore";
import { NativeWorkerSettings, type WorkerProfiles } from "./NativeWorkers";
import { PALETTE_ROLES, WORKBENCH_PALETTES, workbenchPalette } from "../../lib/workbenchPalette";

export function DianeSettings({ onClose, profiles, onProfilesChange, onPapers, page = false }: {
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
  const sections = [["appearance", "Appearance"], ["connections", "Connections"], ["agents", "Agents & skills"], ...(onPapers ? [["library", "Library"]] : []), ["usage", "Usage"], ["notifications", "Notifications"], ["app", "App updates"]];
  const jumpTo = (id: string) => {
    const section = dialog.current?.querySelector<HTMLElement>(`#settings-${id}`);
    section?.scrollIntoView({ block: "start" });
    section?.focus({ preventScroll: true });
  };
  // As a main-column tab the same content renders in a page frame instead of a modal dialog.
  const Frame = (page ? "section" : "dialog") as "dialog";
  return <Frame onClick={(event) => { const box = event.currentTarget.getBoundingClientRect(); if (event.target === event.currentTarget && (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom)) onClose(); }} ref={dialog} className={page ? "wb-dan-settings wb-settings-page" : "wb-project-settings wb-dan-settings"} onCancel={onClose} aria-labelledby="dan-settings-title">
    <header><h2 id="dan-settings-title">Settings</h2>{!page && <button aria-label="Close settings" onClick={onClose}><X size={18} /></button>}</header>
    <p>Preferences for this app profile. Appearance changes save automatically.</p>
    <nav className="wb-settings-jump" aria-label="Settings sections">{sections.map(([id, label]) => <button key={id} type="button" onClick={() => jumpTo(id)}>{label}</button>)}</nav>
    <section id="settings-appearance" className="wb-settings-section" tabIndex={-1} aria-label="Appearance">
    <h3 className="wb-settings-section-title">Appearance</h3>
    <label className="wb-appearance-field">Mode<select value={settings.theme} onChange={(event) => settings.updateSetting("theme", event.target.value as typeof settings.theme)}>
      <option value="system">System</option><option value="vs">Light</option><option value="vs-dark">Dark</option>
      {settings.theme === "hc-black" && <option value="hc-black">High contrast (legacy)</option>}
    </select></label>
    <label className="wb-appearance-field">Color scheme<select value={workbenchPalette(settings.workbenchColorScheme).id} onChange={(event) => settings.updateSetting("workbenchColorScheme", workbenchPalette(event.target.value).id)}>
      {WORKBENCH_PALETTES.map((palette) => <option key={palette.id} value={palette.id}>{palette.label}</option>)}
    </select></label>
    <div className="wb-palette-preview" aria-label="Current palette colors">{PALETTE_ROLES.map((role) => <span key={role} title={role === "text" ? "Primary text" : role === "muted" ? "Muted text" : role}><i style={{ background: `var(--dan-wb-${role})` }} aria-hidden="true" /><span>{role === "background" ? "Base" : role === "text" ? "Text" : role}</span></span>)}</div>
    <p>The same scheme follows light and dark mode.</p>
    <h3>Notes appearance</h3>
    <label>Surface<select value={settings.workspaceSurfaceTheme} onChange={(event) => settings.updateSetting("workspaceSurfaceTheme", event.target.value as typeof settings.workspaceSurfaceTheme)}><option value="original">Original</option><option value="industrial">Industrial</option><option value="factory-worn">Factory worn</option></select></label>
    <label>Color mode<select value={settings.workspaceSurfaceTone} onChange={(event) => settings.updateSetting("workspaceSurfaceTone", event.target.value as typeof settings.workspaceSurfaceTone)}><option value="system">System</option><option value="day">Day</option><option value="night">Night</option></select></label>
    </section>
    <section id="settings-connections" className="wb-settings-section" tabIndex={-1} aria-label="Connections">
      <h3 className="wb-settings-section-title">Connections</h3>
      <Suspense fallback={<p>Loading SSH connections…</p>}><RemoteConnections /></Suspense>
    </section>
    <section id="settings-agents" className="wb-settings-section" tabIndex={-1} aria-label="Agents and skills">
      <h3 className="wb-settings-section-title">Agents & skills</h3>
      <Suspense fallback={<p>Loading API providers…</p>}><ApiProviders /></Suspense>
      <NativeWorkerSettings profiles={profiles} onChange={onProfilesChange} />
      <Suspense fallback={<p>Loading skills…</p>}><SkillPool /></Suspense>
    </section>
    {onPapers && <section id="settings-library" className="wb-settings-section" tabIndex={-1} aria-label="Library">
      <h3 className="wb-settings-section-title">Library</h3>
      <button onClick={onPapers}>Open paper library</button><p>Manage paper sources and the sidebar entry in the library settings.</p>
    </section>}
    <section id="settings-usage" className="wb-settings-section" tabIndex={-1} aria-label="Usage">
      <h3 className="wb-settings-section-title">Usage</h3>
      <Suspense fallback={<p>Loading token usage…</p>}><TokenUsage /></Suspense>
    </section>
    <section id="settings-notifications" className="wb-settings-section" tabIndex={-1} aria-label="Notifications">
      <h3 className="wb-settings-section-title">Notifications</h3><NotificationSetting />
    </section>
    <section id="settings-app" className="wb-settings-section" tabIndex={-1} aria-label="App updates">
      <h3 className="wb-settings-section-title">App updates</h3>
      <Suspense fallback={<p>Loading update controls…</p>}><DesktopUpdates /></Suspense>
    </section>
    {!page && <footer><button onClick={onClose}>Done</button></footer>}
  </Frame>;
}
