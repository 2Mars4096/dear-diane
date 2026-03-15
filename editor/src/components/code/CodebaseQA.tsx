/**
 * Floating codebase Q&A panel (Cmd+Shift+I).
 * Ask questions about open files and get AI-powered answers.
 */
import { useState, useRef, useEffect, useCallback, type KeyboardEvent } from "react";
import { Sparkles, X, Loader2, Send, Copy, Check } from "lucide-react";
import { useCodeStore } from "../../store/useCodeStore";
import { askCodebase } from "../../lib/aiCodeActions";

function SimpleMarkdown({ content }: { content: string }) {
  const html = content
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/```(\w*)\n([\s\S]*?)```/g, (_, _lang, code) =>
      `<pre class="bg-gray-950 border border-gray-700/50 rounded-md p-2 my-1.5 overflow-x-auto text-[11px] leading-relaxed font-mono"><code>${code.trim()}</code></pre>`)
    .replace(/`([^`]+)`/g, '<code class="bg-gray-800 text-gray-200 px-1 py-0.5 rounded text-[10px] font-mono">$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/\n\n/g, "</p><p class='mt-1.5'>")
    .replace(/\n- /g, "</p><li class='ml-3 list-disc'>")
    .replace(/\n(\d+)\. /g, "</p><li class='ml-3 list-decimal'>");
  return (
    <div
      className="text-xs text-gray-300 leading-relaxed [&_strong]:text-gray-100 [&_em]:text-gray-200"
      dangerouslySetInnerHTML={{ __html: `<p>${html}</p>` }}
    />
  );
}

interface CodebaseQAProps {
  onClose: () => void;
}

export default function CodebaseQA({ onClose }: CodebaseQAProps) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const handler = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [onClose]);

  const handleAsk = useCallback(async () => {
    const q = question.trim();
    if (!q || loading) return;
    setLoading(true);
    setError(null);
    setAnswer("");
    try {
      const files = useCodeStore.getState().openFiles.map((f) => ({
        path: f.path,
        content: f.content,
      }));
      const result = await askCodebase(q, files);
      setAnswer(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to get answer");
    } finally {
      setLoading(false);
    }
  }, [question, loading]);

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleAsk();
    }
  };

  const handleCopy = () => {
    if (answer) {
      navigator.clipboard.writeText(answer);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  const openFileCount = useCodeStore((s) => s.openFiles.length);

  return (
    <div
      ref={panelRef}
      className="fixed top-[20%] left-1/2 -translate-x-1/2 z-50 w-[620px] max-w-[90vw] bg-[#252526] border border-gray-700 rounded-lg shadow-2xl flex flex-col overflow-hidden"
    >
      {/* Header / input */}
      <div className="px-3 py-2.5 border-b border-gray-700 flex items-center gap-2">
        <Sparkles size={14} className="text-purple-400 shrink-0" />
        <input
          ref={inputRef}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about your codebase…"
          className="flex-1 bg-transparent text-sm text-gray-200 placeholder-gray-500 outline-none"
          disabled={loading}
        />
        <button
          onClick={handleAsk}
          disabled={!question.trim() || loading}
          className="p-1.5 rounded text-gray-400 hover:text-white hover:bg-gray-700 disabled:opacity-30 transition-colors"
          title="Ask (Enter)"
        >
          <Send size={14} />
        </button>
        <button
          onClick={onClose}
          className="p-1 text-gray-500 hover:text-gray-300 rounded transition-colors"
          title="Close (Esc)"
        >
          <X size={14} />
        </button>
      </div>

      {/* Context indicator */}
      <div className="px-3 py-1 border-b border-gray-700/50 text-[10px] text-gray-500 flex items-center justify-between">
        <span>Context: {openFileCount} open file{openFileCount !== 1 ? "s" : ""}</span>
        <span className="text-[10px] text-gray-600">⌘⇧I</span>
      </div>

      {/* Answer area */}
      {(loading || answer || error) && (
        <div className="p-4 max-h-[420px] overflow-y-auto">
          {loading && (
            <div className="flex items-center gap-2 text-gray-400 text-xs">
              <Loader2 size={14} className="animate-spin text-purple-400" />
              Thinking…
            </div>
          )}
          {error && (
            <div className="text-xs text-red-400 bg-red-900/20 rounded px-3 py-2">
              {error}
            </div>
          )}
          {answer && !loading && (
            <>
              <SimpleMarkdown content={answer} />
              <div className="mt-3 flex justify-end">
                <button
                  onClick={handleCopy}
                  className="flex items-center gap-1 text-[10px] text-gray-500 hover:text-gray-300 px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 transition-colors"
                >
                  {copied ? <Check size={10} className="text-green-400" /> : <Copy size={10} />}
                  {copied ? "Copied" : "Copy"}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
