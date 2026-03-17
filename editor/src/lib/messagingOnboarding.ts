import {
  hasConfiguredMessagingProviders,
  hasResolvedMessagingConfig,
  type MessagingProviderId,
  type MessagingProviderState,
} from "../store/useMessagingStore";

export interface MessagingSettingsEventDetail {
  section: "messaging";
  messagingProvider?: MessagingProviderId;
}

export function buildMessagingSettingsEventDetail(
  providerId?: MessagingProviderId,
): MessagingSettingsEventDetail {
  return providerId
    ? { section: "messaging", messagingProvider: providerId }
    : { section: "messaging" };
}

export function shouldOfferMessagingOnboarding(params: {
  fullScreen: boolean;
  messagingInitialized: boolean;
  messagingOnboardingOffered: boolean;
  lastRefreshError?: string | null;
  providers: Record<MessagingProviderId, MessagingProviderState>;
}): boolean {
  return (
    params.fullScreen &&
    params.messagingInitialized &&
    hasResolvedMessagingConfig(params.providers) &&
    !params.lastRefreshError &&
    !params.messagingOnboardingOffered &&
    !hasConfiguredMessagingProviders(params.providers)
  );
}
