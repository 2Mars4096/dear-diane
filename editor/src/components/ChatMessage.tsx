import { useMemo, useCallback } from "react";
import { Check, X, Loader2, RotateCcw, ChevronRight } from "lucide-react";
import hljs from "highlight.js";
import type { ChatMessage } from "../types/chat";
import {
  mentionTypeColor,
  navigateToMention,
  type MentionRef,
} from "../lib/mentionParser";
import { useGraphStore } from "../store/useGraphStore";

// ---------------------------------------------------------------------------
// Markdown → HTML (simple renderer — no external dependency)
// ---------------------------------------------------------------------------

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function applyInlineMarkdown(line: string): string {
  return line
    .replace(
      /`([^`]+)`/g,
      '<code class="bg-gray-100 text-gray-800 px-1 py-0.5 rounded text-[12px] font-mono">$1</code>',
    )
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, "<em>$1</em>")
    .replace(
      /\[([^\]]+)\]\(([^)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener" class="text-indigo-600 underline hover:text-indigo-800">$1</a>',
    );
}

function renderMentionHtml(name: string, type: string, id: string): string {
  const color = mentionTypeColor(type as MentionRef["type"]);
  return `<button data-mention-type="${escapeHtml(type)}" data-mention-id="${escapeHtml(id)}" class="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-xs font-medium cursor-pointer ${color} hover:opacity-80 transition-opacity align-baseline">@${escapeHtml(name)}</button>`;
}

function renderMarkdown(raw: string): string {
  let text = raw.replace(
    /@\[([^\]]+)\]\((node|workflow|subgraph):([^)]+)\)/g,
    (_, name, type, id) => `\x01MENTION:${name}:${type}:${id}\x01`,
  );
  text = escapeHtml(text);
  text = text.replace(
    /\x01MENTION:([^:]+):([^:]+):([^\x01]+)\x01/g,
    (_, name, type, id) => renderMentionHtml(name, type, id),
  );

  const codeBlocks: string[] = [];
  text = text.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
    let highlighted: string;
    try {
      highlighted =
        lang && hljs.getLanguage(lang)
          ? hljs.highlight(code.trimEnd(), { language: lang }).value
          : hljs.highlightAuto(code.trimEnd()).value;
    } catch {
      highlighted = code.trimEnd();
    }
    codeBlocks.push(
      `<pre class="bg-gray-900 text-gray-100 rounded-lg p-3 my-2 overflow-x-auto text-[12px] leading-relaxed font-mono"><code>${highlighted}</code></pre>`,
    );
    return `\x00CB${codeBlocks.length - 1}\x00`;
  });

  const blocks = text.split(/\n{2,}/);
  const rendered = blocks
    .map((block) => {
      const trimmed = block.trim();
      if (!trimmed) return "";
      if (/^\x00CB\d+\x00$/.test(trimmed)) return trimmed;

      const lines = trimmed.split("\n");

      if (lines.length === 1) {
        const hm = trimmed.match(/^(#{1,6})\s+(.+)$/);
        if (hm) {
          const lvl = hm[1].length;
          const sizes = [
            "text-base font-bold",
            "text-sm font-bold",
            "text-sm font-semibold",
            "text-xs font-semibold",
            "text-xs font-semibold",
            "text-xs font-semibold",
          ];
          return `<h${lvl} class="${sizes[lvl - 1]} mt-3 mb-1">${applyInlineMarkdown(hm[2])}</h${lvl}>`;
        }
      }

      if (lines.every((l) => /^[-*]\s/.test(l))) {
        const items = lines
          .map(
            (l) =>
              `<li>${applyInlineMarkdown(l.replace(/^[-*]\s/, ""))}</li>`,
          )
          .join("");
        return `<ul class="my-1 ml-4 list-disc space-y-0.5">${items}</ul>`;
      }

      if (lines.every((l) => /^\d+\.\s/.test(l))) {
        const items = lines
          .map(
            (l) =>
              `<li>${applyInlineMarkdown(l.replace(/^\d+\.\s/, ""))}</li>`,
          )
          .join("");
        return `<ol class="my-1 ml-4 list-decimal space-y-0.5">${items}</ol>`;
      }

      return `<p class="my-1">${lines.map((l) => applyInlineMarkdown(l)).join("<br/>")}</p>`;
    })
    .join("");

  let result = rendered;
  codeBlocks.forEach((block, i) => {
    result = result.replace(`\x00CB${i}\x00`, block);
  });
  return result;
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
}: {
  status: NonNullable<ChatMessage["mutationStatus"]>;
  sessionMarker?: { historyCursor: number };
  onRevert?: () => void;
}) {
  const badge = (() => {
    switch (status) {
      case "proposed":
        return (
          <span className="inline-flex items-center gap-1 text-[10px] font-medium text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded-full">
            Proposed changes
            <ChevronRight size={10} />
          </span>
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
        <button className="ml-auto text-[10px] underline opacity-70 hover:opacity-100">
          View logs
        </button>
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
}

export default function ChatMessageBubble({
  message,
  sessionMarker,
  onRevert,
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
      const btn = (e.target as HTMLElement).closest<HTMLButtonElement>(
        "[data-mention-type]",
      );
      if (!btn) return;
      const type = btn.dataset.mentionType as MentionRef["type"];
      const id = btn.dataset.mentionId!;
      const name = btn.textContent?.replace(/^@/, "") ?? "";
      navigateToMention({ name, type, id }, store);
    },
    [store],
  );

  const tokens =
    message.tokenUsage
      ? message.tokenUsage.prompt + message.tokenUsage.completion
      : null;

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-3`}>
      <div
        className={`max-w-[85%] ${
          isUser
            ? "bg-indigo-50 text-gray-900 rounded-2xl rounded-br-md"
            : "bg-gray-50 text-gray-900 rounded-2xl rounded-bl-md"
        } px-3.5 py-2.5 shadow-xs`}
      >
        {isUser && !hasMentions ? (
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

        {message.runRef && <RunRefBlock runRef={message.runRef} />}

        {(message.mutationPlan != null || message.mutationStatus != null) && (
          <MutationBadge
            status={message.mutationStatus ?? "proposed"}
            sessionMarker={sessionMarker}
            onRevert={onRevert}
          />
        )}

        <div className="flex items-center justify-between mt-1.5 gap-3">
          <span className="text-[10px] text-gray-400">
            {relativeTime(message.timestamp)}
          </span>

          <div className="flex items-center gap-2">
            {tokens !== null && (
              <span className="text-[10px] text-gray-400">
                {tokens.toLocaleString()} tokens
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
