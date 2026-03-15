import { useEffect } from "react";
import { useGraphStore } from "../store/useGraphStore";

const ICONS: Record<string, string> = {
  success: "✓",
  error: "✗",
  info: "ⓘ",
  warning: "⚠",
};

const BG: Record<string, string> = {
  success: "bg-green-600",
  error: "bg-red-600",
  info: "bg-blue-600",
  warning: "bg-amber-500",
};

export default function ToastContainer() {
  const toasts = useGraphStore((s) => s.toasts);
  const removeToast = useGraphStore((s) => s.removeToast);

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 pointer-events-none">
      {toasts.map((t) => (
        <Toast
          key={t.id}
          id={t.id}
          type={t.type}
          message={t.message}
          action={t.action}
          durationMs={t.durationMs}
          onDismiss={removeToast}
        />
      ))}
    </div>
  );
}

function Toast({
  id,
  type,
  message,
  action,
  durationMs,
  onDismiss,
}: {
  id: string;
  type: string;
  message: string;
  action?: { label: string; onClick: () => void };
  durationMs?: number;
  onDismiss: (id: string) => void;
}) {
  useEffect(() => {
    const ms = durationMs ?? (type === "error" ? 6000 : 4000);
    const timer = setTimeout(() => onDismiss(id), ms);
    return () => clearTimeout(timer);
  }, [id, type, durationMs, onDismiss]);

  return (
    <div
      className={`pointer-events-auto flex items-center gap-2 px-3 py-2 rounded-lg shadow-lg text-white text-xs animate-[slideIn_0.25s_ease-out] ${BG[type] ?? BG.info}`}
    >
      <span className="text-sm font-bold">{ICONS[type] ?? ICONS.info}</span>
      <span className="max-w-[260px] truncate">{message}</span>
      {action && (
        <button
          onClick={() => { action.onClick(); onDismiss(id); }}
          className="ml-1 px-2 py-0.5 rounded bg-white/20 hover:bg-white/30 font-semibold transition-colors"
        >
          {action.label}
        </button>
      )}
      <button onClick={() => onDismiss(id)} className="ml-1 opacity-70 hover:opacity-100">
        ×
      </button>
    </div>
  );
}
