import { useState, useEffect, useMemo, useCallback } from "react";
import { ListTree, Plus, Sparkles } from "lucide-react";
import { useResearchStore } from "../../store/useResearchStore";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface OutlineSection {
  level: number;
  title: string;
  lineNumber: number;
  wordCount: number;
  status: "draft" | "review" | "final";
}

const STATUS_COLORS: Record<OutlineSection["status"], string> = {
  draft: "bg-gray-600",
  review: "bg-yellow-500",
  final: "bg-green-500",
};

// ---------------------------------------------------------------------------
// OutlinePanel (default export)
// ---------------------------------------------------------------------------

export default function OutlinePanel() {
  const content = useResearchStore((s) => s.documentContent);
  const [sections, setSections] = useState<OutlineSection[]>([]);
  const [activeLineNumber, setActiveLineNumber] = useState<number | null>(null);

  useEffect(() => {
    const lines = content.split("\n");
    const secs: OutlineSection[] = [];

    for (let i = 0; i < lines.length; i++) {
      const match = lines[i].match(/^(#{1,4})\s+(.+)/);
      if (match) {
        secs.push({
          level: match[1].length,
          title: match[2],
          lineNumber: i + 1,
          wordCount: 0,
          status: "draft",
        });
      }
    }

    for (let i = 0; i < secs.length; i++) {
      const start = secs[i].lineNumber;
      const end =
        i + 1 < secs.length ? secs[i + 1].lineNumber : lines.length;
      const text = lines.slice(start, end).join(" ");
      secs[i].wordCount = text.split(/\s+/).filter(Boolean).length;
    }

    setSections(secs);
  }, [content]);

  const totalWords = useMemo(
    () => content.split(/\s+/).filter(Boolean).length,
    [content],
  );

  const scrollToLine = useCallback((line: number) => {
    setActiveLineNumber(line);
    // Future: dispatch to Monaco editor scrollToLine
  }, []);

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="px-3 py-2 border-b border-gray-800 flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <ListTree size={13} className="text-gray-500" />
          <span className="text-xs font-semibold text-gray-300">Outline</span>
        </div>
        <button className="flex items-center gap-1 text-[10px] text-purple-400 hover:text-purple-300">
          <Sparkles size={11} />
          Generate
        </button>
      </div>

      {/* Section tree */}
      <div className="flex-1 overflow-y-auto py-1">
        {sections.length === 0 ? (
          <EmptyOutline />
        ) : (
          sections.map((sec, i) => (
            <button
              key={i}
              onClick={() => scrollToLine(sec.lineNumber)}
              className={`w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-gray-800/50 cursor-pointer group ${
                activeLineNumber === sec.lineNumber
                  ? "bg-gray-800/40"
                  : ""
              }`}
              style={{ paddingLeft: `${(sec.level - 1) * 12 + 12}px` }}
            >
              <div
                className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${STATUS_COLORS[sec.status]}`}
              />
              <span className="text-[11px] text-gray-300 flex-1 truncate">
                {sec.title}
              </span>
              <span className="text-[9px] text-gray-600 group-hover:text-gray-400 flex-shrink-0">
                {sec.wordCount}w
              </span>
            </button>
          ))
        )}
      </div>

      {/* Footer stats */}
      <div className="px-3 py-1.5 border-t border-gray-800 flex items-center justify-between text-[10px] text-gray-500">
        <span>
          {totalWords} words • {sections.length} sections
        </span>
        <button
          className="flex items-center gap-1 text-gray-500 hover:text-gray-300"
          title="Add section"
        >
          <Plus size={11} />
          <span>Add section</span>
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyOutline() {
  return (
    <div className="flex flex-col items-center justify-center h-full p-4 text-center gap-2">
      <ListTree size={28} className="text-gray-700" />
      <p className="text-xs text-gray-600">
        Add headings (<code className="text-gray-500">## Section</code>) to your
        document to see the outline.
      </p>
      <button className="mt-1 flex items-center gap-1 text-[10px] text-purple-400 hover:text-purple-300">
        <Sparkles size={11} />
        Generate outline from topic
      </button>
    </div>
  );
}
