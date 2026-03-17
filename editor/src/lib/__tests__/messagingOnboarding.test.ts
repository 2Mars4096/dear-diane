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
        },
      }),
    ).toBe(true);

    expect(
      shouldOfferMessagingOnboarding({
        fullScreen: true,
        messagingInitialized: true,
        messagingOnboardingOffered: false,
        providers: {
          telegram: makeProvider({ configSummary: { configured: true } }),
          whatsapp: makeProvider(),
        },
      }),
    ).toBe(false);
  });
});
