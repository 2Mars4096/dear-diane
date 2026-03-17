import { describe, expect, it } from "vitest";

import {
  buildMessagingSummary,
  hasConfiguredMessagingProviders,
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
  });
});
