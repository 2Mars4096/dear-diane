import { useCallback } from "react";
import { Allotment } from "allotment";
import { X, Columns2 } from "lucide-react";
import {
  useResearchStore,
  type ResearchPaper,
} from "../../store/useResearchStore";
import PdfReader from "./PdfReader";

function PaperSelector({
  value,
  onChange,
  papers,
  label,
}: {
  value: string | null;
  onChange: (id: string) => void;
  papers: ResearchPaper[];
  label: string;
}) {
  return (
    <select
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value)}
      className="bg-[#1e1e1e] border border-gray-700 rounded px-2 py-0.5 text-[10px] text-gray-300 outline-none focus:border-blue-500/50 max-w-[180px] truncate"
      title={label}
    >
      <option value="" disabled>
        {label}
      </option>
      {papers
        .filter((p) => p.filePath)
        .map((p) => (
          <option key={p.id} value={p.id}>
            {p.title}
          </option>
        ))}
    </select>
  );
}

export default function SplitPdfReader() {
  const papers = useResearchStore((s) => s.papers);
  const activePaperId = useResearchStore((s) => s.activePaperId);
  const setActivePaper = useResearchStore((s) => s.setActivePaper);
  const splitReaderActive = useResearchStore((s) => s.splitReaderActive);
  const setSplitReaderActive = useResearchStore((s) => s.setSplitReaderActive);
  const splitPaperId = useResearchStore((s) => s.splitPaperId);
  const setSplitPaperId = useResearchStore((s) => s.setSplitPaperId);

  const handleRequestSplit = useCallback(() => {
    setSplitReaderActive(true);
    if (!splitPaperId && papers.length > 1) {
      const other = papers.find((p) => p.id !== activePaperId && p.filePath);
      if (other) setSplitPaperId(other.id);
    }
  }, [papers, activePaperId, splitPaperId, setSplitReaderActive, setSplitPaperId]);

  const handleCloseSplit = useCallback(() => {
    setSplitReaderActive(false);
  }, [setSplitReaderActive]);

  if (!splitReaderActive) {
    return <PdfReader showSplitButton={true} onRequestSplit={handleRequestSplit} />;
  }

  return (
    <div className="h-full flex flex-col">
      {/* Split toolbar */}
      <div className="flex items-center justify-between px-2 py-1 border-b border-gray-800 bg-[#1e1e1e] shrink-0">
        <div className="flex items-center gap-2">
          <Columns2 size={12} className="text-blue-400" />
          <span className="text-[10px] text-gray-500">Split View</span>
          <PaperSelector
            value={activePaperId}
            onChange={(id) => setActivePaper(id)}
            papers={papers}
            label="Left paper"
          />
          <span className="text-gray-600 text-[10px]">|</span>
          <PaperSelector
            value={splitPaperId}
            onChange={(id) => setSplitPaperId(id)}
            papers={papers}
            label="Right paper"
          />
        </div>
        <button
          onClick={handleCloseSplit}
          className="p-1 text-gray-500 hover:text-gray-300 transition-colors"
          title="Close split view"
        >
          <X size={14} />
        </button>
      </div>

      {/* Split panes */}
      <div className="flex-1 min-h-0">
        <Allotment>
          <Allotment.Pane minSize={250}>
            <PdfReader
              paperId={activePaperId}
              showSplitButton={false}
            />
          </Allotment.Pane>
          <Allotment.Pane minSize={250}>
            <PdfReader
              paperId={splitPaperId}
              showSplitButton={false}
            />
          </Allotment.Pane>
        </Allotment>
      </div>
    </div>
  );
}
