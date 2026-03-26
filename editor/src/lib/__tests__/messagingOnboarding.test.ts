import { describe, expect, it } from "vitest";

import {
  buildMessagingSettingsEventDetail,
  shouldOfferMessagingOnboarding,
} from "../messagingOnboarding";
import type { MessagingProviderState } from "../../store/useMessagingStore";

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

describe("messagingOnboarding helpers", () => {
  it("builds provider-aware settings deep links", () => {
    expect(buildMessagingSettingsEventDetail()).toEqual({
      section: "messaging",
    });
    expect(buildMessagingSettingsEventDetail("telegram")).toEqual({
      section: "messaging",
      messagingProvider: "telegram",
    });
    expect(buildMessagingSettingsEventDetail("wechat")).toEqual({
      section: "messaging",
      messagingProvider: "wechat",
    });
  });

  it("offers onboarding only when messaging is unconfigured", () => {
    expect(
      shouldOfferMessagingOnboarding({
        fullScreen: true,
        messagingInitialized: true,
        messagingOnboardingOffered: false,
        providers: {
          telegram: makeProvider(),
          whatsapp: makeProvider(),
          wechat: makeProvider(),
        },
      }),
    ).toBe(false);

    expect(
      shouldOfferMessagingOnboarding({
        fullScreen: true,
        messagingInitialized: true,
        messagingOnboardingOffered: false,
        providers: {
          telegram: makeProvider({ configSummary: { configured: false } }),
          whatsapp: makeProvider(),
          wechat: makeProvider(),
        },
      }),
    ).toBe(false);

    expect(
      shouldOfferMessagingOnboarding({
        fullScreen: true,
        messagingInitialized: true,
        messagingOnboardingOffered: false,
        providers: {
          telegram: makeProvider({ configSummary: { configured: false } }),
          whatsapp: makeProvider({ configEndpointAvailable: false }),
          wechat: makeProvider({ configEndpointAvailable: false }),
        },
      }),
    ).toBe(true);

    expect(
      shouldOfferMessagingOnboarding({
        fullScreen: true,
        messagingInitialized: true,
        messagingOnboardingOffered: false,
        lastRefreshError: "backend unavailable",
        providers: {
          telegram: makeProvider({ configSummary: { configured: false } }),
          whatsapp: makeProvider(),
          wechat: makeProvider(),
        },
      }),
    ).toBe(false);
  });
});
