/**
 * GlobalSettingsPanel: app-level settings (model, verbosity, mode defaults, appearance).
 * Distinct from Code mode's SettingsPanel (editor-specific).
 */
import { useState, useEffect } from "react";
import { Settings, Cpu, Palette, Layout } from "lucide-react";

export default function GlobalSettingsPanel({
  onClose,
}: {
  onClose: () => void;
}) {
  const [activeSection, setActiveSection] = useState("general");

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const sections = [
    { id: "general", label: "General", icon: <Settings size={14} /> },
    { id: "model", label: "AI Model", icon: <Cpu size={14} /> },
    { id: "appearance", label: "Appearance", icon: <Palette size={14} /> },
    { id: "modes", label: "Mode Defaults", icon: <Layout size={14} /> },
  ];

  return (
    <div className="fixed inset-0 z-[100] flex">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative m-auto w-[700px] max-h-[80vh] bg-white dark:bg-gray-900 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 flex overflow-hidden">
        {/* Left nav */}
        <div className="w-48 bg-gray-50 dark:bg-[#1a1a1a] border-r border-gray-200 dark:border-gray-800 py-4">
          <h2 className="px-4 text-sm font-semibold text-gray-800 dark:text-gray-200 mb-3">
            Settings
          </h2>
          {sections.map((s) => (
            <button
              key={s.id}
              onClick={() => setActiveSection(s.id)}
              className={`w-full flex items-center gap-2 px-4 py-1.5 text-xs ${
                activeSection === s.id
                  ? "text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20 border-r-2 border-blue-500"
                  : "text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
              }`}
            >
              {s.icon}
              {s.label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          {activeSection === "general" && <GeneralSettings />}
          {activeSection === "model" && <ModelSettings />}
          {activeSection === "appearance" && <AppearanceSettings />}
          {activeSection === "modes" && <ModeSettings />}
        </div>
      </div>
    </div>
  );
}

function GeneralSettings() {
  return (
    <div className="space-y-6">
      <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">
        General
      </h3>

      <SettingRow
        label="Auto-save"
        description="Save files automatically after editing"
      >
        <select className="bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded px-2 py-1 text-xs">
          <option value="afterDelay">After delay (1s)</option>
          <option value="onFocusChange">On focus change</option>
          <option value="off">Off</option>
        </select>
      </SettingRow>

      <SettingRow
        label="Confirm before delete"
        description="Ask for confirmation before deleting files or conversations"
      >
        <input type="checkbox" defaultChecked className="rounded" />
      </SettingRow>

      <SettingRow
        label="Telemetry"
        description="Send anonymous usage data"
      >
        <input type="checkbox" className="rounded" />
      </SettingRow>
    </div>
  );
}

function ModelSettings() {
  return (
    <div className="space-y-6">
      <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">
        AI Model Configuration
      </h3>

      <SettingRow
        label="Default model"
        description="Model used for chat and inline AI features"
      >
        <select className="bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded px-2 py-1 text-xs">
          <option>Auto (recommended)</option>
          <option>Claude 4 Opus</option>
          <option>Claude 4 Sonnet</option>
          <option>GPT-4o</option>
        </select>
      </SettingRow>

      <SettingRow
        label="Verbosity"
        description="How detailed should AI responses be"
      >
        <select className="bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded px-2 py-1 text-xs">
          <option>Concise</option>
          <option value="normal">Normal</option>
          <option>Detailed</option>
        </select>
      </SettingRow>
    </div>
  );
}

function AppearanceSettings() {
  return (
    <div className="space-y-6">
      <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">
        Appearance
      </h3>

      <SettingRow
        label="Theme"
        description="Color theme for the application"
      >
        <select className="bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded px-2 py-1 text-xs">
          <option value="dark">Dark</option>
          <option value="light">Light</option>
          <option value="system">System</option>
        </select>
      </SettingRow>
    </div>
  );
}

function ModeSettings() {
  return (
    <div className="space-y-6">
      <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">
        Mode Defaults
      </h3>
      <p className="text-xs text-gray-500">
        Default mode when opening a new workspace
      </p>

      <SettingRow
        label="Default mode"
        description="Which mode to activate for new workspaces"
      >
        <select className="bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded px-2 py-1 text-xs">
          <option value="chat">Chat</option>
          <option value="development">Code</option>
          <option value="operations">Operations</option>
        </select>
      </SettingRow>
    </div>
  );
}

function SettingRow({
  label,
  description,
  children,
}: {
  label: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <p className="text-sm text-gray-800 dark:text-gray-200">{label}</p>
        <p className="text-[11px] text-gray-500 mt-0.5">{description}</p>
      </div>
      {children}
    </div>
  );
}
