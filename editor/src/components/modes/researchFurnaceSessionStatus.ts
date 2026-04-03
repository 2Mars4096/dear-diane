import type { TrainingSession } from "../../store/useResearchStore";

const RECONNECTING_MARKER = "Reconnecting live session updates";

type StatusTone = "neutral" | "live" | "warning" | "success" | "danger" | "info";

export interface ResearchFurnaceSessionStatusUi {
  dotClass: string;
  label: string;
  messageClass: string;
  messageTone: StatusTone;
  pillClass: string;
  pulse: boolean;
  reconnecting: boolean;
}

const STATUS_STYLES: Record<
  TrainingSession["status"],
  {
    dotClass: string;
    label: string;
    messageTone: StatusTone;
    pillClass: string;
    pulse: boolean;
  }
> = {
  idle: {
    dotClass: "bg-gray-500",
    label: "Ready",
    messageTone: "neutral",
    pillClass: "border-gray-700 bg-gray-800 text-gray-300",
    pulse: false,
  },
  running: {
    dotClass: "bg-blue-400",
    label: "Live",
    messageTone: "live",
    pillClass: "border-blue-500/30 bg-blue-500/10 text-blue-200",
    pulse: true,
  },
  paused: {
    dotClass: "bg-amber-400",
    label: "Paused",
    messageTone: "warning",
    pillClass: "border-amber-500/30 bg-amber-500/10 text-amber-200",
    pulse: false,
  },
  completed: {
    dotClass: "bg-emerald-400",
    label: "Completed",
    messageTone: "success",
    pillClass: "border-emerald-500/30 bg-emerald-500/10 text-emerald-200",
    pulse: false,
  },
  failed: {
    dotClass: "bg-rose-400",
    label: "Failed",
    messageTone: "danger",
    pillClass: "border-rose-500/30 bg-rose-500/10 text-rose-200",
    pulse: false,
  },
};

function messageClassForTone(tone: StatusTone): string {
  switch (tone) {
    case "live":
      return "text-blue-300/80";
    case "warning":
      return "text-amber-300/80";
    case "success":
      return "text-emerald-300/80";
    case "danger":
      return "text-rose-300/80";
    case "info":
      return "text-sky-300/80";
    case "neutral":
    default:
      return "text-gray-400";
  }
}

export function isResearchSessionReconnecting(
  session: Pick<TrainingSession, "statusMessage">,
): boolean {
  return (session.statusMessage ?? "").includes(RECONNECTING_MARKER);
}

export function getResearchFurnaceSessionStatusUi(
  session: Pick<TrainingSession, "status" | "statusMessage">,
): ResearchFurnaceSessionStatusUi {
  const base = STATUS_STYLES[session.status];
  const reconnecting = isResearchSessionReconnecting(session);
  const messageTone =
    reconnecting && (session.status === "running" || session.status === "paused")
      ? "info"
      : base.messageTone;

  return {
    dotClass: reconnecting ? "bg-sky-400" : base.dotClass,
    label: base.label,
    messageClass: messageClassForTone(messageTone),
    messageTone,
    pillClass: reconnecting
      ? "border-sky-500/30 bg-sky-500/10 text-sky-200"
      : base.pillClass,
    pulse: reconnecting ? false : base.pulse,
    reconnecting,
  };
}
