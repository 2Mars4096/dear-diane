import { useState, useCallback } from "react";
import {
  Image as ImageIcon,
  Upload,
  X,
  ZoomIn,
  ZoomOut,
  FileDown,
  Trash2,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import {
  appendAttachmentToModeChat,
} from "../shared/ModeChatSidebar";
import {
  figureToAttachmentDraft,
} from "../../lib/editorChat";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface ResearchFigure {
  id: string;
  title: string;
  caption: string;
  src: string;
  path?: string;
  cellId?: string;
  createdAt: number;
}

let figCounter = 1;
function figId() {
  return `fig-${Date.now()}-${figCounter++}`;
}

/* ------------------------------------------------------------------ */
/*  Sample data for demo                                               */
/* ------------------------------------------------------------------ */

const SAMPLE_FIGURES: ResearchFigure[] = [
  {
    id: figId(),
    title: "Figure 1: Model Architecture",
    caption: "Overview of the transformer architecture used in the study.",
    src: "data:image/svg+xml," + encodeURIComponent(
      `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200" viewBox="0 0 320 200">
        <rect width="320" height="200" fill="#1a1a2e"/>
        <rect x="40" y="140" width="40" height="40" fill="#7c3aed" rx="4"/>
        <rect x="100" y="100" width="40" height="80" fill="#6d28d9" rx="4"/>
        <rect x="160" y="60" width="40" height="120" fill="#5b21b6" rx="4"/>
        <rect x="220" y="30" width="40" height="150" fill="#4c1d95" rx="4"/>
        <text x="160" y="18" fill="#9ca3af" font-size="12" text-anchor="middle" font-family="sans-serif">Model Performance</text>
      </svg>`,
    ),
    createdAt: Date.now() - 3600_000,
  },
  {
    id: figId(),
    title: "Figure 2: Loss Curve",
    caption: "Training and validation loss over 50 epochs.",
    src: "data:image/svg+xml," + encodeURIComponent(
      `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200" viewBox="0 0 320 200">
        <rect width="320" height="200" fill="#1a1a2e"/>
        <polyline points="20,160 80,100 140,70 200,50 260,40 300,38" fill="none" stroke="#7c3aed" stroke-width="2"/>
        <polyline points="20,170 80,120 140,95 200,85 260,80 300,78" fill="none" stroke="#ef4444" stroke-width="2" stroke-dasharray="4"/>
        <text x="160" y="18" fill="#9ca3af" font-size="12" text-anchor="middle" font-family="sans-serif">Training Progress</text>
      </svg>`,
    ),
    createdAt: Date.now() - 1800_000,
  },
];

/* ------------------------------------------------------------------ */
/*  Lightbox                                                           */
/* ------------------------------------------------------------------ */

function FigureLightbox({
  figure,
  figures,
  onClose,
  onNavigate,
}: {
  figure: ResearchFigure;
  figures: ResearchFigure[];
  onClose: () => void;
  onNavigate: (id: string) => void;
}) {
  const [zoom, setZoom] = useState(1);
  const idx = figures.findIndex((f) => f.id === figure.id);
  const hasPrev = idx > 0;
  const hasNext = idx < figures.length - 1;

  return (
    <div className="fixed inset-0 z-[100] bg-black/80 flex items-center justify-center" onClick={onClose}>
      <div
        className="relative max-w-[90vw] max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Toolbar */}
        <div className="flex items-center justify-between px-4 py-2 bg-gray-900 rounded-t-lg">
          <span className="text-sm text-gray-200 font-medium truncate">{figure.title}</span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setZoom((z) => Math.max(0.25, z - 0.25))}
              className="text-gray-400 hover:text-gray-200 transition-colors"
            >
              <ZoomOut size={16} />
            </button>
            <span className="text-[10px] text-gray-500 w-10 text-center">
              {Math.round(zoom * 100)}%
            </span>
            <button
              onClick={() => setZoom((z) => Math.min(3, z + 0.25))}
              className="text-gray-400 hover:text-gray-200 transition-colors"
            >
              <ZoomIn size={16} />
            </button>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-200 ml-2 transition-colors">
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Image */}
        <div className="flex-1 bg-gray-950 flex items-center justify-center overflow-auto p-4 min-h-[300px]">
          {hasPrev && (
            <button
              className="absolute left-2 top-1/2 -translate-y-1/2 p-1.5 bg-gray-800/80 rounded-full text-gray-300 hover:bg-gray-700 transition-colors"
              onClick={() => onNavigate(figures[idx - 1].id)}
            >
              <ChevronLeft size={18} />
            </button>
          )}
          <img
            src={figure.src}
            alt={figure.caption}
            className="max-w-full max-h-full object-contain transition-transform"
            style={{ transform: `scale(${zoom})` }}
          />
          {hasNext && (
            <button
              className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 bg-gray-800/80 rounded-full text-gray-300 hover:bg-gray-700 transition-colors"
              onClick={() => onNavigate(figures[idx + 1].id)}
            >
              <ChevronRight size={18} />
            </button>
          )}
        </div>

        {/* Caption */}
        <div className="px-4 py-2 bg-gray-900 rounded-b-lg">
          <p className="text-xs text-gray-400">{figure.caption}</p>
          {figure.cellId && (
            <p className="text-[10px] text-gray-600 mt-0.5">
              From code cell {figure.cellId}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Figure card                                                        */
/* ------------------------------------------------------------------ */

function FigureCard({
  figure,
  onSelect,
  onDelete,
}: {
  figure: ResearchFigure;
  onSelect: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      className="border border-gray-800 rounded overflow-hidden cursor-pointer hover:border-gray-600 group transition-colors"
      onClick={onSelect}
    >
      <div className="aspect-video bg-gray-900 flex items-center justify-center overflow-hidden">
        <img
          src={figure.src}
          alt={figure.caption}
          className="max-h-full max-w-full object-contain"
        />
      </div>
      <div className="p-1.5">
        <p className="text-[10px] text-gray-300 truncate">{figure.title}</p>
        <p className="text-[9px] text-gray-600 truncate">{figure.caption}</p>
      </div>
      <div className="px-1.5 pb-1 flex items-center justify-between opacity-0 group-hover:opacity-100 transition-opacity">
        <div className="flex items-center gap-2">
          <button
            className="text-[9px] text-purple-400 hover:text-purple-300 flex items-center gap-0.5"
            onClick={(e) => {
              e.stopPropagation();
              appendAttachmentToModeChat(
                "research",
                figureToAttachmentDraft({
                  title: figure.title,
                  caption: figure.caption,
                  src: figure.src,
                  path: figure.path,
                  source: figure.cellId ? `code cell ${figure.cellId}` : "figure gallery",
                }),
              );
              window.dispatchEvent(
                new CustomEvent("persistent-chat:send", {
                  detail: {
                    message: `Please analyze the appended figure "${figure.title}" and explain the important takeaway.`,
                  },
                }),
              );
            }}
          >
            <ImageIcon size={10} /> Ask AI
          </button>
          <button
            className="text-[9px] text-purple-400 hover:text-purple-300 flex items-center gap-0.5"
            onClick={(e) => {
              e.stopPropagation();
              window.dispatchEvent(
                new CustomEvent("research:insert-figure", {
                  detail: { figureId: figure.id, src: figure.src, caption: figure.caption },
                }),
              );
            }}
          >
            <FileDown size={10} /> Insert into paper
          </button>
        </div>
        <button
          className="text-gray-600 hover:text-red-400 transition-colors"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
        >
          <Trash2 size={10} />
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export default function FigureGallery() {
  const [figures, setFigures] = useState<ResearchFigure[]>(SAMPLE_FIGURES);
  const [selectedFigure, setSelectedFigure] = useState<string | null>(null);

  const deleteFigure = useCallback((id: string) => {
    setFigures((prev) => prev.filter((f) => f.id !== id));
    if (selectedFigure === id) setSelectedFigure(null);
  }, [selectedFigure]);

  const handleUpload = useCallback(() => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.multiple = true;
    input.onchange = () => {
      if (!input.files) return;
      Array.from(input.files).forEach((file) => {
        const reader = new FileReader();
        reader.onload = () => {
          setFigures((prev) => [
            ...prev,
            {
              id: figId(),
              title: file.name.replace(/\.[^.]+$/, ""),
              caption: "",
              src: reader.result as string,
              path:
                typeof (file as File & { path?: string }).path === "string"
                  ? (file as File & { path?: string }).path
                  : undefined,
              createdAt: Date.now(),
            },
          ]);
        };
        reader.readAsDataURL(file);
      });
    };
    input.click();
  }, []);

  const selected = figures.find((f) => f.id === selectedFigure);

  return (
    <div className="h-full flex flex-col">
      <div className="px-3 py-1.5 border-b border-gray-800 bg-[#252526] flex items-center justify-between">
        <span className="text-xs text-gray-400">Figures ({figures.length})</span>
        <button
          onClick={handleUpload}
          className="flex items-center gap-1 text-[10px] text-gray-500 hover:text-gray-300 transition-colors"
        >
          <Upload size={12} /> Upload
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {figures.length === 0 ? (
          <div className="text-center text-xs text-gray-600 mt-8">
            <ImageIcon size={24} className="mx-auto mb-2 text-gray-700" />
            <p>No figures yet</p>
            <p className="mt-1 text-[10px]">
              Generate charts in code cells or upload images
            </p>
            <button
              onClick={handleUpload}
              className="mt-3 px-3 py-1 text-[10px] text-purple-400 border border-purple-500/30 rounded hover:bg-purple-900/20 transition-colors"
            >
              <Upload size={10} className="inline mr-1" />
              Upload image
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            {figures.map((fig) => (
              <FigureCard
                key={fig.id}
                figure={fig}
                onSelect={() => setSelectedFigure(fig.id)}
                onDelete={() => deleteFigure(fig.id)}
              />
            ))}
          </div>
        )}
      </div>

      {selected && (
        <FigureLightbox
          figure={selected}
          figures={figures}
          onClose={() => setSelectedFigure(null)}
          onNavigate={(id) => setSelectedFigure(id)}
        />
      )}
    </div>
  );
}
