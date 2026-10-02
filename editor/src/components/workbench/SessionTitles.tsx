import { useEffect, useRef } from 'react';
import type { ChatV2ThreadSummary } from '../../lib/chatV2Api';
export default function SessionTitles({ threads, onUpdated }: { threads: ChatV2ThreadSummary[]; onUpdated: () => Promise<void> }) {
  const latest = useRef({ threads, onUpdated }); latest.current = { threads, onUpdated };
  const attempted = useRef(new Set<string>());
  const working = useRef(false);
  useEffect(() => {
    let enabledAt = 0;
    try { enabledAt = Number(localStorage.getItem('dan.sessionTitles.enabledAt.v1')); } catch { return; }
    if (!enabledAt || working.current) return;
    working.current = true;
    void (async () => {
      try {
        for (;;) {
          const next = latest.current.threads.find(t => Date.parse(t.created_at) >= enabledAt && t.title_needs_summary && !attempted.current.has(`${t.workflow_id}:${t.id}`));
          if (!next) break;
          attempted.current.add(`${next.workflow_id}:${next.id}`);
          try {
            const response = await fetch(`/api/chats/${encodeURIComponent(next.workflow_id)}/${encodeURIComponent(next.id)}/title`, { method:'POST', signal:AbortSignal.timeout(30000) });
            if (response.ok && (await response.json()).title) await latest.current.onUpdated();
          } catch { /* Keep the original title if summarization is unavailable. */ }
        }
      } finally { working.current = false; }
    })();
  }, [threads]);
  return null;
}
