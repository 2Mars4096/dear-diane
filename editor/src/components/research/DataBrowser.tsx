import { useState, useCallback } from "react";
import {
  Database,
  FileSpreadsheet,
  Upload,
  Trash2,
  Eye,
  X,
  FileJson,
  FileText,
  File,
  ArrowUpDown,
  Search,
} from "lucide-react";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface DataFile {
  id: string;
  name: string;
  path: string;
  type: "csv" | "json" | "xlsx" | "other";
  rows?: number;
  columns?: number;
  size: number;
  addedAt: number;
}

interface PreviewData {
  headers: string[];
  rows: string[][];
  totalRows: number;
}

let fileCounter = 1;
function fileId() {
  return `df-${Date.now()}-${fileCounter++}`;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fileIcon(type: DataFile["type"]) {
  switch (type) {
    case "csv":
      return <FileSpreadsheet size={14} className="text-green-500 shrink-0" />;
    case "json":
      return <FileJson size={14} className="text-yellow-500 shrink-0" />;
    case "xlsx":
      return <FileSpreadsheet size={14} className="text-blue-500 shrink-0" />;
    default:
      return <File size={14} className="text-gray-500 shrink-0" />;
  }
}

function mockPreview(file: DataFile): PreviewData {
  if (file.type === "csv" || file.type === "xlsx") {
    return {
      headers: ["id", "name", "value", "category", "date"],
      rows: [
        ["1", "Alpha", "0.854", "Group A", "2024-01-15"],
        ["2", "Beta", "0.421", "Group B", "2024-02-20"],
        ["3", "Gamma", "0.973", "Group A", "2024-03-10"],
        ["4", "Delta", "0.312", "Group C", "2024-04-05"],
        ["5", "Epsilon", "0.667", "Group B", "2024-05-12"],
        ["6", "Zeta", "0.189", "Group A", "2024-06-18"],
        ["7", "Eta", "0.745", "Group C", "2024-07-22"],
        ["8", "Theta", "0.558", "Group B", "2024-08-30"],
      ],
      totalRows: file.rows ?? 8,
    };
  }
  if (file.type === "json") {
    return {
      headers: ["key", "type", "value"],
      rows: [
        ["name", "string", '"experiment_01"'],
        ["params.lr", "number", "0.001"],
        ["params.epochs", "number", "50"],
        ["params.batch_size", "number", "32"],
        ["results.accuracy", "number", "0.947"],
        ["results.f1", "number", "0.923"],
      ],
      totalRows: 6,
    };
  }
  return { headers: ["(raw)"], rows: [["Binary or unsupported format"]], totalRows: 1 };
}

/* ------------------------------------------------------------------ */
/*  Sample data                                                        */
/* ------------------------------------------------------------------ */

const SAMPLE_FILES: DataFile[] = [
  {
    id: fileId(),
    name: "experiment_results.csv",
    path: "/data/experiment_results.csv",
    type: "csv",
    rows: 1247,
    columns: 12,
    size: 245_760,
    addedAt: Date.now() - 7200_000,
  },
  {
    id: fileId(),
    name: "hyperparams.json",
    path: "/data/hyperparams.json",
    type: "json",
    rows: undefined,
    columns: undefined,
    size: 2048,
    addedAt: Date.now() - 3600_000,
  },
  {
    id: fileId(),
    name: "survey_responses.xlsx",
    path: "/data/survey_responses.xlsx",
    type: "xlsx",
    rows: 523,
    columns: 8,
    size: 89_600,
    addedAt: Date.now() - 1800_000,
  },
];

/* ------------------------------------------------------------------ */
/*  Data preview table                                                 */
/* ------------------------------------------------------------------ */

function DataPreviewTable({
  data,
  fileName,
  onClose,
}: {
  data: PreviewData;
  fileName: string;
  onClose: () => void;
}) {
  const [sortCol, setSortCol] = useState<number | null>(null);
  const [sortAsc, setSortAsc] = useState(true);
  const [filter, setFilter] = useState("");

  const handleSort = (col: number) => {
    if (sortCol === col) {
      setSortAsc((v) => !v);
    } else {
      setSortCol(col);
      setSortAsc(true);
    }
  };

  let rows = data.rows;
  if (filter) {
    const q = filter.toLowerCase();
    rows = rows.filter((r) => r.some((c) => c.toLowerCase().includes(q)));
  }
  if (sortCol !== null) {
    rows = [...rows].sort((a, b) => {
      const va = a[sortCol] ?? "";
      const vb = b[sortCol] ?? "";
      const numA = Number(va);
      const numB = Number(vb);
      if (!isNaN(numA) && !isNaN(numB)) return sortAsc ? numA - numB : numB - numA;
      return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
    });
  }

  return (
    <div className="border-t border-gray-800 flex flex-col">
      <div className="flex items-center justify-between px-3 py-1 bg-gray-800/30">
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-gray-400 font-medium">{fileName}</span>
          <span className="text-[9px] text-gray-600">
            {rows.length} of {data.totalRows} rows
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="relative">
            <Search size={10} className="absolute left-1.5 top-1/2 -translate-y-1/2 text-gray-600" />
            <input
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter…"
              className="w-28 pl-5 pr-2 py-0.5 text-[10px] bg-gray-800 border border-gray-700 rounded text-gray-300 placeholder:text-gray-600 focus:outline-none focus:border-purple-500/50"
            />
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 transition-colors">
            <X size={12} />
          </button>
        </div>
      </div>
      <div className="overflow-auto max-h-56">
        <table className="text-[10px] w-full">
          <thead>
            <tr className="bg-gray-800/50 sticky top-0">
              {data.headers.map((h, i) => (
                <th
                  key={i}
                  className="px-2 py-1 text-left text-gray-400 font-medium border-b border-gray-700 whitespace-nowrap cursor-pointer hover:text-gray-200 select-none"
                  onClick={() => handleSort(i)}
                >
                  <span className="flex items-center gap-0.5">
                    {h}
                    {sortCol === i && (
                      <ArrowUpDown size={8} className="text-purple-400" />
                    )}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 50).map((row, i) => (
              <tr key={i} className="hover:bg-gray-800/20">
                {row.map((cell, j) => (
                  <td
                    key={j}
                    className="px-2 py-0.5 text-gray-400 border-b border-gray-800/30 whitespace-nowrap"
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export default function DataBrowser() {
  const [files, setFiles] = useState<DataFile[]>(SAMPLE_FILES);
  const [previewFile, setPreviewFile] = useState<string | null>(null);
  const [previewData, setPreviewData] = useState<PreviewData | null>(null);

  const handlePreview = useCallback((file: DataFile) => {
    if (previewFile === file.id) {
      setPreviewFile(null);
      setPreviewData(null);
      return;
    }
    setPreviewFile(file.id);
    setPreviewData(mockPreview(file));
  }, [previewFile]);

  const handleImport = useCallback(() => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".csv,.json,.xlsx,.xls,.tsv";
    input.multiple = true;
    input.onchange = () => {
      if (!input.files) return;
      Array.from(input.files).forEach((file) => {
        const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
        const type: DataFile["type"] =
          ext === "csv" || ext === "tsv"
            ? "csv"
            : ext === "json"
              ? "json"
              : ext === "xlsx" || ext === "xls"
                ? "xlsx"
                : "other";
        setFiles((prev) => [
          ...prev,
          {
            id: fileId(),
            name: file.name,
            path: file.name,
            type,
            size: file.size,
            addedAt: Date.now(),
          },
        ]);
      });
    };
    input.click();
  }, []);

  const deleteFile = useCallback((id: string) => {
    setFiles((prev) => prev.filter((f) => f.id !== id));
    if (previewFile === id) {
      setPreviewFile(null);
      setPreviewData(null);
    }
  }, [previewFile]);

  const previewedFileName = files.find((f) => f.id === previewFile)?.name ?? "";

  return (
    <div className="h-full flex flex-col">
      <div className="px-3 py-1.5 border-b border-gray-800 bg-[#252526] flex items-center justify-between">
        <span className="text-xs text-gray-400">Datasets ({files.length})</span>
        <button
          onClick={handleImport}
          className="flex items-center gap-1 text-[10px] text-gray-500 hover:text-gray-300 transition-colors"
        >
          <Upload size={12} /> Import
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {files.length === 0 ? (
          <div className="text-center text-xs text-gray-600 mt-8">
            <Database size={24} className="mx-auto mb-2 text-gray-700" />
            <p>No datasets</p>
            <p className="mt-1 text-[10px]">Upload CSV, JSON, or Excel files</p>
            <button
              onClick={handleImport}
              className="mt-3 px-3 py-1 text-[10px] text-purple-400 border border-purple-500/30 rounded hover:bg-purple-900/20 transition-colors"
            >
              <Upload size={10} className="inline mr-1" />
              Import dataset
            </button>
          </div>
        ) : (
          <>
            <div className="divide-y divide-gray-800/30">
              {files.map((f) => (
                <div
                  key={f.id}
                  className={`px-3 py-2 hover:bg-gray-800/30 cursor-pointer flex items-center gap-2 group transition-colors ${
                    previewFile === f.id ? "bg-gray-800/20" : ""
                  }`}
                  onClick={() => handlePreview(f)}
                >
                  {fileIcon(f.type)}
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-gray-300 truncate">{f.name}</p>
                    <p className="text-[10px] text-gray-600">
                      {f.rows != null ? `${f.rows.toLocaleString()} rows` : "—"}{" "}
                      {f.columns != null ? `× ${f.columns} cols` : ""} ·{" "}
                      {formatSize(f.size)}
                    </p>
                  </div>
                  <button
                    className="opacity-0 group-hover:opacity-100 text-gray-500 hover:text-blue-400 transition-all"
                    onClick={(e) => {
                      e.stopPropagation();
                      handlePreview(f);
                    }}
                    title="Preview"
                  >
                    <Eye size={12} />
                  </button>
                  <button
                    className="opacity-0 group-hover:opacity-100 text-gray-500 hover:text-red-400 transition-all"
                    onClick={(e) => {
                      e.stopPropagation();
                      deleteFile(f.id);
                    }}
                    title="Remove"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              ))}
            </div>

            {previewData && previewFile && (
              <DataPreviewTable
                data={previewData}
                fileName={previewedFileName}
                onClose={() => {
                  setPreviewFile(null);
                  setPreviewData(null);
                }}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}
