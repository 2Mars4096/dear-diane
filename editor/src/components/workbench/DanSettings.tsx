import { useEffect, useRef } from "react";
import { X } from "lucide-react";
import { useSettingsStore } from "../../store/useSettingsStore";
import { NativeWorkerSettings, type WorkerProfiles } from "./NativeWorkers";

export function DanSettings({ onClose, profiles, onProfilesChange }: {
  onClose: () => void; profiles: WorkerProfiles; onProfilesChange: (profiles: WorkerProfiles) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const settings = useSettingsStore();
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => previous?.focus();
  }, []);
  return <dialog ref={dialog} className="wb-project-settings wb-dan-settings" onCancel={onClose} aria-labelledby="dan-settings-title">
    <header><h2 id="dan-settings-title">DAN settings</h2><button aria-label="Close DAN settings" onClick={onClose}><X size={18} /></button></header>
    <p>Preferences for this DAN app profile. Changes save automatically.</p>
    <h3>Notes appearance</h3>
    <label>Surface<select value={settings.workspaceSurfaceTheme} onChange={(event) => settings.updateSetting("workspaceSurfaceTheme", event.target.value as typeof settings.workspaceSurfaceTheme)}><option value="original">Original</option><option value="industrial">Industrial</option><option value="factory-worn">Factory worn</option></select></label>
    <label>Color mode<select value={settings.workspaceSurfaceTone} onChange={(event) => settings.updateSetting("workspaceSurfaceTone", event.target.value as typeof settings.workspaceSurfaceTone)}><option value="system">System</option><option value="day">Day</option><option value="night">Night</option></select></label>
    <NativeWorkerSettings profiles={profiles} onChange={onProfilesChange} />
    <footer><button onClick={onClose}>Done</button></footer>
  </dialog>;
}
