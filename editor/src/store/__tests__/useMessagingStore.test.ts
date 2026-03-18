import { describe, expect, it } from "vitest";

import {
  buildMessagingSummary,
  getSessionLabel,
  hasConfiguredMessagingProviders,
  mergePersistedMessagingProviders,
  normalizeMessagingProvider,
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
    ...extra,
  };
}

describe("useMessagingStore helpers", () => {
  it("normalizes backend adapter types into frontend providers", () => {
    expect(normalizeMessagingProvider("telegram")).toBe("telegram");
    expect(normalizeMessagingProvider("WhatsAppWeb")).toBe("whatsapp");
    expect(normalizeMessagingProvider("whatsapp-web")).toBe("whatsapp");
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
    });

    expect(summary.activeCount).toBe(1);
    expect(summary.errorCount).toBe(1);
    expect(summary.tone).toBe("error");
    expect(summary.tooltip).toContain("Telegram: connected");
    expect(summary.tooltip).toContain("WhatsApp: error");
  });

  it("detects whether any messaging provider is already configured", () => {
    expect(
      hasConfiguredMessagingProviders({
        telegram: makeProvider(),
        whatsapp: makeProvider(),
      }),
    ).toBe(false);

    expect(
      hasConfiguredMessagingProviders({
        telegram: makeProvider({
          configSummary: { configured: true },
        }),
        whatsapp: makeProvider(),
      }),
    ).toBe(true);

    expect(
      hasConfiguredMessagingProviders({
        telegram: makeProvider({ enabled: true }),
        whatsapp: makeProvider(),
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
      },
      {
        telegram: makeProvider(),
        whatsapp: makeProvider(),
      },
    );

    expect(merged.telegram.enabled).toBe(true);
    expect(merged.telegram.autoStart).toBe(true);
    expect(merged.telegram.allowedChatIdsText).toBe("");
    expect(merged.whatsapp.enabled).toBe(true);
    expect(merged.whatsapp.allowedJidsText).toBe("");
  });
});
