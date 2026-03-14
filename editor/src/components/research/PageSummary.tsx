import { useState, useCallback } from "react";
import {
  Sparkles,
  Loader2,
  FlaskConical,
  Tag,
  ImageIcon,
  ChevronDown,
  ChevronRight,
  Lightbulb,
  X,
} from "lucide-react";
import {
  useResearchStore,
  type PageSummaryData,
} from "../../store/useResearchStore";

// ---------------------------------------------------------------------------
// Summary card for a single page
// ---------------------------------------------------------------------------

function SummaryCard({
  summary,
  onClick,
}: {
  summary: PageSummaryData;
  onClick: () => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const hasContent =
    summary.claims.length > 0 ||
    summary.methods.length > 0 ||
    summary.keyTerms.length > 0 ||
    summary.figures.length > 0;

  if (!hasContent) return null;

  return (
    <div className="border border-gray-800 rounded-lg bg-[#1e1e1e] overflow-hidden">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-gray-800/40 transition-colors"
      >
        {expanded ? (
          <ChevronDown size={11} className="text-gray-600" />
        ) : (
          <ChevronRight size={11} className="text-gray-600" />
        )}
        <span className="text-[11px] font-medium text-gray-300">
          Page {summary.page}
        </span>
        <span className="text-[9px] text-gray-600 ml-auto">
          {new Date(summary.generatedAt).toLocaleDateString()}
        </span>
      </button>

      {expanded && (
        <div className="px-3 pb-2 space-y-2">
          {summary.claims.length > 0 && (
            <SummarySection
              icon={<Lightbulb size={10} className="text-yellow-400" />}
              title="Claims"
              items={summary.claims}
              color="text-yellow-400/80"
              onClick={onClick}
            />
          )}
          {summary.methods.length > 0 && (
            <SummarySection
              icon={<FlaskConical size={10} className="text-blue-400" />}
              title="Methods"
              items={summary.methods}
              color="text-blue-400/80"
              onClick={onClick}
            />
          )}
          {summary.keyTerms.length > 0 && (
            <div className="flex items-start gap-1.5">
              <Tag size={10} className="text-green-400 mt-0.5 shrink-0" />
              <div className="flex flex-wrap gap-1">
                {summary.keyTerms.map((term, i) => (
                  <span
                    key={i}
                    className="text-[9px] px-1.5 py-0.5 bg-green-900/20 text-green-400/80 rounded"
                  >
                    {term}
                  </span>
                ))}
              </div>
            </div>
          )}
          {summary.figures.length > 0 && (
            <SummarySection
              icon={<ImageIcon size={10} className="text-purple-400" />}
              title="Figures/Tables"
              items={summary.figures}
              color="text-purple-400/80"
              onClick={onClick}
            />
          )}
        </div>
      )}
    </div>
  );
}

function SummarySection({
  icon,
  title,
  items,
  color,
  onClick,
}: {
  icon: React.ReactNode;
  title: string;
  items: string[];
  color: string;
  onClick: () => void;
}) {
  return (
    <div>
      <div className="flex items-center gap-1 mb-0.5">
        {icon}
        <span className={`text-[9px] font-medium uppercase tracking-wider ${color}`}>
          {title}
        </span>
      </div>
      <ul className="space-y-0.5">
        {items.map((item, i) => (
          <li
            key={i}
            className="text-[10px] text-gray-400 pl-4 leading-relaxed cursor-pointer hover:text-gray-300"
            onClick={onClick}
          >
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component — collapsible panel of per-page summaries
// ---------------------------------------------------------------------------

export default function PageSummaryPanel({
  paperId,
  currentPage,
  numPages,
  onJumpToPage,
}: {
  paperId: string;
  currentPage: number;
  numPages: number;
  onJumpToPage: (page: number) => void;
}) {
  const pageSummaries = useResearchStore((s) => s.pageSummaries);
  const setPageSummary = useResearchStore((s) => s.setPageSummary);
  const [generatingPage, setGeneratingPage] = useState<number | null>(null);

  const summaryKey = (page: number) => `${paperId}:${page}`;
  const currentSummary = pageSummaries[summaryKey(currentPage)];

  const allSummaries = Array.from({ length: numPages }, (_, i) => i + 1)
    .map((page) => pageSummaries[summaryKey(page)])
    .filter(Boolean);

  const generateSummary = useCallback(
    async (page: number) => {
      setGeneratingPage(page);
      try {
        const resp = await fetch("/api/chat/editor/message", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message: `Summarize page ${page} of the current PDF. Return a JSON object with these fields: claims (array of key claims), methods (array of methodology notes), keyTerms (array of important terms), figures (array of figures/tables referenced). Keep each item concise (1 sentence max). Return ONLY valid JSON.`,
            mode: "auto",
            thread_id: null,
          }),
        });

        if (!resp.ok) {
          setGeneratingPage(null);
          return;
        }

        const data = await resp.json();
        const raw =
          typeof data.response === "string"
            ? data.response
            : typeof data.content === "string"
              ? data.content
              : null;

        let parsed: Partial<PageSummaryData> = {};
        if (raw) {
          try {
            const jsonStr = raw.replace(/```json?\s*/g, "").replace(/```/g, "").trim();
            parsed = JSON.parse(jsonStr);
          } catch {
            parsed = { claims: [raw.slice(0, 200)] };
          }
        }

        setPageSummary(summaryKey(page), {
          paperId,
          page,
          claims: parsed.claims ?? [],
          methods: parsed.methods ?? [],
          keyTerms: parsed.keyTerms ?? [],
          figures: parsed.figures ?? [],
          generatedAt: Date.now(),
        });
      } catch {
        // silently fail
      } finally {
        setGeneratingPage(null);
      }
    },
    [paperId, setPageSummary],
  );

  return (
    <div className="w-64 border-l border-gray-800 bg-[#1a1a1a] flex flex-col h-full shrink-0">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-800">
        <div className="flex items-center gap-1.5">
          <Sparkles size={12} className="text-purple-400" />
          <span className="text-xs text-gray-300 font-medium">Page Summaries</span>
        </div>
        <span className="text-[9px] text-gray-600">
          {allSummaries.length}/{numPages} pages
        </span>
      </div>

      {/* Current page summary */}
      <div className="px-2 py-2 border-b border-gray-800">
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-[10px] text-gray-500 font-medium">
            Current: Page {currentPage}
          </span>
          {!currentSummary && (
            <button
              onClick={() => generateSummary(currentPage)}
              disabled={generatingPage !== null}
              className="flex items-center gap-1 px-2 py-0.5 text-[9px] text-purple-400 bg-purple-900/20 rounded hover:bg-purple-900/30 disabled:opacity-40 transition-colors"
            >
              {generatingPage === currentPage ? (
                <Loader2 size={9} className="animate-spin" />
              ) : (
                <Sparkles size={9} />
              )}
              Summarize
            </button>
          )}
        </div>
        {currentSummary ? (
          <SummaryCard
            summary={currentSummary}
            onClick={() => onJumpToPage(currentPage)}
          />
        ) : (
          <div className="text-center py-4 text-gray-600">
            <Sparkles size={16} className="mx-auto mb-1 opacity-40" />
            <p className="text-[10px]">No summary for this page</p>
          </div>
        )}
      </div>

      {/* All summaries list */}
      <div className="flex-1 overflow-y-auto px-2 py-2 space-y-2">
        {allSummaries.length === 0 ? (
          <div className="text-center py-6 text-gray-600 text-[10px]">
            Generate summaries for individual pages using the button above
          </div>
        ) : (
          allSummaries.map((s) => (
            <SummaryCard
              key={`${s.paperId}:${s.page}`}
              summary={s}
              onClick={() => onJumpToPage(s.page)}
            />
          ))
        )}
      </div>
    </div>
  );
}
