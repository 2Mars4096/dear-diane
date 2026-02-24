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
        <Toast key={t.id} id={t.id} type={t.type} message={t.message} onDismiss={removeToast} />
      ))}
    </div>
  );
}

function Toast({
  id,
  type,
  message,
  onDismiss,
}: {
  id: string;
  type: string;
  message: string;
  onDismiss: (id: string) => void;
}) {
  useEffect(() => {
    const ms = type === "error" ? 6000 : 4000;
    const timer = setTimeout(() => onDismiss(id), ms);
    return () => clearTimeout(timer);
  }, [id, type, onDismiss]);

  return (
    <div
      className={`pointer-events-auto flex items-center gap-2 px-3 py-2 rounded-lg shadow-lg text-white text-xs animate-[slideIn_0.25s_ease-out] ${BG[type] ?? BG.info}`}
    >
      <span className="text-sm font-bold">{ICONS[type] ?? ICONS.info}</span>
      <span className="max-w-[260px] truncate">{message}</span>
      <button onClick={() => onDismiss(id)} className="ml-1 opacity-70 hover:opacity-100">
        ×
      </button>
    </div>
  );
}
