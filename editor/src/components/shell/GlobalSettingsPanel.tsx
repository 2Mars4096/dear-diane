/**
 * Global settings modal for app-wide editor preferences plus a small
 * batch of `.env`-backed runtime settings that are actually wired.
 */
import { useEffect, useMemo, useState } from "react";
import {
  BookOpenText,
  Check,
  Cpu,
  Palette,
  RefreshCw,
  Settings2,
  Sparkles,
  X,
} from "lucide-react";
import {
  getRuntimeSettings,
  setRuntimeSetting,
  type RuntimeConfigKey,
  type RuntimeSettingsMap,
} from "../../lib/api";
import {
  useSettingsStore,
  type EditorSettings,
} from "../../store/useSettingsStore";

type SectionId = "appearance" | "editor" | "research" | "runtime";
type RuntimeFieldKind = "text" | "toggle";

const DEFAULT_RUNTIME_VALUES: RuntimeSettingsMap = {
  DAN_CHAT_MODEL: "",
  DAN_LLM_MODEL: "",
  DAN_LLM_BASE_URL: "",
  DAN_BOT_NAME: "DAN",
  DAN_ENABLE_TIER_POLICY: "0",
  DAN_FULL_TOOLS: "1",
  DAN_TELEMETRY: "1",
  DAN_LEARNING_MODE: "0",
};

const SECTIONS: Array<{
  id: SectionId;
  label: string;
  icon: React.ReactNode;
}> = [
  { id: "appearance", label: "Appearance", icon: <Palette size={14} /> },
  { id: "editor", label: "Editor", icon: <Settings2 size={14} /> },
  { id: "research", label: "Research", icon: <BookOpenText size={14} /> },
  { id: "runtime", label: "Runtime", icon: <Cpu size={14} /> },
];

const RUNTIME_FIELDS: Array<{
  key: RuntimeConfigKey;
  label: string;
  description: string;
  kind: RuntimeFieldKind;
}> = [
  {
    key: "DAN_CHAT_MODEL",
    label: "Chat Model",
    description: "Primary model for chat responses. Applies immediately to new chat turns.",
    kind: "text",
  },
  {
    key: "DAN_LLM_MODEL",
    label: "Default Engine Model",
    description: "Fallback default for engine/runtime calls and chat model fallback.",
    kind: "text",
  },
  {
    key: "DAN_BOT_NAME",
    label: "Assistant Name",
    description: "Name used in reply prefixes such as [DAN - Project]. Applies immediately.",
    kind: "text",
  },
  {
    key: "DAN_LLM_BASE_URL",
    label: "LLM Base URL",
    description: "OpenAI-compatible base URL. Saved to .env and used after restart.",
    kind: "text",
  },
  {
    key: "DAN_ENABLE_TIER_POLICY",
    label: "Tier Policy",
    description: "Auto-route work to cheaper/faster or stronger models by task difficulty.",
    kind: "toggle",
  },
  {
    key: "DAN_FULL_TOOLS",
    label: "Full Tool Suite",
    description: "Expose the larger chat toolset beyond the minimal default set.",
    kind: "toggle",
  },
  {
    key: "DAN_TELEMETRY",
    label: "Telemetry",
    description: "Enable unified telemetry/event tracking on the backend.",
    kind: "toggle",
  },
  {
    key: "DAN_LEARNING_MODE",
    label: "Learning Mode",
    description: "Turn on safe advisory learning features from the quick-start .env profile.",
    kind: "toggle",
  },
];

function splitLines(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function Toggle({
  checked,
  onChange,
  disabled = false,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => !disabled && onChange(!checked)}
      className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors ${
        checked ? "bg-blue-600" : "bg-gray-300 dark:bg-gray-600"
      } ${disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer"}`}
    >
      <span
        className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow transition-transform mt-0.5 ${
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
      className="min-w-[180px] rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 outline-none focus:border-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-white"
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

function NumberInput({
  value,
  min,
  max,
  step = 1,
  onChange,
}: {
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (v: number) => void;
}) {
  const clamp = (n: number) => Math.min(max, Math.max(min, n));
  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={() => onChange(clamp(value - step))}
        className="flex h-8 w-8 items-center justify-center rounded-md border border-gray-300 bg-white text-gray-700 transition-colors hover:bg-gray-100 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
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
        className="w-20 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-center text-sm text-gray-900 outline-none focus:border-blue-500 [appearance:textfield] dark:border-gray-700 dark:bg-gray-800 dark:text-white [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
      />
      <button
        type="button"
        onClick={() => onChange(clamp(value + step))}
        className="flex h-8 w-8 items-center justify-center rounded-md border border-gray-300 bg-white text-gray-700 transition-colors hover:bg-gray-100 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
      >
        +
      </button>
    </div>
  );
}

function TextInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <input
      type="text"
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      className="min-w-[260px] rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 outline-none focus:border-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-white dark:placeholder:text-gray-500"
    />
  );
}

function TextArea({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <textarea
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      className="min-h-[96px] w-[320px] rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 outline-none focus:border-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-white dark:placeholder:text-gray-500"
    />
  );
}

function SettingsBadge({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: "neutral" | "live" | "restart";
}) {
  const toneClass =
    tone === "live"
      ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-900/20 dark:text-emerald-300"
      : tone === "restart"
        ? "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900/40 dark:bg-amber-900/20 dark:text-amber-300"
        : "border-gray-200 bg-gray-50 text-gray-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300";
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium ${toneClass}`}>
      {children}
    </span>
  );
}

function SettingRow({
  label,
  description,
  badge,
  children,
}: {
  label: string;
  description: string;
  badge?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-6 border-b border-gray-200 py-4 dark:border-gray-800">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
            {label}
          </p>
          {badge}
        </div>
        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
          {description}
        </p>
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function SectionTitle({
  title,
  subtitle,
}: {
  title: string;
  subtitle: string;
}) {
  return (
    <div className="mb-4">
      <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
        {title}
      </h3>
      <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
        {subtitle}
      </p>
    </div>
  );
}

export default function GlobalSettingsPanel({
  onClose,
}: {
  onClose: () => void;
}) {
  const settings = useSettingsStore();
  const update = settings.updateSetting;

  const [activeSection, setActiveSection] = useState<SectionId>("appearance");
  const [pdfRootsText, setPdfRootsText] = useState(() =>
    settings.researchPdfRoots.join("\n"),
  );
  const [noteRootsText, setNoteRootsText] = useState(() =>
    settings.researchNoteRoots.join("\n"),
  );
  const [runtimeValues, setRuntimeValues] =
    useState<RuntimeSettingsMap>(DEFAULT_RUNTIME_VALUES);
  const [restartRequiredKeys, setRestartRequiredKeys] = useState<
    RuntimeConfigKey[]
  >([]);
  const [runtimeLoading, setRuntimeLoading] = useState(true);
  const [runtimeSavingKey, setRuntimeSavingKey] =
    useState<RuntimeConfigKey | null>(null);
  const [runtimeMessage, setRuntimeMessage] = useState<string | null>(null);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);

  const restartRequired = useMemo(
    () => new Set(restartRequiredKeys),
    [restartRequiredKeys],
  );

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  useEffect(() => {
    setPdfRootsText(settings.researchPdfRoots.join("\n"));
  }, [settings.researchPdfRoots]);

  useEffect(() => {
    setNoteRootsText(settings.researchNoteRoots.join("\n"));
  }, [settings.researchNoteRoots]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setRuntimeLoading(true);
      setRuntimeError(null);
      try {
        const data = await getRuntimeSettings();
        if (cancelled) return;
        setRuntimeValues(data.values);
        setRestartRequiredKeys(data.restart_required_keys);
      } catch (err) {
        if (cancelled) return;
        setRuntimeError(
          err instanceof Error ? err.message : "Failed to load runtime settings.",
        );
      } finally {
        if (!cancelled) setRuntimeLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const saveRuntime = async (key: RuntimeConfigKey, value: string) => {
    setRuntimeSavingKey(key);
    setRuntimeError(null);
    setRuntimeMessage(null);
    try {
      const result = await setRuntimeSetting(key, value);
      setRuntimeValues((prev) => ({ ...prev, [key]: result.value }));
      setRuntimeMessage(
        result.restart_required
          ? `${key} saved to .env. Restart DAN to apply it everywhere.`
          : `${key} updated immediately and saved to .env.`,
      );
    } catch (err) {
      setRuntimeError(
        err instanceof Error ? err.message : `Failed to save ${key}.`,
      );
    } finally {
      setRuntimeSavingKey(null);
    }
  };

  const renderAppearance = () => (
    <div className="space-y-2">
      <SectionTitle
        title="Appearance"
        subtitle="Theme changes apply immediately. Light mode now also remaps the older dark-only shell panels so the app feels noticeably lighter instead of only changing Monaco."
      />
      <SettingRow
        label="Theme"
        description="Color theme for the shell and editor."
      >
        <Select<EditorSettings["theme"]>
          value={settings.theme}
          options={[
            { value: "system", label: "System" },
            { value: "vs-dark", label: "Dark" },
            { value: "vs", label: "Light" },
            { value: "hc-black", label: "High Contrast Dark" },
          ]}
          onChange={(value) => update("theme", value)}
        />
      </SettingRow>
    </div>
  );

  const renderEditor = () => (
    <div className="space-y-2">
      <SectionTitle
        title="Editor"
        subtitle="App-wide editor defaults. Workspace-specific overrides in Development mode still take priority when set."
      />
      <SettingRow
        label="Font Size"
        description="Default editor font size in pixels."
      >
        <NumberInput
          value={settings.fontSize}
          min={8}
          max={32}
          onChange={(value) => update("fontSize", value)}
        />
      </SettingRow>
      <SettingRow
        label="Tab Size"
        description="Spaces per tab in Monaco editors."
      >
        <NumberInput
          value={settings.tabSize}
          min={1}
          max={8}
          onChange={(value) => update("tabSize", value)}
        />
      </SettingRow>
      <SettingRow
        label="Word Wrap"
        description="Default wrapping behavior for code and markdown editors."
      >
        <Select<EditorSettings["wordWrap"]>
          value={settings.wordWrap}
          options={[
            { value: "off", label: "Off" },
            { value: "on", label: "On" },
            { value: "wordWrapColumn", label: "At column" },
            { value: "bounded", label: "Bounded" },
          ]}
          onChange={(value) => update("wordWrap", value)}
        />
      </SettingRow>
      <SettingRow
        label="Format On Save"
        description="Automatically format files on save when a formatter is available."
      >
        <Toggle
          checked={settings.formatOnSave}
          onChange={(value) => update("formatOnSave", value)}
        />
      </SettingRow>
      <SettingRow
        label="Auto-save Delay"
        description="Milliseconds before auto-save triggers. Set to 0 to disable."
      >
        <NumberInput
          value={settings.autoSaveDelay}
          min={0}
          max={5000}
          step={100}
          onChange={(value) => update("autoSaveDelay", value)}
        />
      </SettingRow>
      <SettingRow
        label="Inline Completions"
        description="Show AI inline completion suggestions in the editor."
      >
        <Toggle
          checked={settings.inlineCompletionEnabled}
          onChange={(value) => update("inlineCompletionEnabled", value)}
        />
      </SettingRow>
      <SettingRow
        label="AI Code Actions"
        description="Enable contextual AI actions like explain, tests, docs, and refactor."
      >
        <Toggle
          checked={settings.aiActionsEnabled}
          onChange={(value) => update("aiActionsEnabled", value)}
        />
      </SettingRow>
    </div>
  );

  const renderResearch = () => (
    <div className="space-y-2">
      <SectionTitle
        title="Research"
        subtitle="Default paper and note roots for Research mode. Enter one path per line."
      />
      <SettingRow
        label="PDF Roots"
        description="Folders DAN should scan for papers by default."
      >
        <TextArea
          value={pdfRootsText}
          placeholder={"/Users/me/Papers\n/Volumes/ResearchCorpus"}
          onChange={(value) => {
            setPdfRootsText(value);
            update("researchPdfRoots", splitLines(value));
          }}
        />
      </SettingRow>
      <SettingRow
        label="Note Roots"
        description="Folders DAN should scan for markdown or note files by default."
      >
        <TextArea
          value={noteRootsText}
          placeholder={"/Users/me/Notes\n/Users/me/Research"}
          onChange={(value) => {
            setNoteRootsText(value);
            update("researchNoteRoots", splitLines(value));
          }}
        />
      </SettingRow>
    </div>
  );

  const renderRuntime = () => (
    <div className="space-y-2">
      <SectionTitle
        title="Runtime"
        subtitle="These values are linked to the backend server and persisted to the project `.env`. Fields marked `Restart` save now but need a restart to apply everywhere."
      />

      <div className="mb-4 flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-700 dark:border-blue-900/40 dark:bg-blue-900/20 dark:text-blue-300">
        <Sparkles size={14} />
        <span>
          This is the first wired batch of `.env` settings: model, identity, and a few high-value runtime toggles.
        </span>
      </div>

      {(runtimeMessage || runtimeError) && (
        <div
          className={`mb-4 rounded-lg border px-3 py-2 text-xs ${
            runtimeError
              ? "border-red-200 bg-red-50 text-red-700 dark:border-red-900/40 dark:bg-red-900/20 dark:text-red-300"
              : "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-900/20 dark:text-emerald-300"
          }`}
        >
          {runtimeError ?? runtimeMessage}
        </div>
      )}

      <div className="mb-4 flex items-center justify-end">
        <button
          type="button"
          onClick={() => {
            setRuntimeError(null);
            setRuntimeMessage(null);
            setRuntimeLoading(true);
            void getRuntimeSettings()
              .then((data) => {
                setRuntimeValues(data.values);
                setRestartRequiredKeys(data.restart_required_keys);
              })
              .catch((err) => {
                setRuntimeError(
                  err instanceof Error
                    ? err.message
                    : "Failed to refresh runtime settings.",
                );
              })
              .finally(() => setRuntimeLoading(false));
          }}
          className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-100 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
        >
          <RefreshCw size={13} />
          Refresh from server
        </button>
      </div>

      {RUNTIME_FIELDS.map((field) => {
        const saving = runtimeSavingKey === field.key;
        const isRestartRequired = restartRequired.has(field.key);
        const badge = isRestartRequired ? (
          <SettingsBadge tone="restart">Restart</SettingsBadge>
        ) : (
          <SettingsBadge tone="live">Live</SettingsBadge>
        );

        return (
          <SettingRow
            key={field.key}
            label={field.label}
            description={field.description}
            badge={badge}
          >
            {field.kind === "toggle" ? (
              <Toggle
                checked={runtimeValues[field.key] === "1"}
                disabled={saving || runtimeLoading}
                onChange={(checked) => {
                  const next = checked ? "1" : "0";
                  setRuntimeValues((prev) => ({ ...prev, [field.key]: next }));
                  void saveRuntime(field.key, next);
                }}
              />
            ) : (
              <div className="flex items-center gap-2">
                <TextInput
                  value={runtimeValues[field.key]}
                  onChange={(value) =>
                    setRuntimeValues((prev) => ({
                      ...prev,
                      [field.key]: value,
                    }))
                  }
                />
                <button
                  type="button"
                  disabled={saving || runtimeLoading}
                  onClick={() => void saveRuntime(field.key, runtimeValues[field.key])}
                  className="inline-flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {saving ? <RefreshCw size={13} className="animate-spin" /> : <Check size={13} />}
                  Save
                </button>
              </div>
            )}
          </SettingRow>
        );
      })}

      {runtimeLoading && (
        <p className="pt-2 text-xs text-gray-500 dark:text-gray-400">
          Loading runtime settings...
        </p>
      )}
    </div>
  );

  const content =
    activeSection === "appearance"
      ? renderAppearance()
      : activeSection === "editor"
        ? renderEditor()
        : activeSection === "research"
          ? renderResearch()
          : renderRuntime();

  return (
    <div className="fixed inset-0 z-[100] flex">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative m-auto flex max-h-[85vh] w-[860px] overflow-hidden rounded-xl border border-gray-200 bg-white shadow-2xl dark:border-gray-800 dark:bg-gray-950">
        <div className="w-52 border-r border-gray-200 bg-gray-50 py-4 dark:border-gray-800 dark:bg-gray-900">
          <div className="mb-3 flex items-center justify-between px-4">
            <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              Settings
            </h2>
            <button
              type="button"
              onClick={onClose}
              className="rounded p-1 text-gray-500 transition-colors hover:bg-gray-200 hover:text-gray-800 dark:hover:bg-gray-800 dark:hover:text-gray-200"
              title="Close settings"
            >
              <X size={14} />
            </button>
          </div>
          <div className="space-y-1 px-2">
            {SECTIONS.map((section) => (
              <button
                key={section.id}
                type="button"
                onClick={() => setActiveSection(section.id)}
                className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-xs font-medium transition-colors ${
                  activeSection === section.id
                    ? "bg-blue-50 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300"
                    : "text-gray-600 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-100"
                }`}
              >
                {section.icon}
                <span>{section.label}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-6">{content}</div>
      </div>
    </div>
  );
}
