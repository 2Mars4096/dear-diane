import type { ReactNode } from "react";
import {
  Search,
  FileText,
  Eye,
  GitCompare,
  FileOutput,
  Flame,
  GraduationCap,
} from "lucide-react";
import {
  useResearchStore,
  type PipelineStage,
} from "../../store/useResearchStore";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface QuickStart {
  id: string;
  title: string;
  description: string;
  icon: ReactNode;
  stages: PipelineStage[];
  initialContent?: string;
  action: (store: ReturnType<typeof useResearchStore.getState>) => void;
}

/* ------------------------------------------------------------------ */
/*  Quick-start action helpers                                         */
/* ------------------------------------------------------------------ */

function dispatchResearchCommand(command: string) {
  window.dispatchEvent(
    new CustomEvent("persistent-chat:send", {
      detail: { message: command },
    }),
  );
}

function pickPdf(): Promise<File | null> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".pdf";
    input.onchange = (e: Event) => {
      const file = (e.target as HTMLInputElement).files?.[0] ?? null;
      resolve(file);
    };
    input.click();
  });
}

function pickMultiplePdfs(): Promise<File[]> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".pdf";
    input.multiple = true;
    input.onchange = (e: Event) => {
      const files = Array.from(
        (e.target as HTMLInputElement).files ?? [],
      );
      resolve(files);
    };
    input.click();
  });
}

function startLiteratureReview(
  store: ReturnType<typeof useResearchStore.getState>,
) {
  const topic = prompt("Enter your research topic:");
  if (!topic) return;
  dispatchResearchCommand(`/research literature-review "${topic}"`);
  store.setPrimaryTab("editor");
  if (!store.showPipeline) store.togglePipeline();
}

function startNewPaper(
  store: ReturnType<typeof useResearchStore.getState>,
) {
  const topic = prompt("Enter your paper topic or abstract:");
  if (!topic) return;
  dispatchResearchCommand(`/research new-paper "${topic}"`);
  store.setPrimaryTab("editor");
}

async function startReviewPaper(
  store: ReturnType<typeof useResearchStore.getState>,
) {
  const file = await pickPdf();
  if (!file) return;
  const filePath = (file as unknown as { path?: string }).path ?? file.name;
  store.addPaper({
    title: file.name.replace(/\.pdf$/i, ""),
    authors: [],
    year: new Date().getFullYear(),
    filePath,
    status: "unread",
    tags: ["review-target"],
  });
  store.setPrimaryTab("reader");
  dispatchResearchCommand(`/research review-paper "${file.name}"`);
}

async function startComparePapers(
  store: ReturnType<typeof useResearchStore.getState>,
) {
  const files = await pickMultiplePdfs();
  if (files.length < 2) {
    if (files.length === 1) alert("Please select at least 2 PDFs to compare.");
    return;
  }
  for (const file of files) {
    const filePath = (file as unknown as { path?: string }).path ?? file.name;
    store.addPaper({
      title: file.name.replace(/\.pdf$/i, ""),
      authors: [],
      year: new Date().getFullYear(),
      filePath,
      status: "unread",
      tags: ["compare"],
    });
  }
  const names = files.map((f) => f.name).join('", "');
  dispatchResearchCommand(`/research compare-papers "${names}"`);
  store.setPrimaryTab("reader");
}

async function startSummarizePaper(
  store: ReturnType<typeof useResearchStore.getState>,
) {
  const file = await pickPdf();
  if (!file) return;
  const filePath = (file as unknown as { path?: string }).path ?? file.name;
  store.addPaper({
    title: file.name.replace(/\.pdf$/i, ""),
    authors: [],
    year: new Date().getFullYear(),
    filePath,
    status: "unread",
    tags: ["summarize"],
  });
  store.setPrimaryTab("reader");
  dispatchResearchCommand(`/research summarize-paper "${file.name}"`);
}

function startLearn100(
  store: ReturnType<typeof useResearchStore.getState>,
) {
  const domain = prompt("Enter the domain or topic to learn (e.g., 'supply chain resilience'):");
  if (!domain) return;
  dispatchResearchCommand(`/research learn-100 "${domain}"`);
  store.setContextTab("distillation");
  if (!store.showPipeline) store.togglePipeline();
}

/* ------------------------------------------------------------------ */
/*  Quick-start definitions                                            */
/* ------------------------------------------------------------------ */

export const QUICK_STARTS: QuickStart[] = [
  {
    id: "literature-review",
    title: "New Literature Review",
    description:
      "Provide a topic \u2192 auto-creates search \u2192 read \u2192 synthesize \u2192 write workflow",
    icon: <Search size={20} className="text-blue-400" />,
    stages: [
      { id: "search", label: "Search", status: "queued" },
      { id: "read", label: "Read Papers", status: "queued" },
      { id: "synthesize", label: "Synthesize", status: "queued" },
      { id: "write", label: "Write Review", status: "queued" },
      { id: "revise", label: "Revise", status: "queued" },
    ],
    initialContent:
      "# Literature Review\n\n## Introduction\n\n## Search Strategy\n\n## Key Themes\n\n## Discussion\n\n## Conclusion\n\n## References\n",
    action: startLiteratureReview,
  },
  {
    id: "new-paper",
    title: "New Paper",
    description:
      "From topic/abstract \u2192 outline, parallel write, review workflow",
    icon: <FileText size={20} className="text-green-400" />,
    stages: [
      { id: "outline", label: "Outline", status: "queued" },
      { id: "write-intro", label: "Write Intro", status: "queued" },
      { id: "write-body", label: "Write Body", status: "queued" },
      { id: "write-conclusion", label: "Write Conclusion", status: "queued" },
      { id: "review", label: "Review", status: "queued" },
      { id: "finalize", label: "Finalize", status: "queued" },
    ],
    initialContent:
      "# Research Paper Title\n\n## Abstract\n\n## 1. Introduction\n\n## 2. Literature Review\n\n## 3. Methodology\n\n## 4. Results\n\n## 5. Discussion\n\n## 6. Conclusion\n\n## References\n",
    action: startNewPaper,
  },
  {
    id: "review-paper",
    title: "Review This Paper",
    description:
      "Upload a PDF \u2192 structured review: methodology, contribution, weakness, questions",
    icon: <Eye size={20} className="text-yellow-400" />,
    stages: [
      { id: "ingest", label: "Ingest PDF", status: "queued" },
      { id: "read", label: "Read Paper", status: "queued" },
      { id: "review", label: "Generate Review", status: "queued" },
    ],
    action: startReviewPaper,
  },
  {
    id: "compare-papers",
    title: "Compare Papers",
    description:
      "Upload 2+ PDFs \u2192 structured comparison matrix: methodology, findings, limitations",
    icon: <GitCompare size={20} className="text-purple-400" />,
    stages: [
      { id: "ingest", label: "Ingest PDFs", status: "queued" },
      { id: "read", label: "Read Papers", status: "queued" },
      { id: "compare", label: "Compare", status: "queued" },
      { id: "matrix", label: "Build Matrix", status: "queued" },
    ],
    action: startComparePapers,
  },
  {
    id: "summarize-paper",
    title: "Summarize Paper",
    description: "Upload a PDF \u2192 one-page structured summary",
    icon: <FileOutput size={20} className="text-cyan-400" />,
    stages: [
      { id: "ingest", label: "Ingest", status: "queued" },
      { id: "summarize", label: "Summarize", status: "queued" },
    ],
    action: startSummarizePaper,
  },
  {
    id: "learn-100",
    title: "Learn 100 Papers",
    description:
      "Long-running domain learning: discover \u2192 ingest \u2192 extract \u2192 distill \u2192 evaluate",
    icon: <Flame size={20} className="text-orange-400" />,
    stages: [
      { id: "discover", label: "Discover", status: "queued" },
      { id: "ingest", label: "Ingest", status: "queued" },
      { id: "extract", label: "Extract", status: "queued" },
      { id: "distill", label: "Distill", status: "queued" },
      { id: "evaluate", label: "Evaluate", status: "queued" },
    ],
    action: startLearn100,
  },
];

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function QuickStartPanel({
  onClose,
  visibleIds,
}: {
  onClose?: () => void;
  visibleIds?: string[];
}) {
  const store = useResearchStore();

  const filtered = visibleIds
    ? QUICK_STARTS.filter((qs) => visibleIds.includes(qs.id))
    : QUICK_STARTS;

  const handleSelect = (qs: QuickStart) => {
    store.setPipeline(qs.stages);
    store.setActiveQuickStart(qs.id);
    if (qs.initialContent) store.setDocumentContent(qs.initialContent);
    qs.action(useResearchStore.getState());
    onClose?.();
  };

  return (
    <div className="h-full flex items-center justify-center">
      <div className="max-w-2xl w-full px-6">
        <div className="text-center mb-8">
          <GraduationCap size={40} className="text-purple-400 mx-auto mb-4" />
          <h2 className="text-xl font-semibold text-gray-200">
            Research Workspace
          </h2>
          <p className="text-sm text-gray-400 mt-2">
            Choose a starting point, drag a PDF, or start from scratch
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3 mb-6">
          {filtered.map((qs) => (
            <button
              key={qs.id}
              onClick={() => handleSelect(qs)}
              className="flex items-start gap-3 p-4 bg-gray-800/50 border border-gray-700/50 rounded-lg hover:border-purple-500/30 hover:bg-purple-900/10 text-left transition-all duration-200 group"
            >
              <span className="mt-0.5 group-hover:scale-110 transition-transform">
                {qs.icon}
              </span>
              <div>
                <p className="text-sm text-gray-200 font-medium">{qs.title}</p>
                <p className="text-[11px] text-gray-500 mt-1 leading-relaxed">
                  {qs.description}
                </p>
              </div>
            </button>
          ))}
        </div>

        <div className="text-center">
          <button
            onClick={() => {
              store.setDocumentContent("# New Document\n\n");
              store.setActiveQuickStart(null);
              onClose?.();
            }}
            className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
          >
            or start with a blank document
          </button>
        </div>
      </div>
    </div>
  );
}
