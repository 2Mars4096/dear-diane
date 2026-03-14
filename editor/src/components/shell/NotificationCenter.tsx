import { useCallback, useEffect, useRef, useState } from "react";
import {
  Bell,
  Info,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  X,
  Check,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useAppStore } from "../../store/useAppStore";
import type { AppNotification } from "../../store/useAppStore";

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function timeAgo(ts: number): string {
  const sec = Math.floor((Date.now() - ts) / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}

const TYPE_META: Record<
  AppNotification["type"],
  { Icon: LucideIcon; color: string; bg: string }
> = {
  info: { Icon: Info, color: "text-blue-400", bg: "bg-blue-500/10" },
  success: { Icon: CheckCircle2, color: "text-green-400", bg: "bg-green-500/10" },
  warning: { Icon: AlertTriangle, color: "text-amber-400", bg: "bg-amber-500/10" },
  error: { Icon: XCircle, color: "text-red-400", bg: "bg-red-500/10" },
};

/* ------------------------------------------------------------------ */
/*  NotificationItem                                                   */
/* ------------------------------------------------------------------ */

function NotificationItem({
  notification,
}: {
  notification: AppNotification;
}) {
  const markRead = useAppStore((s) => s.markRead);
  const dismissNotification = useAppStore((s) => s.dismissNotification);
  const { Icon, color, bg } = TYPE_META[notification.type];

  return (
    <div
      className={`group relative px-3 py-2.5 border-b border-gray-800/50 transition-colors hover:bg-gray-800/40 ${
        notification.read ? "opacity-60" : ""
      }`}
      onClick={() => {
        if (!notification.read) markRead(notification.id);
      }}
    >
      <div className="flex items-start gap-2.5">
        <div className={`mt-0.5 p-1 rounded ${bg}`}>
          <Icon size={12} className={color} />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <p className="text-xs font-medium text-gray-200 truncate">
              {notification.title}
            </p>
            {!notification.read && (
              <span className="w-1.5 h-1.5 rounded-full bg-blue-400 shrink-0" />
            )}
          </div>

          {notification.message && (
            <p className="text-[11px] text-gray-400 mt-0.5 line-clamp-2">
              {notification.message}
            </p>
          )}

          <div className="flex items-center gap-2 mt-1">
            <span className="text-[10px] text-gray-600">
              {timeAgo(notification.timestamp)}
            </span>
            {notification.source && (
              <span className="text-[10px] text-gray-600">
                · {notification.source}
              </span>
            )}
            {notification.action && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  notification.action!.callback();
                }}
                className="text-[10px] text-blue-400 hover:text-blue-300 font-medium"
              >
                {notification.action.label}
              </button>
            )}
          </div>
        </div>

        <button
          onClick={(e) => {
            e.stopPropagation();
            dismissNotification(notification.id);
          }}
          className="opacity-0 group-hover:opacity-100 mt-0.5 p-0.5 rounded hover:bg-gray-700 text-gray-500 hover:text-gray-300 transition-opacity"
        >
          <X size={12} />
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  NotificationCenter                                                 */
/* ------------------------------------------------------------------ */

export default function NotificationCenter() {
  const notifications = useAppStore((s) => s.notifications);
  const unreadCount = useAppStore((s) => s.unreadCount);
  const markAllRead = useAppStore((s) => s.markAllRead);
  const clearAll = useAppStore((s) => s.clearAll);

  const [open, setOpen] = useState(false);
  const [pulse, setPulse] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const prevCountRef = useRef(notifications.length);

  // Pulse animation when a new notification arrives
  useEffect(() => {
    if (notifications.length > prevCountRef.current) {
      setPulse(true);
      const t = setTimeout(() => setPulse(false), 600);
      return () => clearTimeout(t);
    }
    prevCountRef.current = notifications.length;
  }, [notifications.length]);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const handleToggle = useCallback(() => setOpen((v) => !v), []);

  return (
    <div ref={containerRef} className="relative app-no-drag">
      {/* Bell button */}
      <button
        onClick={handleToggle}
        className={`relative p-1.5 rounded text-gray-400 hover:text-gray-200 hover:bg-white/10 transition-colors ${
          pulse ? "animate-bell-pulse" : ""
        }`}
        title="Notifications"
      >
        <Bell size={15} />
        {unreadCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 bg-blue-500 rounded-full text-[9px] text-white flex items-center justify-center font-bold px-0.5">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>

      {/* Dropdown */}
      {open && (
        <div className="absolute right-0 top-full mt-1 w-80 bg-[#1e1e2e] border border-[#313244] rounded-lg shadow-xl z-[100] max-h-[400px] overflow-hidden flex flex-col">
          {/* Header */}
          <div className="flex items-center justify-between px-3 py-2 border-b border-[#313244]">
            <h3 className="text-xs font-semibold text-gray-200">Notifications</h3>
            <div className="flex items-center gap-2">
              {unreadCount > 0 && (
                <button
                  onClick={markAllRead}
                  className="flex items-center gap-1 text-[10px] text-blue-400 hover:text-blue-300"
                  title="Mark all read"
                >
                  <Check size={10} />
                  <span>Mark read</span>
                </button>
              )}
              {notifications.length > 0 && (
                <button
                  onClick={clearAll}
                  className="text-[10px] text-gray-500 hover:text-gray-300"
                  title="Clear all"
                >
                  Clear
                </button>
              )}
            </div>
          </div>

          {/* List */}
          <div className="flex-1 overflow-y-auto">
            {notifications.length === 0 ? (
              <div className="px-4 py-8 text-center">
                <Bell size={20} className="mx-auto text-gray-700 mb-2" />
                <p className="text-xs text-gray-500">No notifications</p>
              </div>
            ) : (
              notifications.map((n) => (
                <NotificationItem key={n.id} notification={n} />
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
