import { useEffect, useRef, useState } from "react";
import { Trash2 } from "lucide-react";
import { nativeDebug } from "../../lib/electronBridge";
import { useDebugStore } from "../../store/useDebugStore";

export default function DebugConsole() {
  const output = useDebugStore((s) => s.debugConsoleOutput);
  const status = useDebugStore((s) => s.status);
  const activeFrameId = useDebugStore((s) => s.activeFrameId);
  const appendConsoleOutput = useDebugStore((s) => s.appendConsoleOutput);
  const clearConsoleOutput = useDebugStore((s) => s.clearConsoleOutput);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight);
  }, [output.length]);

  const handleEval = async () => {
    const expr = input.trim();
    if (!expr) return;
    appendConsoleOutput(`> ${expr}\n`);
    setInput("");

    if (status !== "paused") {
      appendConsoleOutput("Cannot evaluate: not paused\n");
      return;
    }

    try {
      const result = await nativeDebug.evaluate(expr, activeFrameId ?? undefined);
      appendConsoleOutput(`${result?.result ?? "undefined"}\n`);
    } catch (err: any) {
      appendConsoleOutput(`Error: ${err.message}\n`);
    }
  };

  return (
    <div className="h-full flex flex-col bg-[#1e1e1e]">
      <div className="flex items-center justify-between px-2 py-0.5 border-b border-[#3c3c3c] shrink-0">
        <span className="text-[11px] text-gray-400 font-medium">Debug Console</span>
        <button onClick={clearConsoleOutput} className="text-gray-500 hover:text-white" title="Clear">
          <Trash2 size={12} />
        </button>
      </div>
      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto px-2 py-1 font-mono text-[12px]">
        {output.map((line, i) => (
          <div
            key={i}
            className={`whitespace-pre-wrap break-all ${
              line.startsWith("> ") ? "text-blue-300" : line.startsWith("Error") ? "text-red-400" : "text-gray-300"
            }`}
          >
            {line}
          </div>
        ))}
      </div>
      <div className="flex items-center px-2 py-1 border-t border-[#3c3c3c] shrink-0">
        <span className="text-[12px] text-gray-500 mr-1">{">"}</span>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleEval()}
          placeholder={status === "paused" ? "Evaluate expression…" : "Start debugging to evaluate"}
          disabled={status !== "paused"}
          className="flex-1 bg-transparent text-[12px] text-gray-300 outline-none placeholder-gray-600 font-mono disabled:opacity-50"
        />
      </div>
    </div>
  );
}
