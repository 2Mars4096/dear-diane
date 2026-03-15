import { useState, useEffect, useRef, useCallback } from "react";

type Status = "connected" | "disconnected" | "reconnected";

export default function ConnectionBanner() {
  const [status, setStatus] = useState<Status>("connected");
  const wasDisconnected = useRef(false);
  const reconnectedTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const ping = useCallback(async () => {
    try {
      const res = await fetch("/api/health", { signal: AbortSignal.timeout(5000) });
      if (!res.ok) throw new Error();
      if (wasDisconnected.current) {
        wasDisconnected.current = false;
        setStatus("reconnected");
        clearTimeout(reconnectedTimer.current);
        reconnectedTimer.current = setTimeout(() => setStatus("connected"), 2500);
      } else {
        setStatus("connected");
      }
    } catch {
      wasDisconnected.current = true;
      setStatus("disconnected");
    }
  }, []);

  useEffect(() => {
    ping();
    const id = setInterval(ping, status === "disconnected" ? 3000 : 10000);
    return () => {
      clearInterval(id);
      clearTimeout(reconnectedTimer.current);
    };
  }, [ping, status]);

  if (status === "connected") return null;

  const isReconnected = status === "reconnected";

  return (
    <div
      className={`flex items-center justify-center gap-2 px-3 py-1 text-xs font-medium transition-colors ${
        isReconnected
          ? "bg-green-100 text-green-800"
          : "bg-amber-100 text-amber-800"
      }`}
    >
      {isReconnected ? (
        <>
          <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
          Reconnected
        </>
      ) : (
        <>
          <svg
            className="w-3 h-3 animate-spin"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
          >
            <path d="M21 12a9 9 0 1 1-6.219-8.56" />
          </svg>
          Backend unavailable — reconnecting…
        </>
      )}
    </div>
  );
}
