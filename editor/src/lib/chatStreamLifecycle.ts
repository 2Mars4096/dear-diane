export function isAssistantBubbleStreaming(args: {
  role: "user" | "assistant" | "system";
  isLastMessage: boolean;
  isStreaming: boolean;
  isRunStreaming: boolean;
}): boolean {
  return (
    args.role === "assistant" &&
    args.isLastMessage &&
    (args.isStreaming || args.isRunStreaming)
  );
}

export function shouldShowAssistantLoadingPlaceholder(args: {
  isUser: boolean;
  isStreaming: boolean;
  content: string;
  toolCallCount?: number;
}): boolean {
  void args.toolCallCount;
  return !args.isUser && args.isStreaming && !args.content.trim();
}

export function shouldReconnectStream(args: {
  closedIntentionally: boolean;
  activeChannelId: string | null;
  channelId: string;
  reconnectCount: number;
  maxReconnects?: number;
  closeCode?: number;
  hadTransportError?: boolean;
}): boolean {
  const transientClose =
    args.hadTransportError ||
    args.closeCode === 1006 ||
    args.closeCode === 1012 ||
    args.closeCode === 1013 ||
    args.closeCode === 1001 ||
    args.closeCode === 0;
  return (
    !args.closedIntentionally &&
    args.activeChannelId === args.channelId &&
    args.closeCode !== 4004 &&
    transientClose &&
    args.reconnectCount < (args.maxReconnects ?? 6)
  );
}

export function getStreamReconnectDelayMs(
  reconnectCount: number,
  options?: { baseDelayMs?: number; maxDelayMs?: number },
): number {
  const base = options?.baseDelayMs ?? 500;
  const max = options?.maxDelayMs ?? 4000;
  return Math.min(base * 2 ** Math.max(0, reconnectCount), max);
}

export function getStreamDisconnectError(args: {
  closedIntentionally: boolean;
  closeCode: number;
  hadTransportError: boolean;
  streamLabel?: string;
  recoveryHint?: string;
  backendState?: "unknown" | "unavailable" | "restarted";
  restoredSnapshot?: boolean;
}): string | null {
  if (args.closedIntentionally || args.closeCode === 1000 || args.closeCode === 1005) {
    return null;
  }
  const label = args.streamLabel ?? "Connection";
  const recoveryHint = args.recoveryHint ?? "Click Retry to resend.";
  const restoreNote = args.restoredSnapshot
    ? " I restored the latest saved partial response."
    : "";
  if (args.backendState === "unavailable") {
    return `${label} to the local DAN backend was lost because the backend is unavailable or restarting.${restoreNote} ${recoveryHint}`;
  }
  if (args.backendState === "restarted" || args.closeCode === 4004) {
    return `The local DAN backend restarted or forgot this live response stream.${restoreNote} ${recoveryHint}`;
  }
  return args.hadTransportError
    ? `${label} lost while streaming — retrying failed before the response completed. ${recoveryHint}`
    : `${label} lost — your response may be incomplete. ${recoveryHint}`;
}
