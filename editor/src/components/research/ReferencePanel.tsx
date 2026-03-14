import { useState, useMemo, useCallback } from "react";
import {
  Plus,
  Download,
  Trash2,
  ChevronDown,
  Search,
  ExternalLink,
  BookOpen,
  Sparkles,
} from "lucide-react";
import {
  useResearchStore,
  type ResearchPaper,
} from "../../store/useResearchStore";
import { nativeDialog } from "../../lib/electronBridge";

type SortKey = "added" | "year" | "title";
type CitationFormat = "apa" | "chicago" | "harvard";

// ---------------------------------------------------------------------------
// Citation formatters
// ---------------------------------------------------------------------------

function formatCitation(paper: ResearchPaper, style: CitationFormat): string {
  const authors = paper.authors.length
    ? paper.authors.join(", ")
    : "Unknown Author";
  switch (style) {
    case "apa":
      return `${authors} (${paper.year}). ${paper.title}.${paper.doi ? ` https://doi.org/${paper.doi}` : ""}`;
    case "chicago":
      return `${authors}. "${paper.title}." ${paper.year}.${paper.doi ? ` https://doi.org/${paper.doi}` : ""}`;
    case "harvard":
      return `${authors} ${paper.year}, '${paper.title}'.${paper.doi ? ` doi:${paper.doi}` : ""}`;
  }
}

// ---------------------------------------------------------------------------
// BibTeX export
// ---------------------------------------------------------------------------

function generateBibtex(papers: ResearchPaper[]): string {
  return papers
    .map((p) => {
      const key = `${(p.authors[0] ?? "unknown").split(" ").pop()?.toLowerCase() ?? "unknown"}${p.year}`;
      const fields = [
        `  title = {${p.title}}`,
        `  author = {${p.authors.join(" and ") || "Unknown"}}`,
        `  year = {${p.year}}`,
      ];
      if (p.doi) fields.push(`  doi = {${p.doi}}`);
      if (p.abstract) fields.push(`  abstract = {${p.abstract}}`);
      return `@article{${key},\n${fields.join(",\n")}\n}`;
    })
    .join("\n\n");
}

function downloadFile(filename: string, content: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// StatusDot
// ---------------------------------------------------------------------------

function StatusDot({ status }: { status: string }) {
  const colors: Record<string, string> = {
    unread: "bg-blue-500",
    reading: "bg-yellow-500",
    read: "bg-green-500",
  };
  return (
    <div
      className={`w-2 h-2 rounded-full shrink-0 ${colors[status] ?? "bg-gray-600"}`}
      title={status}
    />
  );
}

// ---------------------------------------------------------------------------
// ReferenceItem
// ---------------------------------------------------------------------------

function ReferenceItem({
  paper,
  citationFormat,
}: {
  paper: ResearchPaper;
  citationFormat: CitationFormat;
}) {
  const [expanded, setExpanded] = useState(false);

  const handleClick = useCallback(() => {
    useResearchStore.getState().setActivePaper(paper.id);
    useResearchStore.getState().setPrimaryTab("reader");
  }, [paper.id]);

  const handleRemove = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      useResearchStore.getState().removePaper(paper.id);
    },
    [paper.id],
  );

  const handleFindSimilar = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      window.dispatchEvent(
        new CustomEvent("persistent-chat:send", {
          detail: {
            message: `/search Find papers similar to "${paper.title}" by ${paper.authors.join(", ")} (${paper.year})`,
          },
        }),
      );
    },
    [paper],
  );

  const handleToggleExpand = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      setExpanded((v) => !v);
    },
    [],
  );

  return (
    <div className="px-3 py-2 border-b border-gray-800/30 hover:bg-gray-800/30 group">
      <div className="flex items-start gap-2">
        <div className="mt-1">
          <StatusDot status={paper.status} />
        </div>
        <div className="flex-1 min-w-0">
          <p
            className="text-xs text-gray-200 cursor-pointer hover:text-blue-400 leading-snug"
            onClick={handleClick}
          >
            {paper.title}
          </p>
          <p className="text-[10px] text-gray-500 mt-0.5">
            {paper.authors.length
              ? paper.authors.join(", ")
              : "Unknown Author"}{" "}
            ({paper.year})
          </p>
          {paper.doi && (
            <p className="text-[10px] text-blue-600 mt-0.5 truncate">
              {paper.doi}
            </p>
          )}
          {expanded && (
            <div className="mt-1.5 space-y-1">
              {paper.abstract && (
                <p className="text-[10px] text-gray-500 leading-relaxed">
                  {paper.abstract}
                </p>
              )}
              <p className="text-[10px] text-gray-600 italic select-all">
                {formatCitation(paper, citationFormat)}
              </p>
            </div>
          )}
        </div>
        <div className="flex gap-0.5 opacity-0 group-hover:opacity-100 shrink-0">
          <button
            onClick={handleFindSimilar}
            className="p-0.5 text-gray-500 hover:text-purple-400 cursor-pointer"
            title="Find similar papers"
          >
            <Sparkles size={12} />
          </button>
          {paper.doi && (
            <a
              href={`https://doi.org/${paper.doi}`}
              target="_blank"
              rel="noopener noreferrer"
              className="p-0.5 text-gray-500 hover:text-blue-400"
              title="Open DOI"
              onClick={(e) => e.stopPropagation()}
            >
              <ExternalLink size={12} />
            </a>
          )}
          <button
            onClick={handleToggleExpand}
            className="p-0.5 text-gray-500 hover:text-gray-300 cursor-pointer"
            title={expanded ? "Collapse" : "Expand"}
          >
            <ChevronDown
              size={12}
              className={`transition-transform ${expanded ? "rotate-180" : ""}`}
            />
          </button>
          <button
            onClick={handleRemove}
            className="p-0.5 text-gray-500 hover:text-red-400 cursor-pointer"
            title="Remove reference"
          >
            <Trash2 size={12} />
          </button>
        </div>
      </div>
      {paper.tags.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1 ml-5">
          {paper.tags.map((t) => (
            <span
              key={t}
              className="text-[9px] px-1.5 py-0.5 bg-gray-800 text-gray-500 rounded"
            >
              #{t}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function ReferencePanel() {
  const papers = useResearchStore((s) => s.papers);
  const [searchQuery, setSearchQuery] = useState("");
  const [sortBy, setSortBy] = useState<SortKey>("added");
  const [citationFormat, setCitationFormat] = useState<CitationFormat>("apa");

  const filtered = useMemo(() => {
    let result = papers;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      result = result.filter(
        (p) =>
          p.title.toLowerCase().includes(q) ||
          p.authors.some((a) => a.toLowerCase().includes(q)) ||
          String(p.year).includes(q) ||
          p.tags.some((t) => t.toLowerCase().includes(q)),
      );
    }
    return [...result].sort((a, b) => {
      if (sortBy === "year") return b.year - a.year;
      if (sortBy === "title") return a.title.localeCompare(b.title);
      return b.addedAt - a.addedAt;
    });
  }, [papers, searchQuery, sortBy]);

  const handleAddReference = useCallback(async () => {
    const paths = await nativeDialog.openFile({
      filters: [{ name: "PDF", extensions: ["pdf"] }],
      multiple: true,
    });
    if (!paths) return;
    for (const filePath of paths) {
      const name = filePath.split("/").pop()?.replace(/\.pdf$/i, "") ?? "Untitled";
      useResearchStore.getState().addPaper({
        title: name,
        authors: [],
        year: new Date().getFullYear(),
        filePath,
        status: "unread",
        tags: [],
      });
    }
  }, []);

  const handleExportBibtex = useCallback(() => {
    const bibtex = generateBibtex(papers);
    downloadFile("references.bib", bibtex, "text/plain");
  }, [papers]);

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="px-3 py-2 border-b border-gray-800 shrink-0">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-semibold text-gray-300">
            References ({papers.length})
          </span>
          <div className="flex gap-1">
            <button
              onClick={handleAddReference}
              className="p-1 text-gray-400 hover:text-green-400 rounded cursor-pointer"
              title="Add reference"
            >
              <Plus size={14} />
            </button>
            <button
              onClick={handleExportBibtex}
              disabled={papers.length === 0}
              className="p-1 text-gray-400 hover:text-blue-400 rounded disabled:opacity-30 cursor-pointer disabled:cursor-default"
              title="Export BibTeX"
            >
              <Download size={14} />
            </button>
          </div>
        </div>
        <div className="relative">
          <Search
            size={12}
            className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-500"
          />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search references..."
            className="w-full bg-gray-800 border border-gray-700 rounded pl-6 pr-2 py-1 text-[11px] text-gray-200 placeholder-gray-500"
          />
        </div>
      </div>

      {/* Sort controls */}
      <div className="px-3 py-1 flex items-center gap-2 border-b border-gray-800/50 shrink-0">
        <span className="text-[10px] text-gray-600">Sort:</span>
        {(["added", "year", "title"] as const).map((s) => (
          <button
            key={s}
            onClick={() => setSortBy(s)}
            className={`text-[10px] cursor-pointer ${sortBy === s ? "text-blue-400" : "text-gray-500 hover:text-gray-300"}`}
          >
            {s}
          </button>
        ))}
      </div>

      {/* Reference list */}
      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 ? (
          <div className="p-4 text-center">
            <BookOpen size={24} className="mx-auto mb-2 text-gray-700" />
            <p className="text-xs text-gray-600">
              {papers.length === 0
                ? "No references yet. Add papers by PDF upload or let DAN discover them."
                : "No references match your search."}
            </p>
          </div>
        ) : (
          filtered.map((paper) => (
            <ReferenceItem
              key={paper.id}
              paper={paper}
              citationFormat={citationFormat}
            />
          ))
        )}
      </div>

      {/* Citation format selector */}
      <div className="px-3 py-1.5 border-t border-gray-800 flex items-center justify-between shrink-0">
        <span className="text-[10px] text-gray-600">Citation style:</span>
        <select
          value={citationFormat}
          onChange={(e) => setCitationFormat(e.target.value as CitationFormat)}
          className="bg-gray-800 border border-gray-700 rounded px-1.5 py-0.5 text-[10px] text-gray-300"
        >
          <option value="apa">APA</option>
          <option value="chicago">Chicago</option>
          <option value="harvard">Harvard</option>
        </select>
      </div>
    </div>
  );
}
