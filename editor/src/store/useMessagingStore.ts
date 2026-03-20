import { create } from "zustand";
import { persist } from "zustand/middleware";
import {
  connectAdapterEvents,
  getAdapterConfig,
  getAdapterStatus,
  resetAdapterConfig,
  saveAdapterConfig,
  startAdapter,
  stopAdapter,
  type AdapterConfigSummary,
  type AdapterInfo,
} from "../lib/api";

export type MessagingProviderId = "telegram" | "whatsapp";
export type MessagingConnectionState =
  | "disconnected"
  | "starting"
  | "pairing"
  | "connected"
  | "reconnecting"
  | "error"
  | "unknown";

type PendingAction =
  | "connect"
  | "disconnect"
  | "reconnect"
  | "refresh"
  | "reset"
  | null;
type DraftField = "botToken" | "allowedChatIdsText" | "allowedJidsText";
type MessagingStateUpdater = (
  state: MessagingState,
) => MessagingState | Partial<MessagingState>;

const PROVIDER_IDS = ["telegram", "whatsapp"] as const;
const POLL_INTERVAL_MS = 10_000;
const STALE_ERROR_RECOVERY_DELAY_MS = 1_500;

const CONNECTION_STATE_PRIORITY: Record<MessagingConnectionState, number> = {
  error: 5,
  unknown: 4,
  pairing: 4,
  reconnecting: 3,
  starting: 2,
  connected: 1,
  disconnected: 0,
};

let pollTimer: number | null = null;
let bootPromise: Promise<void> | null = null;
let staleErrorRecoveryTimer: number | null = null;
let staleErrorRecoveryArmed = false;
const eventSources = new Map<
  MessagingProviderId,
  { adapterId: string; source: EventSource }
>();

export interface MessagingProviderState {
  enabled: boolean;
  autoStart: boolean;
  adapterId: string | null;
  adapterIds: string[];
  reportedTypes: string[];
  running: boolean;
  sessionCount: number;
  uptimeSeconds: number | null;
  connectionState: MessagingConnectionState;
  lastError: string | null;
  paired: boolean | null;
  configSummary: AdapterConfigSummary | null;
  configEndpointAvailable: boolean | null;
  eventsEndpointAvailable: boolean | null;
  pendingAction: PendingAction;
  statusNote: string | null;
  qrData: string | null;
  qrSvgDataUri: string | null;
  botToken: string;
  allowedChatIdsText: string;
  allowedJidsText: string;
}

export interface MessagingSummary {
  activeCount: number;
  errorCount: number;
  tone: "idle" | "active" | "warning" | "error";
  label: string;
  tooltip: string;
}

interface MessagingState {
  providers: Record<MessagingProviderId, MessagingProviderState>;
  initialized: boolean;
  refreshing: boolean;
  lastRefreshError: string | null;
  initialize: () => Promise<void>;
  teardown: () => void;
  refreshStatus: (options?: { silent?: boolean }) => Promise<void>;
  refreshConfig: (providerId?: MessagingProviderId) => Promise<void>;
  connectProvider: (providerId: MessagingProviderId) => Promise<void>;
  disconnectProvider: (providerId: MessagingProviderId) => Promise<void>;
  reconnectProvider: (providerId: MessagingProviderId) => Promise<void>;
  resetProvider: (providerId: MessagingProviderId) => Promise<void>;
  setAutoStart: (providerId: MessagingProviderId, value: boolean) => void;
  setDraftField: (
    providerId: MessagingProviderId,
    field: DraftField,
    value: string,
  ) => void;
  clearProviderError: (providerId: MessagingProviderId) => void;
}

export function mergePersistedMessagingProviders(
  persisted: Partial<Record<MessagingProviderId, Partial<MessagingProviderState>>> | undefined,
  current: Record<MessagingProviderId, MessagingProviderState>,
): Record<MessagingProviderId, MessagingProviderState> {
  return {
    telegram: {
      ...current.telegram,
      enabled:
        typeof persisted?.telegram?.enabled === "boolean"
          ? persisted.telegram.enabled
          : current.telegram.enabled,
      autoStart:
        typeof persisted?.telegram?.autoStart === "boolean"
          ? persisted.telegram.autoStart
          : current.telegram.autoStart,
    },
    whatsapp: {
      ...current.whatsapp,
      enabled:
        typeof persisted?.whatsapp?.enabled === "boolean"
          ? persisted.whatsapp.enabled
          : current.whatsapp.enabled,
      autoStart:
        typeof persisted?.whatsapp?.autoStart === "boolean"
          ? persisted.whatsapp.autoStart
          : current.whatsapp.autoStart,
    },
  };
}

export function hasResolvedMessagingConfig(
  providers: Record<MessagingProviderId, MessagingProviderState>,
): boolean {
  return PROVIDER_IDS.every((providerId) => {
    const provider = providers[providerId];
    return provider.configSummary != null || provider.configEndpointAvailable === false;
  });
}

function createProviderState(): MessagingProviderState {
  return {
    enabled: false,
    autoStart: false,
    adapterId: null,
    adapterIds: [],
    reportedTypes: [],
    running: false,
    sessionCount: 0,
    uptimeSeconds: null,
    connectionState: "disconnected",
    lastError: null,
    paired: null,
    configSummary: null,
    configEndpointAvailable: null,
    eventsEndpointAvailable: null,
    pendingAction: null,
    statusNote: null,
    qrData: null,
    qrSvgDataUri: null,
    botToken: "",
    allowedChatIdsText: "",
    allowedJidsText: "",
  };
}

function uniqueStrings(values: Array<string | null | undefined>): string[] {
  return Array.from(
    new Set(
      values
        .map((value) => String(value ?? "").trim())
        .filter(Boolean),
    ),
  );
}

function parseLineList(value: string): string[] {
  return uniqueStrings(
    value
      .split(/\r?\n|,/)
      .map((part) => part.trim()),
  );
}

function parseIntegerList(value: string): number[] {
  return parseLineList(value)
    .map((part) => Number.parseInt(part, 10))
    .filter((part) => Number.isFinite(part));
}

function formatErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) {
    return error.message.trim();
  }
  if (typeof error === "string" && error.trim()) {
    return error.trim();
  }
  return "Unknown messaging error.";
}

function isMissingEndpointError(error: unknown): boolean {
  const message = formatErrorMessage(error).toLowerCase();
  return (
    message.includes("404:") ||
    message.includes("404 not found") ||
    message.endsWith("not found") ||
    message.includes('"detail":"not found"')
  );
}

function getProviderAdapterType(providerId: MessagingProviderId): string {
  return providerId === "whatsapp" ? "whatsapp-web" : "telegram";
}

export function normalizeMessagingProvider(
  adapterType: string | null | undefined,
): MessagingProviderId | null {
  const normalized = String(adapterType ?? "").trim().toLowerCase();
  if (!normalized) return null;
  if (normalized.includes("telegram")) return "telegram";
  if (normalized.includes("whatsapp")) return "whatsapp";
  return null;
}

export function normalizeMessagingConnectionState(
  value: unknown,
  running: boolean,
): MessagingConnectionState {
  const normalized = String(value ?? "").trim().toLowerCase();
  switch (normalized) {
    case "connected":
    case "pairing":
    case "reconnecting":
    case "starting":
    case "disconnected":
    case "error":
    case "unknown":
      return normalized;
    case "running":
      return "connected";
    case "failed":
      return "error";
    case "connecting":
      return "starting";
    case "waiting":
      return "pairing";
    default:
      return running ? "connected" : "disconnected";
  }
}

export function summarizeMessagingStatus(
  providerId: MessagingProviderId,
  statuses: AdapterInfo[],
): Pick<
  MessagingProviderState,
  | "adapterId"
  | "adapterIds"
  | "reportedTypes"
  | "running"
  | "sessionCount"
  | "uptimeSeconds"
  | "connectionState"
  | "lastError"
  | "paired"
  | "statusNote"
> {
  const matches = statuses.filter(
    (status) => normalizeMessagingProvider(status.type) === providerId,
  );

  if (matches.length === 0) {
    return {
      adapterId: null,
      adapterIds: [],
      reportedTypes: [],
      running: false,
      sessionCount: 0,
      uptimeSeconds: null,
      connectionState: "disconnected",
      lastError: null,
      paired: null,
      statusNote: null,
    };
  }

  const primary = matches.find((status) => status.running) ?? matches[0];
  const reportedTypes = uniqueStrings(matches.map((status) => status.type));
  const running = matches.some((status) => status.running);
  const sessionCount = matches.reduce(
    (sum, status) => sum + (Number(status.session_count) || 0),
    0,
  );
  const uptimeSeconds =
    matches.reduce(
      (max, status) => Math.max(max, Number(status.uptime_seconds) || 0),
      0,
    ) || null;
  const pairedValue =
    matches.find((status) => typeof status.paired === "boolean")?.paired ?? null;
  const lastError =
    matches
      .map((status) => status.last_error)
      .find((value) => typeof value === "string" && value.trim()) ?? null;

  const connectionState = matches.reduce<MessagingConnectionState>(
    (best, status) => {
      const next = normalizeMessagingConnectionState(
        status.connection_state,
        status.running,
      );
      return CONNECTION_STATE_PRIORITY[next] > CONNECTION_STATE_PRIORITY[best]
        ? next
        : best;
    },
    running ? "connected" : "disconnected",
  );

  const legacyWhatsApp =
    providerId === "whatsapp" &&
    reportedTypes.some((type) => type === "whatsapp") &&
    !reportedTypes.some((type) => type.includes("web"));

  return {
    adapterId: primary.adapter_id ?? null,
    adapterIds: uniqueStrings(matches.map((status) => status.adapter_id)),
    reportedTypes,
    running,
    sessionCount,
    uptimeSeconds,
    connectionState:
      !running && lastError && connectionState === "disconnected"
        ? "error"
        : connectionState,
    lastError,
    paired: pairedValue,
    statusNote: legacyWhatsApp
      ? "Backend reports the legacy WhatsApp adapter; WhatsApp Web pairing events are not exposed here yet."
      : null,
  };
}

export function buildMessagingSummary(
  providers: Record<MessagingProviderId, MessagingProviderState>,
): MessagingSummary {
  const entries = PROVIDER_IDS.map((providerId) => providers[providerId]);
  const activeCount = entries.filter(
    (provider) =>
      provider.running || provider.connectionState === "connected",
  ).length;
  const warningCount = entries.filter((provider) =>
    ["starting", "pairing", "reconnecting"].includes(provider.connectionState),
  ).length;
  const errorCount = entries.filter(
    (provider) => provider.connectionState === "error",
  ).length;

  const tone =
    errorCount > 0
      ? "error"
      : warningCount > 0
        ? "warning"
        : activeCount > 0
          ? "active"
          : "idle";

  const label =
    activeCount > 0
      ? `${activeCount} active`
      : warningCount > 0
        ? "Connecting"
        : errorCount > 0
          ? "Needs attention"
          : "Disconnected";

  const tooltip = PROVIDER_IDS.map((providerId) => {
    const provider = providers[providerId];
    const name = providerId === "telegram" ? "Telegram" : "WhatsApp";
    return `${name}: ${provider.connectionState}`;
  }).join(" · ");

  return { activeCount, errorCount, tone, label, tooltip };
}

export function shouldAutoRefreshMessagingHealth(
  providers: Record<MessagingProviderId, MessagingProviderState>,
): boolean {
  const summary = buildMessagingSummary(providers);
  const hasPendingAction = Object.values(providers).some(
    (provider) => provider.pendingAction != null,
  );
  return summary.tone === "error" && summary.activeCount > 0 && !hasPendingAction;
}

function reconcileMessagingHealthAutoRefresh(
  providers: Record<MessagingProviderId, MessagingProviderState>,
) {
  if (typeof window === "undefined") {
    return;
  }
  if (!shouldAutoRefreshMessagingHealth(providers)) {
    staleErrorRecoveryArmed = false;
    if (staleErrorRecoveryTimer !== null) {
      window.clearTimeout(staleErrorRecoveryTimer);
      staleErrorRecoveryTimer = null;
    }
    return;
  }
  if (staleErrorRecoveryArmed) {
    return;
  }
  staleErrorRecoveryArmed = true;
  staleErrorRecoveryTimer = window.setTimeout(() => {
    staleErrorRecoveryTimer = null;
    void useMessagingStore
      .getState()
      .refreshStatus({ silent: true })
      .catch(() => {});
  }, STALE_ERROR_RECOVERY_DELAY_MS);
}

export function getSessionLabel(
  providerId: MessagingProviderId,
  provider: MessagingProviderState,
): string {
  if (provider.sessionCount > 0) {
    return `${provider.sessionCount} session${provider.sessionCount === 1 ? "" : "s"}`;
  }
  if (provider.running && provider.sessionCount === 0) {
    return providerId === "telegram"
      ? "Listening (no messages yet)"
      : "Linked (idle)";
  }
  return "0 sessions";
}

export function hasConfiguredMessagingProviders(
  providers: Record<MessagingProviderId, MessagingProviderState>,
): boolean {
  return Object.values(providers).some(
    (provider) =>
      provider.running ||
      provider.configSummary?.configured ||
      provider.paired ||
      provider.configSummary?.paired,
  );
}

function friendlyMessagingError(
  providerId: MessagingProviderId,
  error: unknown,
): string {
  const message = formatErrorMessage(error);
  const normalized = message.toLowerCase();

  if (providerId === "telegram") {
    if (normalized.includes("bot token")) {
      return "Enter a Telegram bot token from @BotFather before connecting.";
    }
    if (normalized.includes("python-telegram-bot")) {
      return "Telegram support is not installed. Run `pip install 'dan[messaging]'` in the backend environment.";
    }
  }

  if (providerId === "whatsapp") {
    if (
      normalized.includes("unknown adapter type") &&
      normalized.includes("whatsapp-web")
    ) {
      return "This backend does not expose `whatsapp-web` yet. The desktop UI is wired for it, but the server route is still missing.";
    }
    if (normalized.includes("neonize")) {
      return "WhatsApp Web support is not installed. Run `pip install 'dan[whatsapp-web]'` in the backend environment.";
    }
  }

  if (isMissingEndpointError(error)) {
    return "This backend endpoint is not available yet.";
  }

  return message.replace(/^\d+:\s*/, "").trim();
}

function buildProviderConfigPayload(
  providerId: MessagingProviderId,
  provider: MessagingProviderState,
  options?: { includeSecret?: boolean },
): Record<string, unknown> {
  if (providerId === "telegram") {
    const config: Record<string, unknown> = {};
    const allowedChatIds = parseIntegerList(provider.allowedChatIdsText);
    if (allowedChatIds.length > 0) {
      config.allowed_chat_ids = allowedChatIds;
    }
    if (options?.includeSecret && provider.botToken.trim()) {
      config.bot_token = provider.botToken.trim();
    }
    return config;
  }

  const allowedJids = parseLineList(provider.allowedJidsText);
  return allowedJids.length > 0 ? { allowed_jids: allowedJids } : {};
}

function mergeConfigSummaryIntoProvider(
  providerId: MessagingProviderId,
  current: MessagingProviderState,
  summary: AdapterConfigSummary,
): Partial<MessagingProviderState> {
  const next: Partial<MessagingProviderState> = {
    configSummary: summary,
    configEndpointAvailable: true,
  };

  if (providerId === "telegram") {
    next.allowedChatIdsText = Array.isArray(summary.allowed_chat_ids)
      ? summary.allowed_chat_ids.map((value) => String(value)).join("\n")
      : "";
  }

  if (providerId === "whatsapp") {
    next.allowedJidsText = Array.isArray(summary.allowed_jids)
      ? summary.allowed_jids.join("\n")
      : "";
  }

  if (typeof summary.paired === "boolean") {
    next.paired = summary.paired;
  }

  if (summary.connection_state != null) {
    next.connectionState = normalizeMessagingConnectionState(
      summary.connection_state,
      current.running,
    );
  }

  if (typeof summary.last_error === "string" && summary.last_error.trim()) {
    next.lastError = summary.last_error.trim();
  }

  return next;
}

function closeProviderEvents(providerId: MessagingProviderId) {
  const existing = eventSources.get(providerId);
  if (existing) {
    existing.source.close();
    eventSources.delete(providerId);
  }
}

function attachProviderEvents(
  providerId: MessagingProviderId,
  adapterId: string,
  applyUpdate: (updater: MessagingStateUpdater) => void,
) {
  if (providerId !== "whatsapp") return;
  closeProviderEvents(providerId);

  let sawEvent = false;
  const source = connectAdapterEvents(
    adapterId,
    (event) => {
      sawEvent = true;
      const eventType = String(event.type ?? event.event ?? "").toLowerCase();
      let nextProvidersSnapshot: Record<
        MessagingProviderId,
        MessagingProviderState
      > | null = null;
      applyUpdate((state) => {
        const provider = state.providers[providerId];
        const patch: Partial<MessagingProviderState> = {
          eventsEndpointAvailable: true,
        };

        if (eventType === "qr") {
          patch.qrData =
            typeof event.qr_data === "string" && event.qr_data.trim()
              ? event.qr_data.trim()
              : null;
          patch.qrSvgDataUri =
            typeof event.svg_data_uri === "string" && event.svg_data_uri.trim()
              ? event.svg_data_uri.trim()
              : null;
          patch.connectionState = "pairing";
          patch.paired = false;
          patch.lastError = null;
          patch.statusNote =
            "Waiting for a QR scan. Open WhatsApp > Linked Devices > Link a Device.";
        } else if (eventType === "pair_status") {
          const status = String(
            event.status ?? event.pair_status ?? "",
          ).toLowerCase();
          if (status === "paired" || status === "connected") {
            patch.connectionState = "connected";
            patch.paired = true;
            patch.lastError = null;
            patch.qrData = null;
            patch.qrSvgDataUri = null;
            patch.statusNote = "WhatsApp linked successfully.";
          } else if (status === "waiting" || status === "pairing") {
            patch.connectionState = "pairing";
            patch.paired = false;
          } else if (status === "disconnected") {
            patch.connectionState = "disconnected";
            patch.qrData = null;
            patch.qrSvgDataUri = null;
          } else if (status === "failed") {
            patch.connectionState = "error";
            patch.paired = false;
            patch.lastError = "WhatsApp pairing failed.";
          }
        } else if (eventType === "status") {
          if (event.connection_state != null) {
            patch.connectionState = normalizeMessagingConnectionState(
              event.connection_state,
              provider.running,
            );
          }
          if (typeof event.paired === "boolean") {
            patch.paired = event.paired;
          }
          if (
            typeof event.last_error === "string" &&
            event.last_error.trim()
          ) {
            patch.lastError = event.last_error.trim();
          }
          if (patch.connectionState && patch.connectionState !== "pairing") {
            patch.qrData = null;
            patch.qrSvgDataUri = null;
          }
        } else if (eventType.includes("error")) {
          patch.connectionState = "error";
          patch.lastError =
            typeof event.message === "string"
              ? event.message
              : "Messaging event stream reported an error.";
        }

        return {
          providers: (nextProvidersSnapshot = {
            ...state.providers,
            [providerId]: {
              ...provider,
              ...patch,
            },
          }),
        };
      });
      if (nextProvidersSnapshot) {
        reconcileMessagingHealthAutoRefresh(nextProvidersSnapshot);
      }
    },
    () => {
      const currentEntry = eventSources.get(providerId);
      if (currentEntry?.adapterId === adapterId) {
        eventSources.delete(providerId);
      }
      if (sawEvent) return;
      applyUpdate((state) => {
        const provider = state.providers[providerId];
        if (!provider.running) {
          return state;
        }
        return {
          providers: {
            ...state.providers,
            [providerId]: {
              ...provider,
              eventsEndpointAvailable: false,
              statusNote:
                "Live pairing events are not available on this backend yet.",
            },
          },
        };
      });
    },
  );
  eventSources.set(providerId, { adapterId, source });
}

const initialProviders: Record<MessagingProviderId, MessagingProviderState> = {
  telegram: createProviderState(),
  whatsapp: createProviderState(),
};

export const useMessagingStore = create<MessagingState>()(
  persist(
    (set, get) => ({
      providers: initialProviders,
      initialized: false,
      refreshing: false,
      lastRefreshError: null,

      initialize: async () => {
        if (bootPromise) return bootPromise;

        if (pollTimer === null && typeof window !== "undefined") {
          pollTimer = window.setInterval(() => {
            void useMessagingStore.getState().refreshStatus({ silent: true });
          }, POLL_INTERVAL_MS);
        }

        bootPromise = (async () => {
          await get().refreshConfig();
          await get().refreshStatus({ silent: true });

          const state = get();
          for (const providerId of PROVIDER_IDS) {
            const provider = state.providers[providerId];
            const canAutoStart =
              provider.enabled &&
              provider.autoStart &&
              !provider.running &&
              Boolean(
                provider.configSummary?.configured ||
                  (providerId === "whatsapp" && provider.configSummary?.paired),
              );
            if (!canAutoStart) continue;
            try {
              await state.connectProvider(providerId);
            } catch {
              // Individual connect errors are already pushed into provider state.
            }
          }
          set({ initialized: true });
        })().finally(() => {
          bootPromise = null;
        });

        return bootPromise;
      },

      teardown: () => {
        if (pollTimer !== null) {
          window.clearInterval(pollTimer);
          pollTimer = null;
        }
        if (staleErrorRecoveryTimer !== null) {
          window.clearTimeout(staleErrorRecoveryTimer);
          staleErrorRecoveryTimer = null;
        }
        staleErrorRecoveryArmed = false;
        for (const providerId of PROVIDER_IDS) {
          closeProviderEvents(providerId);
        }
      },

      refreshStatus: async (options) => {
        const silent = options?.silent ?? false;
        if (!silent) {
          set({ refreshing: true, lastRefreshError: null });
        }

        try {
          const previousWhatsapp = get().providers.whatsapp;
          const statuses = await getAdapterStatus();
          const summaries = {
            telegram: summarizeMessagingStatus("telegram", statuses),
            whatsapp: summarizeMessagingStatus("whatsapp", statuses),
          } satisfies Record<
            MessagingProviderId,
            ReturnType<typeof summarizeMessagingStatus>
          >;
          let nextProvidersSnapshot: Record<
            MessagingProviderId,
            MessagingProviderState
          > | null = null;
          set((state) => {
            const nextProviders = { ...state.providers };
            for (const providerId of PROVIDER_IDS) {
              const current = state.providers[providerId];
              const summary = summaries[providerId];
              nextProviders[providerId] = {
                ...current,
                ...summary,
                lastError: summary.running
                  ? summary.lastError ?? null
                  : summary.lastError ?? current.lastError,
                pendingAction:
                  current.pendingAction === "refresh"
                    ? null
                    : current.pendingAction === "disconnect" && !summary.running
                      ? null
                      : (current.pendingAction === "connect" ||
                            current.pendingAction === "reconnect") &&
                          summary.running
                        ? null
                        : current.pendingAction,
              };
            }
            nextProvidersSnapshot = nextProviders;
            return {
              providers: nextProviders,
              refreshing: false,
              lastRefreshError: null,
            };
          });
          if (nextProvidersSnapshot) {
            reconcileMessagingHealthAutoRefresh(nextProvidersSnapshot);
          }

          const whatsappSummary = summaries.whatsapp;
          const currentEventEntry = eventSources.get("whatsapp");
          const whatsappPendingConnect =
            previousWhatsapp.pendingAction === "connect" ||
            previousWhatsapp.pendingAction === "reconnect";
          if (
            whatsappSummary.running &&
            whatsappSummary.adapterId &&
            currentEventEntry?.adapterId !== whatsappSummary.adapterId
          ) {
            attachProviderEvents("whatsapp", whatsappSummary.adapterId, (updater) => {
              set(updater);
            });
          } else if (!whatsappSummary.running && !whatsappPendingConnect) {
            closeProviderEvents("whatsapp");
          }
        } catch (error) {
          staleErrorRecoveryArmed = false;
          if (staleErrorRecoveryTimer !== null) {
            window.clearTimeout(staleErrorRecoveryTimer);
            staleErrorRecoveryTimer = null;
          }
          set((state) => {
            const nextProviders = { ...state.providers };
            for (const providerId of PROVIDER_IDS) {
              const current = state.providers[providerId];
              nextProviders[providerId] = {
                ...current,
                connectionState: "unknown",
                statusNote: "Backend unreachable",
                lastError: "Backend unreachable",
              };
            }
            return {
              providers: nextProviders,
              refreshing: false,
              lastRefreshError: formatErrorMessage(error),
            };
          });
        }
      },

      refreshConfig: async (providerId) => {
        const targets = providerId ? [providerId] : PROVIDER_IDS;
        for (const targetId of targets) {
          try {
            const summary = await getAdapterConfig(getProviderAdapterType(targetId));
            set((state) => {
              const current = state.providers[targetId];
              return {
                providers: {
                  ...state.providers,
                  [targetId]: {
                    ...current,
                    ...mergeConfigSummaryIntoProvider(targetId, current, summary),
                  },
                },
              };
            });
          } catch (error) {
            if (isMissingEndpointError(error)) {
              set((state) => ({
                providers: {
                  ...state.providers,
                  [targetId]: {
                    ...state.providers[targetId],
                    configEndpointAvailable: false,
                  },
                },
              }));
              continue;
            }
            set((state) => ({
              providers: {
                ...state.providers,
                [targetId]: {
                  ...state.providers[targetId],
                  lastError: friendlyMessagingError(targetId, error),
                },
              },
            }));
          }
        }
      },

      connectProvider: async (providerId) => {
        const current = get().providers[providerId];
        const pendingAction: PendingAction = current.running ? "reconnect" : "connect";
        set((state) => ({
          providers: {
            ...state.providers,
            [providerId]: {
              ...state.providers[providerId],
              pendingAction,
              connectionState: current.running ? "reconnecting" : "starting",
              lastError: null,
              ...(providerId === "whatsapp"
                ? { qrData: null, qrSvgDataUri: null }
                : {}),
            },
          },
        }));

        try {
          let configSaved = false;
          if (current.configEndpointAvailable !== false) {
            const savePayload = buildProviderConfigPayload(providerId, current, {
              includeSecret: true,
            });
            const hasSavePayload =
              providerId === "telegram"
                ? Boolean(
                    current.botToken.trim() ||
                      parseIntegerList(current.allowedChatIdsText).length,
                  )
                : Boolean(parseLineList(current.allowedJidsText).length);

            if (hasSavePayload) {
              try {
                const summary = await saveAdapterConfig(
                  getProviderAdapterType(providerId),
                  savePayload,
                );
                configSaved = true;
                set((state) => {
                  const provider = state.providers[providerId];
                  return {
                    providers: {
                      ...state.providers,
                      [providerId]: {
                        ...provider,
                        ...mergeConfigSummaryIntoProvider(
                          providerId,
                          provider,
                          summary,
                        ),
                      },
                    },
                  };
                });
              } catch (error) {
                if (isMissingEndpointError(error)) {
                  set((state) => ({
                    providers: {
                      ...state.providers,
                      [providerId]: {
                        ...state.providers[providerId],
                        configEndpointAvailable: false,
                      },
                    },
                  }));
                } else {
                  throw error;
                }
              }
            }
          }

          const latest = get().providers[providerId];
          if (
            providerId === "telegram" &&
            !latest.botToken.trim() &&
            !latest.configSummary?.configured
          ) {
            throw new Error("Telegram bot token required.");
          }

          const startConfig = buildProviderConfigPayload(providerId, latest, {
            includeSecret: !configSaved,
          });
          const result = await startAdapter(
            getProviderAdapterType(providerId),
            startConfig,
          );

          set((state) => ({
            providers: {
              ...state.providers,
              [providerId]: {
                ...state.providers[providerId],
                enabled: true,
                adapterId: result.adapter_id,
                adapterIds: uniqueStrings([
                  result.adapter_id,
                  ...state.providers[providerId].adapterIds,
                ]),
                reportedTypes: uniqueStrings([
                  result.type,
                  ...state.providers[providerId].reportedTypes,
                ]),
                running: true,
              },
            },
          }));

          if (providerId === "whatsapp") {
            attachProviderEvents(providerId, result.adapter_id, (updater) => {
              set(updater);
            });
          }

          await get().refreshStatus({ silent: true });
        } catch (error) {
          closeProviderEvents(providerId);
          set((state) => ({
            providers: {
              ...state.providers,
              [providerId]: {
                ...state.providers[providerId],
                running: false,
                connectionState: "error",
                lastError: friendlyMessagingError(providerId, error),
                pendingAction: null,
              },
            },
          }));
          throw error;
        } finally {
          set((state) => ({
            providers: {
              ...state.providers,
              [providerId]: {
                ...state.providers[providerId],
                pendingAction: null,
              },
            },
          }));
        }
      },

      disconnectProvider: async (providerId) => {
        const current = get().providers[providerId];
        const adapterIds = uniqueStrings([
          current.adapterId,
          ...current.adapterIds,
        ]);

        set((state) => ({
          providers: {
            ...state.providers,
            [providerId]: {
              ...state.providers[providerId],
              pendingAction: "disconnect",
              lastError: null,
            },
          },
        }));

        closeProviderEvents(providerId);

        let stopError: unknown = null;
        for (const adapterId of adapterIds) {
          try {
            await stopAdapter(adapterId);
          } catch (error) {
            stopError ??= error;
          }
        }

        await get().refreshStatus({ silent: true });

        set((state) => ({
          providers: {
            ...state.providers,
            [providerId]: {
              ...state.providers[providerId],
              enabled: false,
              adapterId: null,
              adapterIds: [],
              running: false,
              connectionState: stopError ? "error" : "disconnected",
              qrData: null,
              qrSvgDataUri: null,
              lastError: stopError
                ? friendlyMessagingError(providerId, stopError)
                : null,
              pendingAction: null,
            },
          },
        }));
      },

      reconnectProvider: async (providerId) => {
        const provider = get().providers[providerId];
        if (provider.running || provider.adapterIds.length > 0 || provider.adapterId) {
          await get().disconnectProvider(providerId);
        }
        await get().connectProvider(providerId);
      },

      resetProvider: async (providerId) => {
        if (providerId !== "whatsapp") {
          return;
        }

        set((state) => ({
          providers: {
            ...state.providers,
            [providerId]: {
              ...state.providers[providerId],
              pendingAction: "reset",
              lastError: null,
            },
          },
        }));

        closeProviderEvents(providerId);

        try {
          const summary = await resetAdapterConfig(getProviderAdapterType(providerId));
          set((state) => {
            const provider = state.providers[providerId];
            return {
              providers: {
                ...state.providers,
                [providerId]: {
                  ...provider,
                  ...mergeConfigSummaryIntoProvider(providerId, provider, summary),
                  enabled: false,
                  adapterId: null,
                  adapterIds: [],
                  running: false,
                  connectionState: "disconnected",
                  paired: false,
                  qrData: null,
                  qrSvgDataUri: null,
                  lastError: null,
                  statusNote:
                    "Pairing reset. Connect again to generate a fresh QR code.",
                },
              },
            };
          });
          await get().refreshStatus({ silent: true });
        } catch (error) {
          set((state) => ({
            providers: {
              ...state.providers,
              [providerId]: {
                ...state.providers[providerId],
                connectionState: "error",
                lastError: friendlyMessagingError(providerId, error),
              },
            },
          }));
          throw error;
        } finally {
          set((state) => ({
            providers: {
              ...state.providers,
              [providerId]: {
                ...state.providers[providerId],
                pendingAction: null,
              },
            },
          }));
        }
      },

      setAutoStart: (providerId, value) =>
        set((state) => ({
          providers: {
            ...state.providers,
            [providerId]: {
              ...state.providers[providerId],
              autoStart: value,
            },
          },
        })),

      setDraftField: (providerId, field, value) =>
        set((state) => ({
          providers: {
            ...state.providers,
            [providerId]: {
              ...state.providers[providerId],
              [field]: value,
            },
          },
        })),

      clearProviderError: (providerId) =>
        set((state) => ({
          providers: {
            ...state.providers,
            [providerId]: {
              ...state.providers[providerId],
              lastError: null,
            },
          },
        })),
    }),
    {
      name: "dan-editor-messaging",
      version: 1,
      partialize: (state) => ({
        providers: {
          telegram: {
            enabled: state.providers.telegram.enabled,
            autoStart: state.providers.telegram.autoStart,
          },
          whatsapp: {
            enabled: state.providers.whatsapp.enabled,
            autoStart: state.providers.whatsapp.autoStart,
          },
        },
      }),
      merge: (persistedState, currentState) => {
        const persisted =
          ((persistedState as Partial<MessagingState> | undefined)?.providers ??
            {}) as Partial<
            Record<MessagingProviderId, Partial<MessagingProviderState>>
          >;
        return {
          ...currentState,
          providers: mergePersistedMessagingProviders(
            persisted,
            currentState.providers,
          ),
        };
      },
    },
  ),
);
