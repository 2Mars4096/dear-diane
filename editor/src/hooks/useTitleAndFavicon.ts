/**
 * Updates document.title and favicon accent when mode or workspace changes.
 */
import { useEffect } from "react";
import { useAppStore } from "../store/useAppStore";
import { useWorkspaceStore } from "../store/useWorkspaceStore";

const MODE_LABELS: Record<string, string> = {
  chat: "Chat",
  development: "Code",
  operations: "Operations",
  research: "Research",
  analytics: "Analytics",
  content: "Content",
};

const MODE_COLORS: Record<string, string> = {
  chat: "#3b82f6",
  development: "#10b981",
  operations: "#f59e0b",
  research: "#8b5cf6",
  analytics: "#ef4444",
  content: "#ec4899",
};

function updateFaviconAccent(color: string) {
  const canvas = document.createElement("canvas");
  canvas.width = 32;
  canvas.height = 32;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  ctx.fillStyle = "#1e1e1e";
  ctx.fillRect(0, 0, 32, 32);
  ctx.fillStyle = "#fff";
  ctx.font = "bold 20px system-ui";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("D", 16, 17);

  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(26, 26, 5, 0, Math.PI * 2);
  ctx.fill();

  let link = document.querySelector('link[rel="icon"]') as HTMLLinkElement | null;
  if (!link) {
    link = document.createElement("link");
    link.rel = "icon";
    link.type = "image/x-icon";
    document.head.appendChild(link);
  }
  link.href = canvas.toDataURL();
}

export function useTitleAndFavicon() {
  const activeMode = useAppStore((s) => s.activeMode);
  const workspaceName = useWorkspaceStore((s) => s.getActiveWorkspace()?.name ?? null);

  useEffect(() => {
    const label = MODE_LABELS[activeMode] ?? activeMode;
    document.title = workspaceName
      ? `${workspaceName} — ${label} | DAN`
      : `${label} | DAN`;

    const color = MODE_COLORS[activeMode] ?? "#6b7280";
    updateFaviconAccent(color);
  }, [activeMode, workspaceName]);
}
