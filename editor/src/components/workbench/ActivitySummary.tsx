import type { ReactNode } from "react";
import type { RunEventPayload } from "../../types/chat";

/** Describe observed work without exposing command lines or claiming an outcome. */
export function currentActivity(events: RunEventPayload[], live = "") {
  const latest = [...events].reverse().find(event => event.type !== "model_text_delta" && Boolean(event.summary?.trim()));
  const text = (live || latest?.summary || "").trim();
  if (/\b(pytest|vitest|test suite|running tests|npm run test)\b/i.test(text)) return "Running checks";
  if (/\b(npm run build|tsc|vite build|building)\b/i.test(text)) return "Checking the build";
  if (/\b(apply_patch|editing|updating|writing file)\b/i.test(text)) return "Updating files";
  if (/\b(reading|inspecting|searching|rg |grep |sed |cat )/i.test(text)) return "Checking files";
  if (/\b(browser|playwright|screenshot)\b/i.test(text)) return "Checking the interface";
  if (/\b(tool|command|\/bin\/|status_reported|accepted|queued|token|model\.)/i.test(text)) return "Working on your request";
  return text && text.length <= 100 && !/[\n{}]/.test(text) ? text.replace(/…$/, "") : "Working on your request";
}
export function ActivitySummary({ events, active, live, children }: { events: RunEventPayload[]; active: boolean; live?: string; children: ReactNode }) {
  return <details className="wb-event wb-work-details">
    <summary><span>{active ? currentActivity(events, live) : "Work details"}</span>{active && <small>Details</small>}</summary>
    <div className="wb-work-history">{children}</div>
  </details>;
}
