import { MessageSquare } from "lucide-react";
import {
  buildMessagingSummary,
  useMessagingStore,
} from "../../store/useMessagingStore";
import { buildMessagingSettingsEventDetail } from "../../lib/messagingOnboarding";

type Tone = ReturnType<typeof buildMessagingSummary>["tone"];

const TONE_CLASSES: Record<Tone, string> = {
  idle: "border-gray-300 text-gray-500 hover:bg-gray-100 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-white/10",
  active:
    "border-emerald-300 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 dark:border-emerald-900/40 dark:bg-emerald-900/20 dark:text-emerald-300",
  warning:
    "border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100 dark:border-amber-900/40 dark:bg-amber-900/20 dark:text-amber-300",
  error:
    "border-red-300 bg-red-50 text-red-700 hover:bg-red-100 dark:border-red-900/40 dark:bg-red-900/20 dark:text-red-300",
};

const DOT_CLASSES: Record<Tone, string> = {
  idle: "bg-gray-400",
  active: "bg-emerald-500",
  warning: "bg-amber-500",
  error: "bg-red-500",
};

export default function MessagingStatusButton({
  showLabel = true,
  className = "",
}: {
  showLabel?: boolean;
  className?: string;
}) {
  const providers = useMessagingStore((state) => state.providers);
  const summary = buildMessagingSummary(providers);

  return (
    <button
      type="button"
      onClick={() =>
        window.dispatchEvent(
          new CustomEvent("app:openSettings", {
            detail: buildMessagingSettingsEventDetail(),
          }),
        )
      }
      className={`app-no-drag inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium transition-colors ${TONE_CLASSES[summary.tone]} ${className}`}
      title={summary.tooltip}
    >
      <span className={`h-2 w-2 rounded-full ${DOT_CLASSES[summary.tone]}`} />
      <MessageSquare size={14} />
      {showLabel && <span>Messaging</span>}
      {summary.activeCount > 0 && (
        <span className="rounded-full bg-black/10 px-1.5 py-0.5 text-[10px] font-semibold dark:bg-white/10">
          {summary.activeCount}
        </span>
      )}
    </button>
  );
}
