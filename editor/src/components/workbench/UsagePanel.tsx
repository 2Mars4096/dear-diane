import { useEffect, useState } from "react";
import { Gauge } from "lucide-react";
import { useDismissDetails } from "./useDismissDetails";

export type UsageWindow = { label: string; used_percent: number; resets_at: number | null };
export type UsageAccount = { backend: string; account: string; label: string; windows: UsageWindow[]; plan?: string; error?: string; observed_at?: number };
const NAMES: Record<string, string> = { codex: "Codex", claude: "Claude Code", cursor: "Cursor", antigravity: "Antigravity", dan: "DAN" };
const POLL_MS = 5 * 60_000; // refreshed on every hover-open, then every 5 minutes while it stays open

export function resetsIn(at: number | null, now = Date.now() / 1000): string {
  if (!at) return "";
  const seconds = Math.max(0, at - now);
  if (seconds < 3600) return `resets in ${Math.max(1, Math.round(seconds / 60))}m`;
  if (seconds < 86400) return `resets in ${Math.round(seconds / 3600)}h`;
  return `resets in ${Math.round(seconds / 86400)}d`;
}
export function agoLabel(at?: number, now = Date.now() / 1000): string {
  if (!at) return "";
  const seconds = Math.max(0, now - at);
  return seconds < 60 ? "just now" : seconds < 3600 ? `${Math.round(seconds / 60)}m ago` : `${Math.round(seconds / 3600)}h ago`;
}

/** Sidebar footer: hover to see the latest quota for the two most recently used accounts. */
export function UsagePanel() {
  const details = useDismissDetails();
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<{ accounts: UsageAccount[]; fetched_at?: number } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    let disposed = false;
    const refresh = async () => {
      try {
        const response = await fetch("/api/usage?limit=2");
        if (!response.ok) throw new Error("Usage is unavailable right now.");
        const next = await response.json();
        if (!disposed) { setData(next); setError(""); }
      } catch (caught) { if (!disposed) setError(caught instanceof Error ? caught.message : String(caught)); }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [open]);
  return <details ref={details} className="wb-native-settings wb-usage" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary title="Usage and remaining quota" aria-label="Usage"><Gauge size={15} />Usage</summary>
    <div className="wb-native-settings-panel wb-usage-panel" aria-live="polite">
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p>Checking usage…</p>}
      {data?.accounts.map((account) => <section key={`${account.backend}:${account.account}`} className="wb-usage-account">
        <h4>{NAMES[account.backend] ?? account.backend}<span>{account.account}{account.plan ? ` · ${account.plan}` : ""}</span></h4>
        {account.error ? <p className="wb-muted">{account.error}</p> : account.windows.map((window) => {
          const used = Math.min(100, Math.max(0, window.used_percent));
          return <div key={window.label} className="wb-usage-row" data-level={used >= 90 ? "high" : used >= 70 ? "mid" : undefined}>
            <span className="wb-usage-label">{window.label}</span>
            <span className="wb-usage-bar" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(used)} aria-label={`${window.label} used`}><i style={{ width: `${used}%` }} /></span>
            <span className="wb-usage-pct">{Math.round(100 - used)}% left</span>
            <span className="wb-usage-reset">{resetsIn(window.resets_at)}</span>
          </div>;
        })}
        {!account.error && !account.windows.length && <p className="wb-muted">No limits reported yet.</p>}
      </section>)}
      {data?.fetched_at && <p className="wb-usage-stamp">Updated {agoLabel(data.fetched_at)} · refreshes when opened</p>}
    </div>
  </details>;
}
