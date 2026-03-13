import { useState, useMemo, useCallback, useEffect, useRef } from "react";
import { Check, X, Loader2, RotateCcw, ChevronRight, ChevronDown, Copy, Wrench } from "lucide-react";
import katex from "katex";
import "katex/dist/katex.min.css";
import { Marked, Renderer } from "marked";
import hljs from "../lib/hljs";
import type { ChatMessage } from "../types/chat";
import {
  mentionTypeColor,
  navigateToMention,
  type MentionRef,
} from "../lib/mentionParser";
import { shouldShowAssistantLoadingPlaceholder } from "../lib/chatStreamLifecycle";
import { useGraphStore } from "../store/useGraphStore";
import { groupToolCallsForDisplay } from "../lib/toolCallPresentation";
import ToolCallCard from "./ToolCallCard";
import RunOutputBlock from "./RunOutputBlock";

// ---------------------------------------------------------------------------
// Markdown → HTML  (marked + custom renderer)
// ---------------------------------------------------------------------------

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function renderKatex(tex: string, displayMode: boolean): string {
  try {
    return katex.renderToString(tex, { displayMode, throwOnError: false, strict: false });
  } catch {
    return `<code>${escapeHtml(tex)}</code>`;
  }
}

function renderMentionHtml(name: string, type: string, id: string): string {
  const isCode = type === "code";
  const color = isCode
    ? "bg-gray-800 text-gray-200 font-mono"
    : mentionTypeColor(type as MentionRef["type"]);
  const inner = isCode
    ? `<code class="text-[11px]">@${escapeHtml(name)}</code>`
    : `@${escapeHtml(name)}`;
  return `<button data-mention-type="${escapeHtml(type)}" data-mention-id="${escapeHtml(id)}" class="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-xs font-medium cursor-pointer ${color} hover:opacity-80 transition-opacity align-baseline">${inner}</button>`;
}

// ---------------------------------------------------------------------------
// Build a single Marked instance with DAN-styled renderers
// ---------------------------------------------------------------------------

const _preserved: string[] = [];
function _ph(html: string): string {
  _preserved.push(html);
  return `\x00PH${_preserved.length - 1}\x00`;
}

const _markedInstance = (() => {
  const renderer = new Renderer();

  renderer.code = ({ text, lang }: { text: string; lang?: string }) => {
    let highlighted: string;
    try {
      highlighted =
        lang && hljs.getLanguage(lang)
          ? hljs.highlight(text, { language: lang }).value
          : hljs.highlightAuto(text).value;
    } catch {
      highlighted = escapeHtml(text);
    }
    const langLabel = lang
      ? `<span class="text-[10px] text-gray-400 font-sans">${escapeHtml(lang)}</span>`
      : "";
    const idx = _preserved.length;
    const copyBtn = `<button data-copy-code="${idx}" class="text-[10px] text-gray-400 hover:text-gray-200 font-sans transition-colors">Copy</button>`;
    return _ph(
      `<div class="my-2 rounded-lg overflow-hidden border border-gray-700/50">` +
        `<div class="flex items-center justify-between px-3 py-1.5 bg-gray-800 border-b border-gray-700/50">${langLabel}${copyBtn}</div>` +
        `<pre class="bg-gray-900 text-gray-100 p-3 overflow-x-auto text-[12px] leading-relaxed font-mono m-0"><code>${highlighted}</code></pre>` +
        `<input type="hidden" data-code-raw="${idx}" value="${text.replace(/"/g, "&quot;")}" />` +
      `</div>`,
    );
  };

  renderer.codespan = ({ text }: { text: string }) =>
    `<code class="bg-gray-100 text-gray-800 px-1 py-0.5 rounded text-[12px] font-mono">${text}</code>`;

  renderer.heading = ({ text, depth }: { text: string; depth: number }) => {
    const sizes = [
      "text-base font-bold",
      "text-sm font-bold",
      "text-sm font-semibold",
      "text-xs font-semibold",
      "text-xs font-semibold",
      "text-xs font-semibold",
    ];
    return `<h${depth} class="${sizes[depth - 1]} mt-3 mb-1">${text}</h${depth}>`;
  };

  renderer.table = ({ header, rows }: { header: string; rows: string }) =>
    `<div class="my-2 overflow-x-auto rounded-lg border border-gray-200">` +
    `<table class="min-w-full divide-y divide-gray-200">` +
    `<thead class="bg-gray-50">${header}</thead>` +
    `<tbody>${rows}</tbody></table></div>`;

  renderer.tablerow = ({ text }: { text: string }) =>
    `<tr class="hover:bg-gray-50 transition-colors">${text}</tr>`;

  renderer.tablecell = ({
    text,
    header,
    align,
  }: {
    text: string;
    header: boolean;
    align: "center" | "left" | "right" | null;
  }) => {
    const tag = header ? "th" : "td";
    const cls = header
      ? "px-3 py-2 text-left text-[11px] font-semibold text-gray-500 uppercase tracking-wider border-b border-gray-200"
      : "px-3 py-2 text-sm border-b border-gray-100";
    const style = align ? ` style="text-align:${align}"` : "";
    return `<${tag} class="${cls}"${style}>${text}</${tag}>`;
  };

  renderer.blockquote = ({ text }: { text: string }) =>
    `<blockquote class="my-2 pl-3 border-l-2 border-indigo-300 text-gray-600 italic">${text}</blockquote>`;

  renderer.list = ({ body, ordered }: { body: string; ordered: boolean }) => {
    const tag = ordered ? "ol" : "ul";
    const cls = ordered
      ? "my-1 ml-4 list-decimal space-y-0.5"
      : "my-1 ml-4 list-disc space-y-0.5";
    return `<${tag} class="${cls}">${body}</${tag}>`;
  };

  renderer.listitem = ({ text }: { text: string }) => `<li>${text}</li>`;

  renderer.paragraph = ({ text }: { text: string }) =>
    `<p class="my-1">${text}</p>`;

  renderer.hr = () => `<hr class="my-3 border-gray-200" />`;

  renderer.link = ({ href, text }: { href: string; text: string }) => {
    if (/^\s*javascript\s*:/i.test(href)) return escapeHtml(text);
    const safeHref = /^(https?:|mailto:|#)/.test(href) ? href : "#";
    return `<a href="${safeHref}" target="_blank" rel="noopener noreferrer" class="text-indigo-600 underline hover:text-indigo-800">${text}</a>`;
  };

  renderer.strong = ({ text }: { text: string }) => `<strong>${text}</strong>`;
  renderer.em = ({ text }: { text: string }) => `<em>${text}</em>`;
  renderer.del = ({ text }: { text: string }) => `<del>${text}</del>`;

  return new Marked({ renderer, async: false });
})();

function renderMarkdown(raw: string): string {
  _preserved.length = 0;

  // 1. Extract mentions before marked processes them
  let text = raw.replace(
    /@\[([^\]]+)\]\((node|workflow|subgraph|file|code|docs|chat):([^)]+)\)/g,
    (_, name, type, id) => _ph(renderMentionHtml(name, type, id)),
  );

  // 2. Extract block math ($$...$$) before marked
  text = text.replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) =>
    _ph(`<div class="my-2 overflow-x-auto">${renderKatex(tex.trim(), true)}</div>`),
  );

  // 3. Extract inline math ($...$)
  text = text.replace(/(?<!\$)\$(?!\$)([^\n$]+?)\$(?!\$)/g, (_, tex) =>
    _ph(renderKatex(tex.trim(), false)),
  );

  // 4. Run marked (handles headings, lists, tables, blockquotes, code, inline formatting)
  let html = _markedInstance.parse(text) as string;

  // 5. Restore preserved placeholders
  _preserved.forEach((content, i) => {
    html = html.replaceAll(`\x00PH${i}\x00`, content);
  });

  return html;
}

// ---------------------------------------------------------------------------
// Relative time
// ---------------------------------------------------------------------------

function relativeTime(ts: number): string {
  const diff = Date.now() - ts;
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hrs = Math.floor(min / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

// ---------------------------------------------------------------------------
// Mutation badge
// ---------------------------------------------------------------------------

function MutationBadge({
  status,
  sessionMarker,
  onRevert,
  onPreview,
}: {
  status: NonNullable<ChatMessage["mutationStatus"]>;
  sessionMarker?: { historyCursor: number };
  onRevert?: () => void;
  onPreview?: () => void;
}) {
  const badge = (() => {
    switch (status) {
      case "proposed":
        return (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onPreview?.();
            }}
            className="inline-flex items-center gap-1 text-[10px] font-medium text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded-full hover:bg-amber-100 transition-colors cursor-pointer"
          >
            Proposed changes
            <ChevronRight size={10} />
          </button>
        );
      case "applied":
        return (
          <span className="inline-flex items-center gap-1 text-[10px] font-medium text-green-600 bg-green-50 px-1.5 py-0.5 rounded-full">
            <Check size={10} />
            Changes applied
          </span>
        );
      case "rejected":
        return (
          <span className="text-[10px] font-medium text-gray-500 bg-gray-100 px-1.5 py-0.5 rounded-full">
            Changes rejected
          </span>
        );
      case "reverted":
        return (
          <span className="text-[10px] font-medium text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded-full line-through">
            Reverted
          </span>
        );
      default:
        return null;
    }
  })();

  const revertBtn =
    status === "applied" ? (
      sessionMarker ? (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onRevert?.();
          }}
          className="inline-flex items-center gap-0.5 text-[10px] text-indigo-600 hover:text-indigo-800 font-medium transition-colors"
        >
          <RotateCcw size={10} />
          Revert to here
        </button>
      ) : (
        <span
          className="inline-flex items-center gap-0.5 text-[10px] text-gray-300 cursor-default"
          title="Available in current session only"
        >
          <RotateCcw size={10} />
          Revert
        </span>
      )
    ) : null;

  return (
    <div className="flex items-center gap-2 mt-1.5">
      {badge}
      {revertBtn}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Run reference block
// ---------------------------------------------------------------------------

function RunRefBlock({
  runRef,
}: {
  runRef: NonNullable<ChatMessage["runRef"]>;
}) {
  const configs: Record<
    string,
    {
      icon: React.ReactNode;
      text: string;
      color: string;
    }
  > = {
    running: {
      icon: <Loader2 size={12} className="animate-spin text-blue-500" />,
      text: `Running ${runRef.scope}…`,
      color: "text-blue-600 bg-blue-50 border-blue-100",
    },
    completed: {
      icon: <Check size={12} className="text-green-500" />,
      text: "Run completed",
      color: "text-green-600 bg-green-50 border-green-100",
    },
    failed: {
      icon: <X size={12} className="text-red-500" />,
      text: "Run failed",
      color: "text-red-600 bg-red-50 border-red-100",
    },
  };

  const cfg = configs[runRef.status] ?? configs.running;

  return (
    <div
      className={`flex items-center gap-1.5 mt-2 px-2 py-1.5 rounded-lg border text-xs ${cfg.color}`}
    >
      {cfg.icon}
      <span>{cfg.text}</span>
      {runRef.status !== "running" && (
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => useGraphStore.getState().focusLogPanel()}
            className="text-[10px] underline opacity-70 hover:opacity-100"
          >
            View logs
          </button>
          {runRef.runId && (
            <button
              onClick={() => useGraphStore.getState().focusHistoryPanel(runRef.runId)}
              className="text-[10px] underline opacity-70 hover:opacity-100"
            >
              View history
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface ChatMessageProps {
  message: ChatMessage;
  sessionMarker?: { historyCursor: number };
  onRevert?: () => void;
  onPreviewMutation?: (message: ChatMessage) => void;
  onCopyMarkdown?: () => void;
  isStreaming?: boolean;
}

function ToolCallGroup({
  toolCalls,
  mutationPlan,
  mutationStatus,
  onPreviewMutation,
}: {
  toolCalls: import("../types/chat").ToolCallInfo[];
  mutationPlan: unknown;
  mutationStatus?: ChatMessage["mutationStatus"];
  onPreviewMutation?: () => void;
}) {
  const displayToolCalls = useMemo(
    () => groupToolCallsForDisplay(toolCalls),
    [toolCalls],
  );
  const allDone = toolCalls.every((tc) => tc.status !== "running");
  const [expanded, setExpanded] = useState(false);
  const hasErrors = toolCalls.some((tc) => tc.status === "error");
  const durations = toolCalls.map((tc) => tc.durationMs ?? 0);
  const wallClockMs = durations.length > 0 ? Math.max(...durations) : 0;
  const durationLabel = wallClockMs < 1000 ? `${wallClockMs}ms` : `${(wallClockMs / 1000).toFixed(1)}s`;
  const latest = displayToolCalls[displayToolCalls.length - 1]?.toolCall;
  const latestRunning = latest && latest.status === "running";

  return (
    <div className="my-1.5">
      <button
        onClick={() => setExpanded(!expanded)}
        className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] transition-colors cursor-pointer hover:bg-gray-100 ${
          hasErrors ? "bg-red-50/50" : "bg-gray-50/70"
        }`}
      >
        {expanded ? <ChevronDown size={10} className="text-gray-400" /> : <ChevronRight size={10} className="text-gray-400" />}
        <Wrench size={10} className="text-gray-400" />
        <span className="font-medium text-gray-600">
          {displayToolCalls.length === 1
            ? displayToolCalls[0].fileReadGroup
              ? `read ${displayToolCalls[0].fileReadGroup.label}`
              : displayToolCalls[0].toolCall.toolName
            : `${displayToolCalls.length} tool calls`}
        </span>
        {allDone ? (
          hasErrors
            ? <X size={11} className="text-red-500" />
            : <Check size={11} className="text-emerald-500" />
        ) : (
          <Loader2 size={11} className="animate-spin text-blue-500" />
        )}
        {allDone && <span className="text-[10px] text-gray-400 tabular-nums">{durationLabel}</span>}
        {!allDone && latest && (
          <span className="text-[10px] text-gray-400 italic truncate max-w-[180px]">
            {latestRunning ? latest.toolName : `done: ${latest.toolName}`}
          </span>
        )}
      </button>
      {expanded && (
        <div className="ml-2 mt-0.5">
          {displayToolCalls.map((item) => {
            const tc = item.toolCall;
            const isMut = tc.toolName === "plan_graph_mutations";
            const ops = isMut && mutationPlan
              ? ((mutationPlan as Record<string, unknown>).operations as Array<{ op: string; name?: string; node_id?: string; node_type?: string }>) ?? []
              : undefined;
            return (
              <ToolCallCard
                key={item.key}
                toolCall={tc}
                mutationStatus={isMut ? mutationStatus : undefined}
                onPreviewChanges={isMut && mutationStatus === "proposed" ? onPreviewMutation : undefined}
                operations={ops}
                fileReadGroup={item.fileReadGroup}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function ChatMessageBubble({
  message,
  sessionMarker,
  onRevert,
  onPreviewMutation,
  onCopyMarkdown,
  isStreaming,
}: ChatMessageProps) {
  const isUser = message.role === "user";
  const store = useGraphStore();

  const html = useMemo(
    () => renderMarkdown(message.content),
    [message.content],
  );

  const hasMentions = message.content.includes("@[");

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      const target = e.target as HTMLElement;

      const copyBtn = target.closest<HTMLButtonElement>("[data-copy-code]");
      if (copyBtn) {
        const idx = copyBtn.dataset.copyCode;
        const hidden = copyBtn.closest("div")?.parentElement?.querySelector<HTMLInputElement>(`[data-code-raw="${idx}"]`);
        if (hidden) {
          navigator.clipboard.writeText(hidden.value).then(() => {
            copyBtn.textContent = "Copied!";
            setTimeout(() => { copyBtn.textContent = "Copy"; }, 1500);
          });
        }
        return;
      }

      const mentionBtn = target.closest<HTMLButtonElement>("[data-mention-type]");
      if (!mentionBtn) return;
      const type = mentionBtn.dataset.mentionType as MentionRef["type"];
      const id = mentionBtn.dataset.mentionId!;
      const name = mentionBtn.textContent?.replace(/^@/, "") ?? "";
      navigateToMention({ name, type, id }, store);
    },
    [store],
  );

  const tokens = (() => {
    const tu = message.tokenUsage;
    if (!tu) return null;
    const p = typeof tu.prompt === "number" ? tu.prompt : 0;
    const c = typeof tu.completion === "number" ? tu.completion : 0;
    return p + c > 0 ? p + c : null;
  })();

  const [elapsed, setElapsed] = useState<number | null>(null);
  const frozenElapsed = useRef<number | null>(null);

  useEffect(() => {
    if (!isStreaming || isUser) {
      if (elapsed !== null && frozenElapsed.current === null) {
        frozenElapsed.current = elapsed;
      }
      return;
    }
    frozenElapsed.current = null;
    const start = message.timestamp;
    const tick = () => setElapsed(Math.round((Date.now() - start) / 100) / 10);
    tick();
    const id = setInterval(tick, 100);
    return () => clearInterval(id);
  }, [isStreaming, isUser, message.timestamp]);

  const displayElapsed = isStreaming && !isUser ? elapsed : frozenElapsed.current;
  const showLoadingPlaceholder = shouldShowAssistantLoadingPlaceholder({
    isUser,
    isStreaming: Boolean(isStreaming),
    content: message.content,
    toolCallCount: message.toolCalls?.length ?? 0,
  });

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-3`}>
      <div
        className={`max-w-[85%] ${
          isUser
            ? "bg-indigo-50 text-gray-900 rounded-2xl rounded-br-md"
            : "bg-gray-50 text-gray-900 rounded-2xl rounded-bl-md"
        } px-3.5 py-2.5 shadow-xs${isStreaming && !isUser ? " dan-streaming-bubble" : ""}`}
      >
        {showLoadingPlaceholder ? (
          <div className="flex items-center gap-1.5 py-0.5 text-xs text-gray-400 dan-progress-pulse">
            <Loader2 size={11} className="animate-spin flex-shrink-0" />
            <span className="truncate">{message.progressStatus || "Working..."}</span>
          </div>
        ) : isUser && !hasMentions ? (
          <p className="text-sm whitespace-pre-wrap">{message.content}</p>
        ) : isUser && hasMentions ? (
          <div
            onClick={handleClick}
            className="text-sm whitespace-pre-wrap"
            dangerouslySetInnerHTML={{ __html: html }}
          />
        ) : (
          <div
            onClick={handleClick}
            className="text-sm leading-relaxed [&_pre]:my-2 [&_code]:break-words [&_a]:underline"
            dangerouslySetInnerHTML={{ __html: html }}
          />
        )}

        {message.toolCalls && message.toolCalls.length > 0 && (
          <ToolCallGroup
            toolCalls={message.toolCalls}
            mutationPlan={message.mutationPlan}
            mutationStatus={message.mutationStatus}
            onPreviewMutation={onPreviewMutation ? () => onPreviewMutation(message) : undefined}
          />
        )}

        {message.progressStatus && isStreaming && (
          <div className="flex items-center gap-1.5 mt-1.5 text-xs text-gray-400 dan-progress-pulse">
            <Loader2 size={11} className="animate-spin flex-shrink-0" />
            <span className="truncate">{message.progressStatus}</span>
          </div>
        )}

        {message.attachments && message.attachments.length > 0 && (
          <div className="mt-2 space-y-1">
            {message.attachments.map((att, idx) => (
              <div
                key={`${att.path}-${idx}`}
                className="text-xs rounded-md border border-gray-200 bg-white/70 px-2 py-1"
              >
                <span className="font-medium">📎 {att.filename}</span>
                {typeof att.size === "number" && (
                  <span className="text-gray-500"> ({Math.round(att.size / 1024)}KB)</span>
                )}
                {att.path && <span className="text-gray-500"> — <code>{att.path}</code></span>}
              </div>
            ))}
          </div>
        )}

        {/* Run output block (structured events) */}
        {message.runEvents && message.runEvents.length > 0 && (
          <RunOutputBlock events={message.runEvents} runRef={message.runRef} />
        )}

        {/* Fallback: simple run ref badge when no structured events */}
        {message.runRef && (!message.runEvents || message.runEvents.length === 0) && (
          <RunRefBlock runRef={message.runRef} />
        )}

        {/* Mutation badge — only show if no tool call card rendered it */}
        {!message.toolCalls?.length &&
          (message.mutationPlan != null || message.mutationStatus != null) && (
          <MutationBadge
            status={message.mutationStatus ?? "proposed"}
            sessionMarker={sessionMarker}
            onRevert={onRevert}
            onPreview={message.mutationStatus === "proposed" ? () => onPreviewMutation?.(message) : undefined}
          />
        )}

        <div className="flex items-center justify-between mt-1.5 gap-3">
          <span className="text-[10px] text-gray-400">
            {relativeTime(message.timestamp)}
          </span>

          <div className="flex items-center gap-2">
            {displayElapsed !== null && (
              <span className={`text-[10px] tabular-nums ${isStreaming && !isUser ? "text-indigo-400" : "text-gray-400"}`}>
                {displayElapsed.toFixed(1)}s
              </span>
            )}
            {tokens !== null && (
              <span className="text-[10px] text-gray-400">
                {tokens.toLocaleString()} tokens
              </span>
            )}
            {onCopyMarkdown && message.content && (
              <button
                onClick={onCopyMarkdown}
                className="text-gray-300 hover:text-gray-500 transition-colors"
                title="Copy as Markdown"
              >
                <Copy size={11} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
