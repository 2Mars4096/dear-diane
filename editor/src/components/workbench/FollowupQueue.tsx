import { useState } from "react";
import { ChevronDown, Clock3, X } from "lucide-react";
import type { ChatV2TaskSnapshot } from "../../lib/chatV2Api";
type Row = { id: string; rawDetail?: string; detail: string; status: string; kind: string; lane: string; taskId?: string | null; runId?: string | null; clientMessageId?: string };
export function FollowupQueue({rows, runId, canSteer, onTask, onRemoved}: {rows:Row[];runId:string;canSteer:boolean;onTask:(task:ChatV2TaskSnapshot)=>void;onRemoved?:(row:Row)=>void}) {
  const [sending, setSending] = useState("");
  const [error, setError] = useState("");
  async function act(row: Row, action: "steer" | "cancel") {
    setSending(row.id); setError("");
    try {
      const response = await fetch(`/api/v2/agent-runs/${encodeURIComponent(row.runId || runId)}/queue/${encodeURIComponent(row.id.slice(6))}/${action}`, {method:"POST"});
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || (action === "steer" ? "Could not steer; the message is still queued." : "Could not remove this message."));
      onTask(body.task);
      if (action === "cancel") onRemoved?.(row);
    } catch (error) { setError(error instanceof Error ? error.message : action === "steer" ? "Could not steer this message." : "Could not remove this message."); }
    finally { setSending(""); }
  }
  const steer = (row: Row) => act(row, "steer");
  return <details className="wb-followup-queue" open>
    <summary><Clock3 size={13}/><span>Up next <small>{rows.length}</small></span><ChevronDown size={13}/></summary>
    <ol>{rows.map((row,index)=><li key={row.id}>
      <span className="wb-queue-position" aria-hidden="true">{index+1}</span>
      <details className="wb-queue-message"><summary><span>{row.rawDetail || row.detail}</span><ChevronDown size={12}/></summary>
        <p>{row.status === "waiting_dependency" ? "Waiting for an earlier task" : row.kind === "task" ? "Waiting to start" : row.lane === "append" && canSteer ? "Sending to the current run" : "Starts after the current run completes"}</p>
      </details>
      {row.kind === "followup" && row.runId === runId && row.lane !== "append" && <button className="wb-queue-steer" type="button" disabled={!canSteer || Boolean(sending)} title={canSteer ? "Send to the current run before the other waiting messages" : "Live steering is not available for this run"} onClick={()=>void steer(row)}>{sending===row.id ? "Sending…" : "Steer now"}</button>}
      {row.kind === "followup" && row.lane === "append" && <small className="wb-muted">{canSteer ? "Sending…" : "Queued"}</small>}
      {row.kind === "followup" && row.runId && <button className="wb-queue-remove" type="button" aria-label="Remove from Up next" title={row.status === "injected" ? "Delivery is in progress" : "Remove from Up next"} disabled={Boolean(sending) || row.status === "injected"} onClick={()=>void act(row, "cancel")}><X size={12}/></button>}
    </li>)}</ol>
    {error && <p role="alert">{error}</p>}
  </details>;
}
