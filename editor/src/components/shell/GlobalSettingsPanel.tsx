/**
 * Global settings modal for app-wide editor preferences plus a small
 * batch of `.env`-backed runtime settings that are actually wired.
 */
import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  BookOpenText,
  Check,
  Cpu,
  MessageSquare,
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
import { WORKSPACE_SURFACE_THEME_OPTIONS } from "../../lib/workspaceSurfaceTheme";
import {
  useSettingsStore,
  type EditorSettings,
} from "../../store/useSettingsStore";
import {
  useMessagingStore,
  getSessionLabel,
  type MessagingConnectionState,
  type MessagingProviderId,
} from "../../store/useMessagingStore";

export type SectionId =
  | "appearance"
  | "editor"
  | "research"
  | "runtime"
  | "messaging";
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
  { id: "messaging", label: "Messaging", icon: <MessageSquare size={14} /> },
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
  type = "text",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
}) {
  return (
    <input
      type={type}
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

function formatMessagingState(state: MessagingConnectionState): string {
  switch (state) {
    case "connected":
      return "Connected";
    case "starting":
      return "Starting";
    case "pairing":
      return "Pairing";
    case "reconnecting":
      return "Reconnecting";
    case "error":
      return "Needs attention";
    default:
      return "Disconnected";
  }
}

function MessagingStateBadge({ state }: { state: MessagingConnectionState }) {
  const toneClass =
    state === "connected"
      ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-900/20 dark:text-emerald-300"
      : state === "error"
        ? "border-red-200 bg-red-50 text-red-700 dark:border-red-900/40 dark:bg-red-900/20 dark:text-red-300"
        : state === "pairing" || state === "starting" || state === "reconnecting"
          ? "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900/40 dark:bg-amber-900/20 dark:text-amber-300"
          : "border-gray-200 bg-gray-50 text-gray-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300";
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium ${toneClass}`}>
      {formatMessagingState(state)}
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
  initialSection = "appearance",
  initialMessagingProvider = null,
}: {
  onClose: () => void;
  initialSection?: SectionId;
  initialMessagingProvider?: MessagingProviderId | null;
}) {
  const settings = useSettingsStore();
  const update = settings.updateSetting;
  const messagingProviders = useMessagingStore((state) => state.providers);
  const messagingRefreshing = useMessagingStore((state) => state.refreshing);
  const messagingRefreshError = useMessagingStore(
    (state) => state.lastRefreshError,
  );
  const initializeMessaging = useMessagingStore((state) => state.initialize);
  const refreshMessagingStatus = useMessagingStore(
    (state) => state.refreshStatus,
  );
  const refreshMessagingConfig = useMessagingStore(
    (state) => state.refreshConfig,
  );
  const connectMessagingProvider = useMessagingStore(
    (state) => state.connectProvider,
  );
  const disconnectMessagingProvider = useMessagingStore(
    (state) => state.disconnectProvider,
  );
  const reconnectMessagingProvider = useMessagingStore(
    (state) => state.reconnectProvider,
  );
  const resetMessagingProvider = useMessagingStore((state) => state.resetProvider);
  const setMessagingAutoStart = useMessagingStore(
    (state) => state.setAutoStart,
  );
  const setMessagingDraftField = useMessagingStore(
    (state) => state.setDraftField,
  );
  const clearMessagingError = useMessagingStore(
    (state) => state.clearProviderError,
  );

  const [activeSection, setActiveSection] = useState<SectionId>(initialSection);
  const [messagingAdvancedOpen, setMessagingAdvancedOpen] = useState<
    Record<MessagingProviderId, boolean>
  >({
    telegram: initialMessagingProvider === "telegram",
    whatsapp: initialMessagingProvider === "whatsapp",
    wechat: initialMessagingProvider === "wechat",
  });
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
    setActiveSection(initialSection);
  }, [initialSection]);

  useEffect(() => {
    if (!initialMessagingProvider) {
      return;
    }
    setMessagingAdvancedOpen((current) => ({
      ...current,
      [initialMessagingProvider]: true,
    }));
  }, [initialMessagingProvider]);

  useEffect(() => {
    void initializeMessaging();
  }, [initializeMessaging]);

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
      <SettingRow
        label="Desktop Surface"
        description="Factory Worn follows the app Light/Dark theme; the current worn look is its dark mode."
      >
        <Select<EditorSettings["workspaceSurfaceTheme"]>
          value={settings.workspaceSurfaceTheme}
          options={WORKSPACE_SURFACE_THEME_OPTIONS}
          onChange={(value) => update("workspaceSurfaceTheme", value)}
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

  const renderMessaging = () => {
    const renderProviderCard = (
      providerId: MessagingProviderId,
      title: string,
      description: string,
    ) => {
      const provider = messagingProviders[providerId];
      const isTelegram = providerId === "telegram";
      const isWhatsApp = providerId === "whatsapp";
      const isWeChat = providerId === "wechat";
      const isRunning =
        provider.running || provider.connectionState === "connected";
      const hasReconnectContext =
        Boolean(
          provider.running ||
            provider.configSummary?.configured ||
            provider.adapterId ||
            (isWhatsApp &&
              (provider.paired || provider.configSummary?.paired)),
        );
      const actionMode = isRunning
        ? "stop"
        : hasReconnectContext
          ? "reconnect"
          : "connect";
      const actionLabel =
        actionMode === "stop"
          ? "Stop"
          : actionMode === "reconnect"
            ? "Reconnect"
            : isWhatsApp
              ? "Pair"
              : "Connect";
      const connectDisabled =
        Boolean(provider.pendingAction) ||
        (!isRunning &&
          ((isTelegram &&
            !provider.botToken.trim() &&
            !provider.configSummary?.configured) ||
            (isWeChat &&
              !provider.wechatToken.trim() &&
              !provider.configSummary?.configured)));
      const backendNote = isTelegram
        ? provider.configEndpointAvailable === false
          ? "This backend does not expose masked config persistence yet, so bot tokens stay in memory for the current desktop session only."
          : "Masked Telegram config is persisted on the backend, so reconnecting usually does not require re-entering the bot token."
        : isWhatsApp
          ? provider.eventsEndpointAvailable === false
            ? "This backend does not expose live adapter events yet, so in-app QR pairing remains blocked for now."
            : "WhatsApp Web pairing is driven by live adapter events. If a linked-device session already exists, reconnect usually resumes without showing a fresh QR."
          : provider.configEndpointAvailable === false
            ? "This backend does not expose the WeChat adapter config routes yet, so credentials stay in memory for the current desktop session only."
            : "WeChat settings are persisted on the backend. `webhook_url` is reference-only, while `server_url` externalizes the relay path when set.";
      const dependencyInstalled =
        provider.configSummary?.dependency_installed === true
          ? true
          : provider.configSummary?.dependency_installed === false
            ? false
            : null;
      const installHint =
        typeof provider.configSummary?.install_hint === "string" &&
        provider.configSummary.install_hint.trim()
          ? provider.configSummary.install_hint.trim()
          : isTelegram
            ? "pip install 'dan[messaging]'"
            : isWhatsApp
              ? "pip install 'dan[whatsapp-web]'"
              : "pip install 'dan[wechat]'";
      const dependencyCopy =
        dependencyInstalled === true
          ? "Installed in the backend environment."
          : dependencyInstalled === false
            ? `Not installed yet. Run \`${installHint}\` in the backend environment.`
            : isTelegram
              ? `Requires \`python-telegram-bot\`. Install with \`${installHint}\`.`
              : isWhatsApp
                ? `Requires \`neonize\`. Install with \`${installHint}\`.`
                : `Requires \`cryptography\`. Install with \`${installHint}\`.`;
      const dependencyCardClass =
        dependencyInstalled === false
          ? "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900/40 dark:bg-amber-900/20 dark:text-amber-300"
          : dependencyInstalled === true
            ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-900/20 dark:text-emerald-300"
            : "border-gray-200 bg-white text-gray-600 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-300";
      const botUsername =
        typeof provider.configSummary?.bot_username === "string" &&
        provider.configSummary.bot_username.trim()
          ? provider.configSummary.bot_username.trim()
          : null;
      const maskedWeChatToken =
        typeof provider.configSummary?.masked_token === "string" &&
        provider.configSummary.masked_token.trim()
          ? provider.configSummary.masked_token.trim()
          : null;
      const wechatAppId =
        typeof provider.configSummary?.app_id === "string" &&
        provider.configSummary.app_id.trim()
          ? provider.configSummary.app_id.trim()
          : provider.wechatAppId.trim()
            ? provider.wechatAppId.trim()
            : null;
      const advancedOpen = messagingAdvancedOpen[providerId];
      const isHighlighted =
        activeSection === "messaging" && initialMessagingProvider === providerId;
      const configurationText = isTelegram
        ? provider.configSummary?.masked_token
          ? String(provider.configSummary.masked_token)
          : provider.configSummary?.configured
            ? "Configured on backend"
            : provider.botToken.trim()
              ? "Ready from this session"
              : "Not configured"
        : isWhatsApp
          ? provider.paired || provider.configSummary?.paired
            ? "Paired"
            : provider.running
              ? "Connected"
              : "Not paired"
          : maskedWeChatToken
            ? maskedWeChatToken
            : wechatAppId
              ? `App ${wechatAppId}`
              : provider.configSummary?.configured
                ? "Configured on backend"
                : provider.wechatToken.trim()
                  ? "Ready from this session"
                  : "Not configured";
      const detailText = isTelegram
        ? getSessionLabel(providerId, provider)
        : isWhatsApp
          ? provider.paired === true
            ? getSessionLabel(providerId, provider)
            : provider.connectionState === "pairing"
              ? "Waiting for QR / link"
              : provider.running
                ? getSessionLabel(providerId, provider)
                : "Pairing state unavailable"
          : provider.running
            ? getSessionLabel(providerId, provider)
            : provider.configSummary?.account_name ||
              provider.wechatAccountName.trim() ||
              provider.configSummary?.app_name ||
              provider.wechatAppName.trim() ||
              "Official Account";

      return (
        <div
          key={providerId}
          className={`rounded-xl border bg-white p-4 shadow-sm dark:bg-gray-900 ${
            isHighlighted
              ? "border-blue-400 ring-1 ring-blue-400/40 dark:border-blue-500"
              : "border-gray-200 dark:border-gray-800"
          }`}
        >
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                  {title}
                </h4>
                <MessagingStateBadge state={provider.connectionState} />
                {provider.pendingAction && (
                  <SettingsBadge tone="live">Working</SettingsBadge>
                )}
              </div>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                {description}
              </p>
            </div>
            <button
              type="button"
              disabled={connectDisabled}
              onClick={() => {
                clearMessagingError(providerId);
                if (actionMode === "stop") {
                  void disconnectMessagingProvider(providerId).catch(() => {});
                } else if (actionMode === "reconnect") {
                  void reconnectMessagingProvider(providerId).catch(() => {});
                } else {
                  void connectMessagingProvider(providerId).catch(() => {});
                }
              }}
              className="inline-flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-60"
              title={
                connectDisabled
                  ? isTelegram
                    ? "Enter a Telegram bot token first."
                    : isWeChat
                      ? "Enter a WeChat callback token first."
                      : undefined
                  : undefined
              }
            >
              {provider.pendingAction ? (
                <RefreshCw size={13} className="animate-spin" />
              ) : null}
              {actionLabel}
            </button>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-800 dark:bg-gray-950">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                Configuration
              </p>
              <p className="mt-1 text-sm font-medium text-gray-900 dark:text-gray-100">
                {configurationText}
              </p>
            </div>
            <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-800 dark:bg-gray-950">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                Detail
              </p>
              <p className="mt-1 text-sm font-medium text-gray-900 dark:text-gray-100">
                {detailText}
              </p>
            </div>
            <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-800 dark:bg-gray-950">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                Uptime
              </p>
              <p className="mt-1 text-sm font-medium text-gray-900 dark:text-gray-100">
                {provider.running && provider.uptimeSeconds != null
                  ? `${Math.round(provider.uptimeSeconds)}s`
                  : "—"}
              </p>
            </div>
          </div>

          {providerId === "telegram" && (
            <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-800 dark:bg-gray-950">
              <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                Bot token
              </p>
              <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                Used only to start the adapter from the desktop UI.{" "}
                {provider.configEndpointAvailable === false
                  ? "Until the backend config route exists, the token is kept in memory for the current app session only."
                  : "When saved successfully, the backend stores only a masked summary back to the UI so reconnects do not require re-entering it."}
              </p>
              <div className="mt-3">
                <TextInput
                  type="password"
                  value={provider.botToken}
                  placeholder="123456789:AA..."
                  onChange={(value) =>
                    setMessagingDraftField(providerId, "botToken", value)
                  }
                />
              </div>
            </div>
          )}

          {providerId === "telegram" && (
            <div className="mt-4 rounded-lg border border-gray-200 bg-white p-3 text-xs text-gray-600 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-300">
              <details>
                <summary className="cursor-pointer select-none font-medium text-gray-900 dark:text-gray-100">
                  How to get a Telegram bot token
                </summary>
                <div className="mt-3 space-y-1.5">
                  <p>1. Open Telegram and message `@BotFather`.</p>
                  <p>2. Send `/newbot` and choose a name and username.</p>
                  <p>3. Copy the token BotFather gives you and paste it above.</p>
                  <p>
                    4. If you want replies in groups, run `/setprivacy` in
                    `@BotFather` and disable privacy mode for your bot.
                  </p>
                </div>
              </details>
              {botUsername && (
                <div className="mt-3 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-blue-700 dark:border-blue-900/40 dark:bg-blue-900/20 dark:text-blue-300">
                  Verified as `@{botUsername}`. After connecting, send `/start`
                  to the bot to confirm the link.
                </div>
              )}
            </div>
          )}

          {providerId === "wechat" && (
            <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-800 dark:bg-gray-950">
              <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                Official Account credentials
              </p>
              <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                Token is required for callback verification. App ID and app
                secret are strongly recommended so delayed replies can fall back
                to customer-service sends.
              </p>
              <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                    App ID
                  </p>
                  <TextInput
                    value={provider.wechatAppId}
                    placeholder="wx1234567890"
                    onChange={(value) =>
                      setMessagingDraftField(providerId, "wechatAppId", value)
                    }
                  />
                </div>
                <div>
                  <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                    Callback token
                  </p>
                  <TextInput
                    type="password"
                    value={provider.wechatToken}
                    placeholder="wechat-token-value"
                    onChange={(value) =>
                      setMessagingDraftField(providerId, "wechatToken", value)
                    }
                  />
                </div>
                <div className="sm:col-span-2">
                  <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                    App secret
                  </p>
                  <TextInput
                    type="password"
                    value={provider.wechatAppSecret}
                    placeholder="wechat-secret-value"
                    onChange={(value) =>
                      setMessagingDraftField(providerId, "wechatAppSecret", value)
                    }
                  />
                </div>
              </div>
              {(provider.configSummary?.masked_app_secret ||
                provider.configSummary?.masked_encoding_aes_key ||
                provider.configSummary?.masked_token) && (
                <div className="mt-3 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-700 dark:border-blue-900/40 dark:bg-blue-900/20 dark:text-blue-300">
                  Saved backend secrets:
                  {provider.configSummary?.masked_token
                    ? ` token ${String(provider.configSummary.masked_token)}`
                    : ""}
                  {provider.configSummary?.masked_app_secret
                    ? `, app secret ${String(provider.configSummary.masked_app_secret)}`
                    : ""}
                  {provider.configSummary?.masked_encoding_aes_key
                    ? `, AES key ${String(provider.configSummary.masked_encoding_aes_key)}`
                    : ""}
                  .
                </div>
              )}
            </div>
          )}

          {providerId === "whatsapp" &&
            (provider.connectionState === "pairing" ||
              provider.qrSvgDataUri ||
              provider.qrData) && (
              <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-800 dark:bg-gray-950">
                <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
                  <div className="flex h-44 w-44 shrink-0 items-center justify-center rounded-lg border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-900">
                    {provider.qrSvgDataUri ? (
                      <img
                        src={provider.qrSvgDataUri}
                        alt="WhatsApp Web QR code"
                        className="h-full w-full"
                      />
                    ) : (
                      <div className="text-center text-xs text-gray-500 dark:text-gray-400">
                        Waiting for a QR code...
                      </div>
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      Link WhatsApp on your phone
                    </p>
                    <div className="mt-2 space-y-1 text-xs text-gray-600 dark:text-gray-300">
                      <p>1. Open WhatsApp on your phone.</p>
                      <p>2. Go to Linked Devices.</p>
                      <p>3. Tap Link a Device and scan this QR.</p>
                    </div>
                    {!provider.qrSvgDataUri && provider.qrData && (
                      <p className="mt-3 break-all rounded-md border border-dashed border-gray-300 bg-white px-2 py-1 font-mono text-[10px] text-gray-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-400">
                        {provider.qrData}
                      </p>
                    )}
                  </div>
                </div>
              </div>
            )}

          <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-800 dark:bg-gray-950">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                  Auto-start on launch
                </p>
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                  Best-effort reconnect after the desktop app starts. This works
                  best when backend-side config has already been saved.
                </p>
              </div>
              <Toggle
                checked={provider.autoStart}
                onChange={(value) => setMessagingAutoStart(providerId, value)}
              />
            </div>
          </div>

          <div className="mt-4">
            <button
              type="button"
              onClick={() =>
                setMessagingAdvancedOpen((current) => ({
                  ...current,
                  [providerId]: !current[providerId],
                }))
              }
              className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-200 dark:hover:bg-gray-800"
            >
              {advancedOpen ? "Hide advanced" : "Show advanced"}
            </button>
          </div>

          {advancedOpen && (
            <>
              {(providerId === "telegram" || providerId === "whatsapp") && (
                <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-800 dark:bg-gray-950">
                  <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                    {providerId === "telegram" ? "Allowed chat IDs" : "Allowed JIDs"}
                  </p>
                  <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                    {providerId === "telegram"
                      ? "Optional. Leave empty to let the bot answer any chat it can see."
                      : "Optional allowlist. Leave empty to allow any linked WhatsApp JID."}
                  </p>
                  <div className="mt-3">
                    <TextArea
                      value={
                        providerId === "telegram"
                          ? provider.allowedChatIdsText
                          : provider.allowedJidsText
                      }
                      placeholder={
                        providerId === "telegram"
                          ? "123456789\n987654321"
                          : "15551234567@s.whatsapp.net"
                      }
                      onChange={(value) =>
                        setMessagingDraftField(
                          providerId,
                          providerId === "telegram"
                            ? "allowedChatIdsText"
                            : "allowedJidsText",
                          value,
                        )
                      }
                    />
                  </div>
                </div>
              )}

              {providerId === "wechat" && (
                <>
                  <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-800 dark:bg-gray-950">
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      Callback and identity
                    </p>
                    <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <div>
                        <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                          Callback path
                        </p>
                        <TextInput
                          value={provider.wechatCallbackPath}
                          placeholder="callback"
                          onChange={(value) =>
                            setMessagingDraftField(
                              providerId,
                              "wechatCallbackPath",
                              value,
                            )
                          }
                        />
                      </div>
                      <div>
                        <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                          Public webhook URL
                        </p>
                        <TextInput
                          value={provider.wechatWebhookUrl}
                          placeholder="https://dan.example.com/api/adapters/wechat/callback"
                          onChange={(value) =>
                            setMessagingDraftField(
                              providerId,
                              "wechatWebhookUrl",
                              value,
                            )
                          }
                        />
                      </div>
                      <div>
                        <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                          Account name
                        </p>
                        <TextInput
                          value={provider.wechatAccountName}
                          placeholder="Claw Bot"
                          onChange={(value) =>
                            setMessagingDraftField(
                              providerId,
                              "wechatAccountName",
                              value,
                            )
                          }
                        />
                      </div>
                      <div>
                        <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                          App name
                        </p>
                        <TextInput
                          value={provider.wechatAppName}
                          placeholder="OpenClaw WeChat"
                          onChange={(value) =>
                            setMessagingDraftField(
                              providerId,
                              "wechatAppName",
                              value,
                            )
                          }
                        />
                      </div>
                    </div>
                  </div>

                  <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-800 dark:bg-gray-950">
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      Reply behavior
                    </p>
                    <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <div className="sm:col-span-2">
                        <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                          Welcome message
                        </p>
                        <TextArea
                          value={provider.wechatWelcomeMessage}
                          placeholder="Welcome! Send a message to start a workflow."
                          onChange={(value) =>
                            setMessagingDraftField(
                              providerId,
                              "wechatWelcomeMessage",
                              value,
                            )
                          }
                        />
                      </div>
                      <div>
                        <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                          Passive reply budget (seconds)
                        </p>
                        <TextInput
                          value={provider.wechatPassiveReplyBudgetSecondsText}
                          placeholder="4"
                          onChange={(value) =>
                            setMessagingDraftField(
                              providerId,
                              "wechatPassiveReplyBudgetSecondsText",
                              value,
                            )
                          }
                        />
                      </div>
                      <div>
                        <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                          Fallback reply
                        </p>
                        <TextInput
                          value={provider.wechatPassiveReplyFallbackText}
                          placeholder="Working on it..."
                          onChange={(value) =>
                            setMessagingDraftField(
                              providerId,
                              "wechatPassiveReplyFallbackText",
                              value,
                            )
                          }
                        />
                      </div>
                    </div>
                  </div>

                  <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-800 dark:bg-gray-950">
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      Relay and API tuning
                    </p>
                    <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <div className="sm:col-span-2">
                        <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                          Relay server URL
                        </p>
                        <TextInput
                          value={provider.wechatServerUrl}
                          placeholder="https://dan.example.com"
                          onChange={(value) =>
                            setMessagingDraftField(
                              providerId,
                              "wechatServerUrl",
                              value,
                            )
                          }
                        />
                      </div>
                      <div className="sm:col-span-2">
                        <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                          WeChat API base URL
                        </p>
                        <TextInput
                          value={provider.wechatApiBaseUrl}
                          placeholder="https://api.weixin.qq.com"
                          onChange={(value) =>
                            setMessagingDraftField(
                              providerId,
                              "wechatApiBaseUrl",
                              value,
                            )
                          }
                        />
                      </div>
                      <div>
                        <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                          Token refresh margin (seconds)
                        </p>
                        <TextInput
                          value={provider.wechatAccessTokenRefreshMarginSecondsText}
                          placeholder="300"
                          onChange={(value) =>
                            setMessagingDraftField(
                              providerId,
                              "wechatAccessTokenRefreshMarginSecondsText",
                              value,
                            )
                          }
                        />
                      </div>
                    </div>
                  </div>

                  <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-800 dark:bg-gray-950">
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                          AES-encrypted callbacks
                        </p>
                        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                          Enable encrypted handshake and callback envelopes when
                          the Official Account is configured for AES mode.
                        </p>
                      </div>
                      <Toggle
                        checked={provider.wechatSupportEncryptedCallbacks}
                        onChange={(value) =>
                          setMessagingDraftField(
                            providerId,
                            "wechatSupportEncryptedCallbacks",
                            value,
                          )
                        }
                      />
                    </div>
                    <div className="mt-3">
                      <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
                        Encoding AES key
                      </p>
                      <TextInput
                        type="password"
                        value={provider.wechatEncodingAesKey}
                        placeholder="43-char-encoding-aes-key"
                        onChange={(value) =>
                          setMessagingDraftField(
                            providerId,
                            "wechatEncodingAesKey",
                            value,
                          )
                        }
                      />
                    </div>
                  </div>
                </>
              )}

              <div
                className={`mt-4 rounded-lg border px-3 py-2 text-xs ${dependencyCardClass}`}
              >
                <p className="font-medium text-gray-900 dark:text-gray-100">
                  Dependency
                </p>
                <p className="mt-1">{dependencyCopy}</p>
              </div>

              <div className="mt-3 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs text-gray-600 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-300">
                <p className="font-medium text-gray-900 dark:text-gray-100">
                  Backend support
                </p>
                <p className="mt-1">{backendNote}</p>
              </div>

              {(providerId === "whatsapp" || providerId === "wechat") && (
                <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-3 text-xs text-red-700 dark:border-red-900/40 dark:bg-red-900/20 dark:text-red-300">
                  <p className="font-medium">
                    {providerId === "whatsapp" ? "Reset pairing" : "Reset saved config"}
                  </p>
                  <p className="mt-1">
                    {providerId === "whatsapp"
                      ? "Stop WhatsApp Web and delete the saved linked-device session so the next connect generates a fresh QR code."
                      : "Clear the saved WeChat adapter config and stop any active WeChat adapter so you can re-enter a clean deployment setup."}
                  </p>
                  <button
                    type="button"
                    disabled={Boolean(provider.pendingAction)}
                    onClick={() => {
                      clearMessagingError(providerId);
                      void resetMessagingProvider(providerId).catch(() => {});
                    }}
                    className="mt-3 rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {providerId === "whatsapp" ? "Reset pairing" : "Reset config"}
                  </button>
                </div>
              )}
            </>
          )}

          {provider.lastError && (
            <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900/40 dark:bg-red-900/20 dark:text-red-300">
              <div className="flex items-start gap-2">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                <div className="min-w-0 flex-1">
                  <p className="font-medium">Connection issue</p>
                  <p className="mt-0.5 break-words">{provider.lastError}</p>
                </div>
                <button
                  type="button"
                  onClick={() => clearMessagingError(providerId)}
                  className="rounded p-1 text-red-500 transition-colors hover:bg-red-100 hover:text-red-700 dark:hover:bg-red-900/30"
                  title="Dismiss"
                >
                  <X size={12} />
                </button>
              </div>
            </div>
          )}

          {provider.statusNote && (
            <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:border-amber-900/40 dark:bg-amber-900/20 dark:text-amber-300">
              {provider.statusNote}
            </div>
          )}
        </div>
      );
    };

    return (
      <div className="space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <SectionTitle
            title="Messaging"
            subtitle="Global remote-control surfaces for the whole desktop app. Save Telegram bot access, pair WhatsApp Web in-app, or wire a WeChat Official Account without leaving the desktop shell."
          />
          <button
            type="button"
            onClick={() => {
              void refreshMessagingStatus();
              void refreshMessagingConfig();
            }}
            className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-100 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
          >
            <RefreshCw
              size={13}
              className={messagingRefreshing ? "animate-spin" : undefined}
            />
            Refresh from server
          </button>
        </div>

        <div className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-700 dark:border-blue-900/40 dark:bg-blue-900/20 dark:text-blue-300">
          Telegram tokens are masked before they come back to the UI. WhatsApp
          Web now uses the same desktop control plane, including saved
          allowlists, richer connection state, and live QR pairing events.
          WeChat settings save through the same adapter config API, including
          callback path, passive reply tuning, and optional external relay
          server routing.
        </div>

        {messagingRefreshError && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900/40 dark:bg-red-900/20 dark:text-red-300">
            Failed to refresh adapter status: {messagingRefreshError}
          </div>
        )}

        <div className="space-y-4">
          {renderProviderCard(
            "telegram",
            "Telegram",
            "Connect a Telegram bot. Requires a bot token from @BotFather.",
          )}
          {renderProviderCard(
            "whatsapp",
            "WhatsApp Web",
            "Link your personal WhatsApp by scanning a QR code. No Business API needed.",
          )}
          {renderProviderCard(
            "wechat",
            "WeChat Official Account",
            "Configure a public Official Account callback surface with passive replies and optional async follow-up delivery.",
          )}
        </div>
      </div>
    );
  };

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
          : activeSection === "messaging"
            ? renderMessaging()
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
