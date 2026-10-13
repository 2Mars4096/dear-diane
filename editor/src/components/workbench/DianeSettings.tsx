import { NotificationSetting } from "./WorkNotifications";
import { lazy, Suspense, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { useSettingsStore } from "../../store/useSettingsStore";
import { NativeWorkerSettings, type WorkerProfiles } from "./NativeWorkers";
import { AppearanceChoices } from "./AppearanceChoices";
import "./settings.css";

const DesktopUpdates = lazy(() => import("./DesktopUpdates").then(module => ({ default: module.DesktopUpdates })));
const TokenUsage = lazy(() => import("./TokenUsage").then(module => ({ default: module.TokenUsage })));
const ApiProviders = lazy(() => import("./ApiProviders").then(module => ({ default: module.ApiProviders })));
const SkillPool = lazy(() => import("./SkillPool").then(module => ({ default: module.SkillPool })));
const RemoteConnections = lazy(() => import("./RemoteConnections").then(module => ({ default: module.RemoteConnections })));

function Preference({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return <label className="wb-preference-row"><span className="wb-preference-copy"><span>{title}</span>{description && <small>{description}</small>}</span>{children}</label>;
}

export function DianeSettings({ onClose, profiles, onProfilesChange, onPapers, page = false }: {
  onPapers?: () => void;
  onClose: () => void; profiles: WorkerProfiles; onProfilesChange: (profiles: WorkerProfiles) => void; page?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const id = useId();
  const settings = useSettingsStore();
  const [selected, setActive] = useState("appearance");
  const active = selected === "library" && !onPapers ? "appearance" : selected;
  const [visited, setVisited] = useState(() => new Set(["appearance"]));
  useEffect(() => {
    if (page) return;
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => previous?.focus();
  }, [page]);
  const groups = [
    { title: "Preferences", sections: [["appearance", "Appearance"], ["notifications", "Notifications"]] },
    { title: "Agent setup", sections: [["providers", "Providers"], ["agents", "Agents"], ["skills", "Skills"]] },
    { title: "Workspace", sections: [["connections", "SSH connections"], ...(onPapers ? [["library", "Library"]] : []), ["usage", "Usage"]] },
    { title: "", sections: [["app", "About & updates"]] },
  ];
  const sections = groups.flatMap(group => group.sections);
  const select = (section: string) => {
    setVisited(previous => new Set([...previous, section]));
    setActive(section);
    if (content.current) content.current.scrollTop = 0;
  };
  const body = (section: string) => {
    switch (section) {
      case "appearance": return <>
        <Preference title="Theme" description="Use a light or dark appearance, or follow your device."><select aria-label="Theme" value={settings.theme} onChange={event => settings.updateSetting("theme", event.target.value as typeof settings.theme)}>
          <option value="system">System</option><option value="vs">Light</option><option value="vs-dark">Dark</option>
          {settings.theme === "hc-black" && <option value="hc-black">High contrast (legacy)</option>}
        </select></Preference>
        <AppearanceChoices />
        <h3>Notes</h3>
        <Preference title="Surface"><select aria-label="Notes surface" value={settings.workspaceSurfaceTheme} onChange={event => settings.updateSetting("workspaceSurfaceTheme", event.target.value as typeof settings.workspaceSurfaceTheme)}><option value="original">Original</option><option value="industrial">Industrial</option><option value="factory-worn">Factory worn</option></select></Preference>
        <Preference title="Color mode" description="Set the appearance of your Notes workspace."><select aria-label="Notes color mode" value={settings.workspaceSurfaceTone} onChange={event => settings.updateSetting("workspaceSurfaceTone", event.target.value as typeof settings.workspaceSurfaceTone)}><option value="system">System</option><option value="day">Day</option><option value="night">Night</option></select></Preference>
      </>;
      case "notifications": return <NotificationSetting />;
      case "providers": return <ApiProviders />;
      case "agents": return <><p className="wb-category-description">Choose which agents can join your team. Changes apply to the next run.</p><NativeWorkerSettings inline profiles={profiles} onChange={onProfilesChange} /></>;
      case "skills": return <SkillPool />;
      case "connections": return <RemoteConnections />;
      case "library": return <div className="wb-preference-row"><span className="wb-preference-copy"><span>Paper sources</span><small>Manage folders and the sidebar entry in your paper library.</small></span><button type="button" onClick={onPapers}>Open library</button></div>;
      case "usage": return <TokenUsage />;
      case "app": return <DesktopUpdates />;
    }
  };
  // Retain visited panels so navigating away cannot discard an unsaved credential or form.
  const Frame = (page ? "section" : "dialog") as "dialog";
  return <Frame ref={dialog} className={`wb-dan-settings wb-settings-layout ${page ? "wb-settings-page" : "wb-project-settings"}`} onCancel={onClose} aria-labelledby={`${id}-title`} onClick={event => {
    if (page || event.target !== event.currentTarget) return;
    const box = event.currentTarget.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) onClose();
  }}>
    <header className="wb-settings-header"><h2 id={`${id}-title`}>Settings</h2>{!page && <button type="button" aria-label="Close settings" onClick={onClose}><X size={18} /></button>}</header>
    <div className="wb-settings-body">
      <nav className="wb-settings-nav" aria-label="Settings categories">{groups.map(group => <div className="wb-settings-nav-group" key={group.title}>
        {group.title && <span className="wb-settings-nav-label">{group.title}</span>}
        {group.sections.map(([section, label]) => <button type="button" key={section} aria-current={active === section ? "page" : undefined} aria-controls={`${id}-${section}`} onClick={() => select(section)}>{label}</button>)}
      </div>)}</nav>
      <div className="wb-settings-content" ref={content}>{sections.map(([section, label]) => <section key={section} id={`${id}-${section}`} hidden={active !== section} aria-labelledby={`${id}-${section}-title`} className="wb-settings-category">
        <h2 id={`${id}-${section}-title`}>{label}</h2>
        {visited.has(section) && <Suspense fallback={<p role="status">Loading {label.toLowerCase()}…</p>}>{body(section)}</Suspense>}
      </section>)}</div>
    </div>
  </Frame>;
}
