import { useEffect, useRef, useState } from "react";
import { Sparkles, X, Loader2, Copy, Check } from "lucide-react";

import { sanitizeHtml } from "../../lib/sanitizeHtml";

interface ExplainPopupProps {
  explanation: string | null;
  loading: boolean;
  position: { x: number; y: number };
  onClose: () => void;
}

function SimpleMarkdown({ content }: { content: string }) {
  const html = content
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/`([^`]+)`/g, '<code class="bg-gray-800 text-gray-200 px-1 py-0.5 rounded text-[10px] font-mono">$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/\n\n/g, "</p><p class='mt-1.5'>")
    .replace(/\n- /g, "</p><li class='ml-3 list-disc'>")
    .replace(/\n(\d+)\. /g, "</p><li class='ml-3 list-decimal'>");
  const safeHtml = sanitizeHtml(`<p>${html}</p>`);
  return (
    <div
      className="text-xs text-gray-300 leading-relaxed [&_strong]:text-gray-100 [&_em]:text-gray-200"
      dangerouslySetInnerHTML={{ __html: safeHtml }}
    />
  );
}

export default function ExplainPopup({
  explanation,
  loading,
  position,
  onClose,
}: ExplainPopupProps) {
  const popupRef = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState(false);
  const [adjustedPos, setAdjustedPos] = useState(position);

  useEffect(() => {
    const el = popupRef.current;
    if (!el) {
      setAdjustedPos(position);
      return;
    }
    const rect = el.getBoundingClientRect();
    let { x, y } = position;
    if (x + rect.width > window.innerWidth - 16) {
      x = window.innerWidth - rect.width - 16;
    }
    if (y + rect.height > window.innerHeight - 16) {
      y = position.y - rect.height - 8;
    }
    x = Math.max(8, x);
    y = Math.max(8, y);
    setAdjustedPos({ x, y });
  }, [position, explanation, loading]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (popupRef.current && !popupRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  const handleCopy = () => {
    if (explanation) {
      navigator.clipboard.writeText(explanation);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  return (
    <div
      ref={popupRef}
      className="fixed z-50 bg-[#252526] border border-gray-700 rounded-lg shadow-2xl max-w-[420px] max-h-[320px] overflow-hidden flex flex-col"
      style={{ left: adjustedPos.x, top: adjustedPos.y }}
    >
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-gray-700 shrink-0">
        <span className="text-[10px] text-purple-400 font-medium flex items-center gap-1">
          <Sparkles size={12} /> AI Explanation
        </span>
        <div className="flex items-center gap-1">
          {explanation && (
            <button
              onClick={handleCopy}
              className="text-gray-500 hover:text-gray-300 p-0.5 rounded transition-colors"
              title="Copy"
            >
              {copied ? <Check size={11} className="text-green-400" /> : <Copy size={11} />}
            </button>
          )}
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-gray-300 p-0.5 rounded transition-colors"
          >
            <X size={12} />
          </button>
        </div>
      </div>
      <div className="p-3 overflow-y-auto min-h-[40px]">
        {loading ? (
          <div className="flex items-center gap-2 text-gray-400 text-xs">
            <Loader2 size={14} className="animate-spin text-purple-400" />
            Analyzing code…
          </div>
        ) : explanation ? (
          <SimpleMarkdown content={explanation} />
        ) : (
          <span className="text-xs text-gray-500">No explanation available.</span>
        )}
      </div>
    </div>
  );
}
