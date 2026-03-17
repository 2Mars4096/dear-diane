import { useCallback, useEffect, useRef, useState } from "react";
import { nativeBackend, isElectron } from "../../lib/electronBridge";

type Status = "running" | "starting" | "stopped" | "error" | "unknown";

const STATUS_META: Record<Status, { color: string; pulse: boolean; label: string }> = {
  running:  { color: "bg-emerald-500", pulse: false, label: "Backend running" },
  starting: { color: "bg-amber-400",   pulse: true,  label: "Backend starting…" },
  stopped:  { color: "bg-red-500",     pulse: false, label: "Backend stopped" },
  error:    { color: "bg-red-500",     pulse: false, label: "Backend error" },
  unknown:  { color: "bg-gray-400",    pulse: false, label: "Backend status unknown" },
};

export default function BackendStatusIndicator() {
  const [status, setStatus] = useState<Status>("unknown");
  const [ownedByUs, setOwnedByUs] = useState(false);
  const [showTooltip, setShowTooltip] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const healthTimer = useRef<ReturnType<typeof setInterval>>(undefined);

  const pollHealth = useCallback(async () => {
    try {
      const res = await fetch("/api/health", { signal: AbortSignal.timeout(4000) });
      if (res.ok) {
        setStatus((prev) => (prev === "starting" ? "starting" : "running"));
      } else {
        setStatus("stopped");
      }
    } catch {
      setStatus((prev) => (prev === "starting" ? "starting" : "stopped"));
    }
  }, []);

  useEffect(() => {
    if (isElectron()) {
      nativeBackend.getStatus().then((s) => {
        setStatus((s.status as Status) || "unknown");
        setOwnedByUs(s.ownedByUs);
      });
    } else {
      pollHealth();
    }

    const unsubStatus = nativeBackend.onStatusChange((data) => {
      setStatus((data.status as Status) || "unknown");
      setOwnedByUs(data.ownedByUs);
      setRestarting(false);
    });

    pollHealth();
    healthTimer.current = setInterval(pollHealth, 10_000);

    return () => {
      unsubStatus();
      clearInterval(healthTimer.current);
    };
  }, [pollHealth]);

  const handleClick = async () => {
    if (restarting) return;
    setRestarting(true);
    setStatus("starting");
    try {
      await nativeBackend.restart();
    } catch {
      setRestarting(false);
      setStatus("error");
    }
  };

  const meta = STATUS_META[status] ?? STATUS_META.unknown;

  return (
    <button
      type="button"
      className="relative flex items-center gap-1.5 rounded px-1.5 py-0.5 text-[11px] text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
      onClick={handleClick}
      onMouseEnter={() => setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
      title={`${meta.label}${ownedByUs ? "" : " (external)"}${status !== "running" ? " — click to restart" : " — click to restart"}`}
    >
      <span className="relative flex h-2 w-2">
        {meta.pulse && (
          <span className={`absolute inset-0 animate-ping rounded-full ${meta.color} opacity-75`} />
        )}
        <span className={`relative inline-flex h-2 w-2 rounded-full ${meta.color}`} />
      </span>
      <span className="hidden sm:inline">Server</span>

      {showTooltip && (
        <span className="absolute bottom-full left-1/2 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded bg-gray-900 px-2 py-1 text-[10px] font-medium text-white shadow-lg dark:bg-gray-700">
          {meta.label}
          {ownedByUs ? "" : " (external)"}
          {status !== "starting" && " — click to restart"}
        </span>
      )}
    </button>
  );
}
