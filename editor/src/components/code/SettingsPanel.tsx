import { useState } from "react";
import { RotateCcw, X } from "lucide-react";
import {
  useSettingsStore,
  type EditorSettings,
} from "../../store/useSettingsStore";
import { useCodeStore } from "../../store/useCodeStore";

/* ------------------------------------------------------------------ */
/*  Reusable controls                                                  */
/* ------------------------------------------------------------------ */

function Toggle({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full transition-colors ${
        checked ? "bg-blue-600" : "bg-gray-300 dark:bg-gray-600"
      }`}
    >
      <span
        className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow transform transition-transform mt-0.5 ${
          checked ? "translate-x-[18px]" : "translate-x-0.5"
        }`}
      />
    </button>
  );
}

function Select<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value as T)}
      className="min-w-[140px] rounded border border-gray-300 bg-white px-2 py-1 text-sm text-gray-900 outline-none focus:border-blue-500 dark:border-[#555] dark:bg-[#3c3c3c] dark:text-white"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

function NumberInput({
  value,
  onChange,
  min,
  max,
  step = 1,
}: {
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step?: number;
}) {
  const clamp = (n: number) => Math.min(max, Math.max(min, n));

  return (
    <div className="flex items-center gap-1">
      <button
        className="flex h-6 w-6 items-center justify-center rounded border border-gray-300 bg-white text-sm text-gray-700 hover:bg-gray-100 dark:border-[#555] dark:bg-[#3c3c3c] dark:text-white dark:hover:bg-[#4c4c4c]"
        onClick={() => onChange(clamp(value - step))}
      >
        -
      </button>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => onChange(clamp(Number(e.target.value)))}
        className="w-16 rounded border border-gray-300 bg-white px-2 py-1 text-center text-sm text-gray-900 outline-none focus:border-blue-500 [appearance:textfield] dark:border-[#555] dark:bg-[#3c3c3c] dark:text-white [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
      />
      <button
        className="flex h-6 w-6 items-center justify-center rounded border border-gray-300 bg-white text-sm text-gray-700 hover:bg-gray-100 dark:border-[#555] dark:bg-[#3c3c3c] dark:text-white dark:hover:bg-[#4c4c4c]"
        onClick={() => onChange(clamp(value + step))}
      >
        +
      </button>
    </div>
  );
}

function TextInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="min-w-[200px] rounded border border-gray-300 bg-white px-2 py-1 text-sm text-gray-900 outline-none focus:border-blue-500 dark:border-[#555] dark:bg-[#3c3c3c] dark:text-white"
    />
  );
}

/* ------------------------------------------------------------------ */
/*  Setting row                                                        */
/* ------------------------------------------------------------------ */

function SettingRow({
  label,
  description,
  children,
}: {
  label: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between border-b border-gray-200 py-2.5 dark:border-[#2d2d2d]">
      <div className="flex flex-col gap-0.5 mr-4">
        <span className="text-sm text-gray-800 dark:text-gray-300">{label}</span>
        {description && (
          <span className="text-xs text-gray-500 dark:text-gray-500">{description}</span>
        )}
      </div>
      {children}
    </div>
  );
}

function SectionHeader({ title }: { title: string }) {
  return (
    <h3 className="mt-6 mb-2 text-[11px] font-semibold uppercase tracking-wider text-gray-500 first:mt-0 dark:text-gray-400">
      {title}
    </h3>
  );
}

/* ------------------------------------------------------------------ */
/*  Main panel                                                         */
/* ------------------------------------------------------------------ */

type SettingsTab = "user" | "workspace";

function UserSettings() {
  const settings = useSettingsStore();
  const update = settings.updateSetting;

  return (
    <>
      <SectionHeader title="Appearance" />

      <SettingRow label="Theme" description="Color theme for the app and editor">
        <Select<EditorSettings["theme"]>
          value={settings.theme}
          options={[
            { value: "system", label: "System" },
            { value: "vs-dark", label: "Dark (VS Dark)" },
            { value: "vs", label: "Light" },
            { value: "hc-black", label: "High Contrast Dark" },
          ]}
          onChange={(v) => update("theme", v)}
        />
      </SettingRow>

      <SettingRow label="Font Size" description="Editor font size in pixels (8–32)">
        <NumberInput
          value={settings.fontSize}
          onChange={(v) => update("fontSize", v)}
          min={8}
          max={32}
        />
      </SettingRow>

      <SettingRow label="Font Family" description="Editor font stack">
        <TextInput
          value={settings.fontFamily}
          onChange={(v) => update("fontFamily", v)}
        />
      </SettingRow>

      <SettingRow label="Cursor Style">
        <Select<EditorSettings["cursorStyle"]>
          value={settings.cursorStyle}
          options={[
            { value: "line", label: "Line" },
            { value: "line-thin", label: "Line Thin" },
            { value: "block", label: "Block" },
            { value: "block-outline", label: "Block Outline" },
            { value: "underline", label: "Underline" },
            { value: "underline-thin", label: "Underline Thin" },
          ]}
          onChange={(v) => update("cursorStyle", v)}
        />
      </SettingRow>

      <SettingRow label="Cursor Blinking">
        <Select<EditorSettings["cursorBlinking"]>
          value={settings.cursorBlinking}
          options={[
            { value: "blink", label: "Blink" },
            { value: "smooth", label: "Smooth" },
            { value: "phase", label: "Phase" },
            { value: "expand", label: "Expand" },
            { value: "solid", label: "Solid" },
          ]}
          onChange={(v) => update("cursorBlinking", v)}
        />
      </SettingRow>

      <SectionHeader title="Editor" />

      <SettingRow label="Tab Size" description="Spaces per tab (1–8)">
        <NumberInput
          value={settings.tabSize}
          onChange={(v) => update("tabSize", v)}
          min={1}
          max={8}
        />
      </SettingRow>

      <SettingRow label="Word Wrap">
        <Select<EditorSettings["wordWrap"]>
          value={settings.wordWrap}
          options={[
            { value: "off", label: "Off" },
            { value: "on", label: "On" },
            { value: "wordWrapColumn", label: "Word Wrap Column" },
            { value: "bounded", label: "Bounded" },
          ]}
          onChange={(v) => update("wordWrap", v)}
        />
      </SettingRow>

      <SettingRow label="Minimap" description="Show code minimap">
        <Toggle
          checked={settings.minimap}
          onChange={(v) => update("minimap", v)}
        />
      </SettingRow>

      <SettingRow label="Line Numbers">
        <Select<EditorSettings["lineNumbers"]>
          value={settings.lineNumbers}
          options={[
            { value: "on", label: "On" },
            { value: "off", label: "Off" },
            { value: "relative", label: "Relative" },
            { value: "interval", label: "Interval" },
          ]}
          onChange={(v) => update("lineNumbers", v)}
        />
      </SettingRow>

      <SettingRow label="Render Whitespace">
        <Select<EditorSettings["renderWhitespace"]>
          value={settings.renderWhitespace}
          options={[
            { value: "none", label: "None" },
            { value: "boundary", label: "Boundary" },
            { value: "selection", label: "Selection" },
            { value: "trailing", label: "Trailing" },
            { value: "all", label: "All" },
          ]}
          onChange={(v) => update("renderWhitespace", v)}
        />
      </SettingRow>

      <SettingRow label="Bracket Pair Colors" description="Colorize matching brackets">
        <Toggle
          checked={settings.bracketPairColorization}
          onChange={(v) => update("bracketPairColorization", v)}
        />
      </SettingRow>

      <SettingRow label="Scroll Past End" description="Allow scrolling beyond the last line">
        <Toggle
          checked={settings.scrollBeyondLastLine}
          onChange={(v) => update("scrollBeyondLastLine", v)}
        />
      </SettingRow>

      <SectionHeader title="Files" />

      <SettingRow label="Format on Save" description="Auto-format when saving a file">
        <Toggle
          checked={settings.formatOnSave}
          onChange={(v) => update("formatOnSave", v)}
        />
      </SettingRow>

      <SettingRow
        label="Auto-save Delay"
        description="Milliseconds before auto-save (0 = disabled, 500–5000)"
      >
        <NumberInput
          value={settings.autoSaveDelay}
          onChange={(v) => update("autoSaveDelay", v)}
          min={0}
          max={5000}
          step={100}
        />
      </SettingRow>
    </>
  );
}

interface WorkspaceOverrideRowProps {
  settingKey: keyof EditorSettings;
  label: string;
  rootPath: string;
  globalValue: string;
  wsValue: string | undefined;
  onClear: () => void;
}

function WorkspaceOverrideRow({ settingKey: _key, label, rootPath: _root, globalValue, wsValue, onClear }: WorkspaceOverrideRowProps) {
  const isOverridden = wsValue !== undefined;

  return (
    <div className="flex items-center justify-between border-b border-gray-200 py-2 dark:border-[#2d2d2d]">
      <div className="flex flex-col gap-0.5 mr-4">
        <span className="text-sm text-gray-800 dark:text-gray-300">{label}</span>
        <span className="text-xs text-gray-500 dark:text-gray-500">
          Global: {String(globalValue)}
          {isOverridden && <span className="text-blue-400 ml-2">Workspace: {String(wsValue)}</span>}
        </span>
      </div>
      {isOverridden && (
        <button
          onClick={onClear}
          className="flex items-center gap-1 rounded px-2 py-1 text-xs text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-800 dark:hover:bg-[#3c3c3c] dark:hover:text-gray-300"
          title="Reset to global"
        >
          <X size={12} /> Reset
        </button>
      )}
    </div>
  );
}

const WORKSPACE_OVERRIDABLE: { key: keyof EditorSettings; label: string }[] = [
  { key: "tabSize", label: "Tab Size" },
  { key: "wordWrap", label: "Word Wrap" },
  { key: "fontSize", label: "Font Size" },
  { key: "formatOnSave", label: "Format on Save" },
  { key: "autoSaveDelay", label: "Auto-save Delay" },
  { key: "minimap", label: "Minimap" },
  { key: "lineNumbers", label: "Line Numbers" },
  { key: "renderWhitespace", label: "Render Whitespace" },
];

function WorkspaceSettings() {
  const pinnedRoots = useCodeStore((s) => s.pinnedRoots);
  const settings = useSettingsStore();
  const rootPath = pinnedRoots[0];

  if (!rootPath) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-500 text-sm">
        No workspace open. Pin a root folder to configure workspace settings.
      </div>
    );
  }

  const wsOverrides = settings.workspaceOverrides[rootPath] ?? {};
  const folderName = rootPath.split("/").pop() ?? rootPath;

  return (
    <>
      <div className="flex items-center justify-between mb-4">
        <div>
          <SectionHeader title={`Workspace: ${folderName}`} />
          <p className="text-xs text-gray-500 mt-0.5">{rootPath}</p>
        </div>
        {Object.keys(wsOverrides).length > 0 && (
          <button
            onClick={() => settings.clearAllWorkspaceOverrides(rootPath)}
            className="flex items-center gap-1.5 rounded px-2.5 py-1 text-xs text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-[#3c3c3c] dark:hover:text-white"
          >
            <RotateCcw size={13} /> Clear All
          </button>
        )}
      </div>

      <p className="text-xs text-gray-500 mb-4">
        Override settings for this workspace. Changes here take priority over global (User) settings.
      </p>

      <SectionHeader title="Overridable Settings" />

      <SettingRow label="Tab Size" description="Workspace tab size override (1–8)">
        <NumberInput
          value={(wsOverrides.tabSize ?? settings.tabSize) as number}
          onChange={(v) => settings.setWorkspaceOverride(rootPath, "tabSize", v)}
          min={1}
          max={8}
        />
      </SettingRow>

      <SettingRow label="Font Size" description="Workspace font size override (8–32)">
        <NumberInput
          value={(wsOverrides.fontSize ?? settings.fontSize) as number}
          onChange={(v) => settings.setWorkspaceOverride(rootPath, "fontSize", v)}
          min={8}
          max={32}
        />
      </SettingRow>

      <SettingRow label="Word Wrap">
        <Select<EditorSettings["wordWrap"]>
          value={(wsOverrides.wordWrap ?? settings.wordWrap) as EditorSettings["wordWrap"]}
          options={[
            { value: "off", label: "Off" },
            { value: "on", label: "On" },
            { value: "wordWrapColumn", label: "Word Wrap Column" },
            { value: "bounded", label: "Bounded" },
          ]}
          onChange={(v) => settings.setWorkspaceOverride(rootPath, "wordWrap", v)}
        />
      </SettingRow>

      <SettingRow label="Minimap" description="Show code minimap">
        <Toggle
          checked={(wsOverrides.minimap ?? settings.minimap) as boolean}
          onChange={(v) => settings.setWorkspaceOverride(rootPath, "minimap", v)}
        />
      </SettingRow>

      <SettingRow label="Format on Save" description="Auto-format when saving a file">
        <Toggle
          checked={(wsOverrides.formatOnSave ?? settings.formatOnSave) as boolean}
          onChange={(v) => settings.setWorkspaceOverride(rootPath, "formatOnSave", v)}
        />
      </SettingRow>

      <SettingRow label="Auto-save Delay" description="Milliseconds before auto-save">
        <NumberInput
          value={(wsOverrides.autoSaveDelay ?? settings.autoSaveDelay) as number}
          onChange={(v) => settings.setWorkspaceOverride(rootPath, "autoSaveDelay", v)}
          min={0}
          max={5000}
          step={100}
        />
      </SettingRow>

      <SettingRow label="Line Numbers">
        <Select<EditorSettings["lineNumbers"]>
          value={(wsOverrides.lineNumbers ?? settings.lineNumbers) as EditorSettings["lineNumbers"]}
          options={[
            { value: "on", label: "On" },
            { value: "off", label: "Off" },
            { value: "relative", label: "Relative" },
            { value: "interval", label: "Interval" },
          ]}
          onChange={(v) => settings.setWorkspaceOverride(rootPath, "lineNumbers", v)}
        />
      </SettingRow>

      <SettingRow label="Render Whitespace">
        <Select<EditorSettings["renderWhitespace"]>
          value={(wsOverrides.renderWhitespace ?? settings.renderWhitespace) as EditorSettings["renderWhitespace"]}
          options={[
            { value: "none", label: "None" },
            { value: "boundary", label: "Boundary" },
            { value: "selection", label: "Selection" },
            { value: "trailing", label: "Trailing" },
            { value: "all", label: "All" },
          ]}
          onChange={(v) => settings.setWorkspaceOverride(rootPath, "renderWhitespace", v)}
        />
      </SettingRow>

      {Object.keys(wsOverrides).length > 0 && (
        <>
          <SectionHeader title="Active Overrides" />
          {WORKSPACE_OVERRIDABLE.filter((s) => s.key in wsOverrides).map((s) => (
            <WorkspaceOverrideRow
              key={s.key}
              settingKey={s.key}
              label={s.label}
              rootPath={rootPath}
              globalValue={String(settings[s.key])}
              wsValue={String(wsOverrides[s.key])}
              onClear={() => settings.clearWorkspaceOverride(rootPath, s.key)}
            />
          ))}
        </>
      )}
    </>
  );
}

export default function SettingsPanel() {
  const settings = useSettingsStore();
  const [activeTab, setActiveTab] = useState<SettingsTab>("user");

  return (
    <div className="h-full overflow-y-auto bg-white text-gray-900 dark:bg-[#1e1e1e] dark:text-white">
      <div className="sticky top-0 z-10 border-b border-gray-200 bg-white dark:border-[#2d2d2d] dark:bg-[#1e1e1e]">
        <div className="flex items-center justify-between px-6 py-3">
          <h2 className="text-base font-semibold">Settings</h2>
          {activeTab === "user" && (
            <button
              onClick={settings.resetToDefaults}
              className="flex items-center gap-1.5 rounded px-2.5 py-1 text-xs text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-[#3c3c3c] dark:hover:text-white"
              title="Reset all settings to defaults"
            >
              <RotateCcw size={13} />
              Reset Defaults
            </button>
          )}
        </div>
        <div className="flex gap-0 px-6">
          {(["user", "workspace"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-4 py-1.5 text-xs font-medium border-b-2 transition-colors ${
                activeTab === tab
                  ? "border-blue-600 text-blue-700 dark:border-white dark:text-white"
                  : "border-transparent text-gray-500 hover:text-gray-900 dark:hover:text-gray-300"
              }`}
            >
              {tab === "user" ? "User" : "Workspace"}
            </button>
          ))}
        </div>
      </div>

      <div className="px-6 pb-8">
        {activeTab === "user" ? <UserSettings /> : <WorkspaceSettings />}
      </div>
    </div>
  );
}
