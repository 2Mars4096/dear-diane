import { useEffect, useState, type ReactNode } from "react";
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
export function elapsedLabel(start: number | undefined, end: number) {
  if (!start || !Number.isFinite(start) || !Number.isFinite(end)) return '';
  const seconds = Math.max(0, Math.floor((end - start) / 1000));
  return `${Math.floor(seconds / 60)}m${String(seconds % 60).padStart(2, '0')}s`;
}
export function ActivitySummary({ events, active, live, startedAt, endedAt, children }: { events: RunEventPayload[]; active: boolean; live?: string; startedAt?: number; endedAt?: number; children: ReactNode }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!active) return;
    const tick = () => setNow(Date.now());
    tick(); const timer = window.setInterval(tick, 1000);
    window.addEventListener('focus', tick);
    return () => { window.clearInterval(timer); window.removeEventListener('focus', tick); };
  }, [active, startedAt]);
  const duration = elapsedLabel(startedAt, active ? now : endedAt || Number.NaN);
  const activity = currentActivity(events, live);
  const progress = active && !/^(Thinking|Working on your request)[.…]*$/i.test(activity) ? activity : '';
  return <details className="wb-event wb-work-details" data-working={active || undefined}>
    <summary><span className="wb-thinking-label">{active ? 'Thinking' : 'Work details'}{duration && <> <span className="wb-thinking-time">({duration})</span></>}</span>{progress && <span className="wb-thinking-progress" title={progress}>{progress}</span>}<small>Details</small></summary>
    <div className="wb-work-history">{children}</div>
  </details>;
}
