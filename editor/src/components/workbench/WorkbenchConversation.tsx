import { Fragment, useEffect, useRef, useState } from "react";
import { ArrowDown, CornerDownRight, GitFork, RefreshCw, Sparkles, MessageSquareText } from "lucide-react";
import MarkdownRenderer from "../shared/MarkdownRenderer";
import type { ChatMessage } from "../../types/chat";
import { eventAgentName, eventDisclosure } from "./eventPresentation";
import type { ChatV2AgentRunEvent } from "../../lib/chatV2Api";

function EventDetail({ event }: { event: ChatV2AgentRunEvent }) {
  const display = eventDisclosure(event);
  return <details className="wb-event"><summary><span className="wb-agent-name">{eventAgentName(event)}</span><span>{display.label}</span></summary>
    {display.command && <pre>{display.command}</pre>}
    {display.body && <div className="wb-event-body"><MarkdownRenderer content={display.body} /></div>}
    <details className="wb-raw-event"><summary>Event data</summary><div className="wb-event-meta">{event.type}{event.run_id && ` · ${event.run_id}`}</div>{event.payload && <pre>{JSON.stringify(event.payload, null, 2)}</pre>}</details>
  </details>;
}

export function WorkbenchActivity({ events }: { events: ChatV2AgentRunEvent[] }) {
  return <div className="wb-activity"><p className="wb-eyebrow">LIVE ACTIVITY</p><h2>Agents & tools</h2><p className="wb-muted">Recorded activity from this session. Expand an event to inspect its details.</p>
    {!events.length && <p className="wb-activity-empty">Activity will appear here when work begins.</p>}
    {events.slice(-100).map((event, index) => <EventDetail key={`${event.source_event_id ?? event.type}-${index}`} event={event} />)}
  </div>;
}

export function WorkbenchConversation({ messages, pending, loading, status, onQuote, onSidecar, onRegenerate, onFork, liveActions = {} }: {
  messages: ChatMessage[];
  liveActions?: Record<string, string>;
  onRegenerate?: () => void;
  onFork?: () => void;
  pending: Record<string, boolean>;
  loading: boolean;
  status: string;
  onQuote: (text: string, messageIds: string[]) => void;
  onSidecar?: (text: string) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const [showLatest, setShowLatest] = useState(false);
  const [quote, setQuote] = useState("");
  const [quoteMessageIds, setQuoteMessageIds] = useState<string[]>([]);
  const lastMessage = messages.at(-1);
  let lastRequestIndex = messages.length - 1;
  while (lastRequestIndex >= 0 && messages[lastRequestIndex].role !== "user") lastRequestIndex -= 1;
  const lastRequestId = messages[lastRequestIndex]?.id;
  useEffect(() => {
    const selection = window.getSelection();
    if (selection && !selection.isCollapsed && scroller.current?.contains(selection.anchorNode)) return;
    if (following.current && scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [messages.length, lastMessage?.content, status]);
  useEffect(() => { setQuote(""); following.current = true; }, [messages[0]?.id]);
  const captureSelection = () => {
    const selection = window.getSelection();
    const container = scroller.current;
    if (selection && container?.contains(selection.anchorNode) && container.contains(selection.focusNode)) {
      setQuote(selection.toString().trim().slice(0, 6000));
      const range = selection.rangeCount ? selection.getRangeAt(0) : null;
      setQuoteMessageIds(Array.from(container.querySelectorAll<HTMLElement>("[data-message-id]")).filter((node) => range?.intersectsNode(node)).map((node) => node.dataset.messageId!));
    } else setQuote("");
  };
  return <div className="wb-conversation-wrap">
    <div ref={scroller} className="wb-conversation-scroll" onMouseUp={captureSelection} onKeyUp={captureSelection} onTouchEnd={() => window.setTimeout(captureSelection, 60)} onScroll={() => {
      const node = scroller.current;
      if (!node) return;
      following.current = node.scrollHeight - node.scrollTop - node.clientHeight < 100;
      setShowLatest(!following.current);
    }}>
      <div className="wb-transcript" aria-busy={loading}>
        {loading ? <div className="wb-empty"><p>Opening session…</p></div> : !messages.length ? <div className="wb-empty"><div className="wb-mark"><Sparkles size={26} strokeWidth={1.3} /></div><p className="wb-eyebrow">ROOM TO THINK. TOOLS TO BUILD.</p><h1>What are we working on?</h1><p>Start with a question, a task, or an unfinished idea.<br />Your work stays together in this project.</p></div> : messages.filter((message) => message.role !== "system").map((message) => <Fragment key={message.id}><article className={`wb-message wb-message-${message.role}`} data-message-id={message.id}>
          <div className="wb-message-author">{message.role === "user" ? "You" : message.runEvents?.length ? eventAgentName({ type: "message", payload: (message.runEvents.find((event) => event.detail?.payload || event.detail?.backend)?.detail?.payload ?? message.runEvents.find((event) => event.detail?.backend)?.detail ?? {}) as Record<string, unknown> }) : "DAN"}{pending[message.id] && <span className="wb-live-dot" aria-label="Working" />}</div>
          <div className="wb-message-body">{message.content ? <MarkdownRenderer content={message.content} /> : pending[message.id] ? <p className="wb-live-action"><span className="wb-live-dot" aria-hidden="true" /><span key={liveActions[message.id] ?? ""}>{liveActions[message.id] ?? "Working"}…</span></p> : <p className="wb-muted">No response content recorded.</p>}</div>
          {message.attachments?.map((attachment, index) => <div className="wb-attachment" key={`${attachment.filename}-${index}`}>{attachment.filename}{attachment.caption && ` — ${attachment.caption}`}</div>)}
          {message.toolCalls?.map((tool) => <details className="wb-event" key={tool.id}><summary><span>{tool.status === "running" ? "◌" : tool.status === "error" ? "!" : "✓"}</span><span>{tool.toolName}</span><span className="wb-muted">{tool.status}</span></summary><pre>{tool.argsPreview}{tool.outputPreview && `\n\n${tool.outputPreview}`}</pre></details>)}
          {Boolean(message.runEvents?.length) && <details className="wb-event"><summary>{message.runEvents!.length} activity {message.runEvents!.length === 1 ? "update" : "updates"}</summary>{message.runEvents!.map((event, index) => <EventDetail key={index} event={{ type: event.type, source_event_type: event.event_type, summary: event.summary, payload: (event.detail?.payload ?? event.detail ?? {}) as Record<string, unknown> }} />)}</details>}
          {message.taskRunRef && <div className="wb-message-state">{message.taskRunRef.status.replaceAll("_", " ")}</div>}
        </article>
        {message.id === lastRequestId && (onRegenerate || onFork) && <div className="wb-request-actions">
          {onRegenerate && <button type="button" onClick={onRegenerate} title="Run this request again and replace the answer below"><RefreshCw size={12} />Regenerate</button>}
          {onFork && <button type="button" onClick={onFork} title="Continue this conversation in a new chat"><GitFork size={12} />Fork</button>}
        </div>}</Fragment>)}
      </div>
    </div>
    {quote && <div className="wb-quote-action" onMouseDown={(event) => event.preventDefault()}><button onClick={() => { onQuote(quote, quoteMessageIds); setQuote(""); window.getSelection()?.removeAllRanges(); }}><CornerDownRight size={15} />Reply to selection</button>{onSidecar && <button onClick={() => { onSidecar(quote); setQuote(""); }}><MessageSquareText size={15} />Sidecar chat</button>}<button onClick={() => setQuote("")} aria-label="Dismiss selected quote">×</button></div>}
    {showLatest && <button className="wb-jump" onClick={() => { following.current = true; scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "instant" }); }}><ArrowDown size={14} />Latest</button>}
    <div className="wb-status" role="status">{workbenchStatus(status)}</div>
  </div>;
}

/** Idle "Ready" and raw event names are noise; terminal run states get plain words. */
export function workbenchStatus(status: string): string {
  const terminal: Record<string, string> = { failed: "Run failed", cancelled: "Run stopped", canceled: "Run stopped", stopped: "Run stopped", interrupted: "Run interrupted" };
  if (terminal[status]) return terminal[status];
  if (!status || status === "Ready" || /^[a-z]+(_[a-z]+)*$/.test(status)) return "";
  return status;
}
