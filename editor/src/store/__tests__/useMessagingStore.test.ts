import { describe, expect, it } from "vitest";

import {
  buildProviderConfigPayload,
  buildMessagingSummary,
  getSessionLabel,
  hasConfiguredMessagingProviders,
  mergePersistedMessagingProviders,
  normalizeMessagingProvider,
  shouldAutoRefreshMessagingHealth,
  summarizeMessagingStatus,
  type MessagingProviderState,
} from "../useMessagingStore";

function makeProvider(
  extra: Partial<MessagingProviderState> = {},
): MessagingProviderState {
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
    wechatAppId: "",
    wechatAppSecret: "",
    wechatToken: "",
    wechatEncodingAesKey: "",
    wechatWebhookUrl: "",
    wechatCallbackPath: "callback",
    wechatAccountName: "",
    wechatAppName: "",
    wechatWelcomeMessage: "Welcome! Send a message to start a workflow.",
    wechatSupportEncryptedCallbacks: false,
    wechatPassiveReplyBudgetSecondsText: "4",
    wechatPassiveReplyFallbackText: "Working on it...",
    wechatApiBaseUrl: "",
    wechatAccessTokenRefreshMarginSecondsText: "300",
    wechatServerUrl: "",
    ...extra,
  };
}

describe("useMessagingStore helpers", () => {
  it("normalizes backend adapter types into frontend providers", () => {
    expect(normalizeMessagingProvider("telegram")).toBe("telegram");
    expect(normalizeMessagingProvider("WhatsAppWeb")).toBe("whatsapp");
    expect(normalizeMessagingProvider("whatsapp-web")).toBe("whatsapp");
    expect(normalizeMessagingProvider("wechat")).toBe("wechat");
    expect(normalizeMessagingProvider("email")).toBeNull();
  });

  it("summarizes legacy WhatsApp adapter status into one provider view", () => {
    const summary = summarizeMessagingStatus("whatsapp", [
      {
        type: "whatsapp",
        running: true,
        session_count: 2,
        adapter_id: "wa-1",
        uptime_seconds: 12.4,
      },
      {
        type: "whatsapp",
        running: false,
        session_count: 1,
        adapter_id: "wa-2",
        last_error: "stale secondary instance",
      },
    ]);

    expect(summary.adapterId).toBe("wa-1");
    expect(summary.adapterIds).toEqual(["wa-1", "wa-2"]);
    expect(summary.running).toBe(true);
    expect(summary.sessionCount).toBe(3);
    expect(summary.connectionState).toBe("connected");
    expect(summary.statusNote).toContain("legacy WhatsApp adapter");
  });

  it("builds a shell-level summary from provider states", () => {
    const summary = buildMessagingSummary({
      telegram: makeProvider({
        running: true,
        connectionState: "connected",
      }),
      whatsapp: makeProvider({
        connectionState: "error",
        lastError: "missing backend route",
      }),
      wechat: makeProvider(),
    });

    expect(summary.activeCount).toBe(1);
    expect(summary.errorCount).toBe(1);
    expect(summary.tone).toBe("error");
    expect(summary.tooltip).toContain("Telegram: connected");
    expect(summary.tooltip).toContain("WhatsApp: error");
  });

  it("ignores stale lastError when the provider is currently connected", () => {
    const summary = buildMessagingSummary({
      telegram: makeProvider({
        running: true,
        connectionState: "connected",
        lastError: "temporary polling failure",
      }),
      whatsapp: makeProvider(),
      wechat: makeProvider(),
    });

    expect(summary.activeCount).toBe(1);
    expect(summary.errorCount).toBe(0);
    expect(summary.tone).toBe("active");
  });

  it("auto-refreshes once when an active provider surface looks errored", () => {
    expect(
      shouldAutoRefreshMessagingHealth({
        telegram: makeProvider({
          running: true,
          connectionState: "connected",
        }),
        whatsapp: makeProvider({
          connectionState: "error",
          lastError: "temporary event-stream failure",
        }),
        wechat: makeProvider(),
      }),
    ).toBe(true);
  });

  it("does not auto-refresh while a messaging action is already in progress", () => {
    expect(
      shouldAutoRefreshMessagingHealth({
        telegram: makeProvider({
          running: true,
          connectionState: "connected",
          pendingAction: "reconnect",
        }),
        whatsapp: makeProvider({
          connectionState: "error",
        }),
        wechat: makeProvider(),
      }),
    ).toBe(false);
  });

  it("detects whether any messaging provider is already configured", () => {
    expect(
      hasConfiguredMessagingProviders({
        telegram: makeProvider(),
        whatsapp: makeProvider(),
        wechat: makeProvider(),
      }),
    ).toBe(false);

    expect(
      hasConfiguredMessagingProviders({
        telegram: makeProvider({
          configSummary: { configured: true },
        }),
        whatsapp: makeProvider(),
        wechat: makeProvider(),
      }),
    ).toBe(true);

    expect(
      hasConfiguredMessagingProviders({
        telegram: makeProvider({ enabled: true }),
        whatsapp: makeProvider(),
        wechat: makeProvider(),
      }),
    ).toBe(false);
  });

  it("getSessionLabel returns context-sensitive copy for telegram", () => {
    expect(
      getSessionLabel("telegram", makeProvider({ running: true, sessionCount: 0 })),
    ).toBe("Listening (no messages yet)");

    expect(
      getSessionLabel("telegram", makeProvider({ running: true, sessionCount: 3 })),
    ).toBe("3 sessions");

    expect(
      getSessionLabel("telegram", makeProvider({ running: true, sessionCount: 1 })),
    ).toBe("1 session");

    expect(
      getSessionLabel("telegram", makeProvider({ running: false, sessionCount: 0 })),
    ).toBe("0 sessions");
  });

  it("getSessionLabel returns context-sensitive copy for whatsapp", () => {
    expect(
      getSessionLabel("whatsapp", makeProvider({ running: true, sessionCount: 0 })),
    ).toBe("Linked (idle)");

    expect(
      getSessionLabel("whatsapp", makeProvider({ running: true, sessionCount: 2 })),
    ).toBe("2 sessions");
  });

  it("getSessionLabel returns context-sensitive copy for wechat", () => {
    expect(
      getSessionLabel("wechat", makeProvider({ running: true, sessionCount: 0 })),
    ).toBe("Ready (idle)");

    expect(
      getSessionLabel("wechat", makeProvider({ running: true, sessionCount: 2 })),
    ).toBe("2 sessions");
  });

  it("builds wechat config payloads that can clear saved optional fields", () => {
    const payload = buildProviderConfigPayload(
      "wechat",
      makeProvider({
        autoStart: true,
        wechatAppId: " ",
        wechatAppSecret: "",
        wechatToken: "",
        wechatEncodingAesKey: "",
        wechatWebhookUrl: " ",
        wechatCallbackPath: "",
        wechatAccountName: " ",
        wechatAppName: " ",
        wechatWelcomeMessage: "",
        wechatSupportEncryptedCallbacks: true,
        wechatPassiveReplyBudgetSecondsText: "",
        wechatPassiveReplyFallbackText: "",
        wechatApiBaseUrl: " ",
        wechatAccessTokenRefreshMarginSecondsText: "",
        wechatServerUrl: " ",
      }),
      { includeSecret: true },
    );

    expect(payload).toMatchObject({
      auto_start: true,
      app_id: "",
      webhook_url: "",
      callback_path: "callback",
      account_name: "",
      app_name: "",
      welcome_message: "",
      support_encrypted_callbacks: true,
      passive_reply_budget_seconds: 4,
      passive_reply_fallback_text: "Working on it...",
      api_base_url: "",
      access_token_refresh_margin_seconds: 300,
      server_url: "",
    });
    expect(payload).not.toHaveProperty("app_secret");
    expect(payload).not.toHaveProperty("token");
    expect(payload).not.toHaveProperty("encoding_aes_key");
  });

  it("restores only persisted enablement flags from storage", () => {
    const merged = mergePersistedMessagingProviders(
      {
        telegram: {
          enabled: true,
          autoStart: true,
          allowedChatIdsText: "111\n222",
        },
        whatsapp: {
          enabled: true,
          autoStart: false,
          allowedJidsText: "stale@s.whatsapp.net",
        },
        wechat: {
          enabled: true,
          autoStart: true,
          wechatCallbackPath: "stale",
        },
      },
      {
        telegram: makeProvider(),
        whatsapp: makeProvider(),
        wechat: makeProvider(),
      },
    );

    expect(merged.telegram.enabled).toBe(true);
    expect(merged.telegram.autoStart).toBe(true);
    expect(merged.telegram.allowedChatIdsText).toBe("");
    expect(merged.whatsapp.enabled).toBe(true);
    expect(merged.whatsapp.allowedJidsText).toBe("");
    expect(merged.wechat.enabled).toBe(true);
    expect(merged.wechat.autoStart).toBe(true);
    expect(merged.wechat.wechatCallbackPath).toBe("callback");
  });
});
