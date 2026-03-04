import { useState, useCallback } from "react";
import { useGraphStore } from "../store/useGraphStore";
import { exportBlock } from "../lib/api";

interface BlockExportDialogProps {
  nodeId: string;
  onClose: () => void;
}

export default function BlockExportDialog({ nodeId, onClose }: BlockExportDialogProps) {
  const graphId = useGraphStore((s) => s.graphId);
  const addToast = useGraphStore((s) => s.addToast);
  const nodes = useGraphStore((s) => s.nodes);

  const targetNode = nodes.find((n) => n.id === nodeId);
  const nodeName = (targetNode?.data as Record<string, unknown>)?.name as string | undefined;

  const [name, setName] = useState(nodeName ?? "");
  const [version, setVersion] = useState("0.1.0");
  const [description, setDescription] = useState("");
  const [author, setAuthor] = useState("");
  const [exporting, setExporting] = useState(false);

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (!graphId || !name.trim()) return;
    setExporting(true);
    try {
      const blob = await exportBlock(graphId, nodeId, {
        name: name.trim(),
        version: version.trim() || "0.1.0",
        description: description.trim(),
        author: author.trim() || undefined,
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${name.trim()}-${version.trim() || "0.1.0"}.danblock`;
      a.click();
      URL.revokeObjectURL(url);
      addToast({ type: "success", message: `Exported block "${name.trim()}"` });
      onClose();
    } catch (err: unknown) {
      addToast({ type: "error", message: `Export failed: ${(err as Error).message}` });
    } finally {
      setExporting(false);
    }
  }, [graphId, nodeId, name, version, description, author, addToast, onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <form
        onSubmit={handleSubmit}
        className="bg-white rounded-xl shadow-2xl w-[400px] flex flex-col"
      >
        <div className="px-5 py-3 border-b border-gray-100 flex items-center justify-between">
          <span className="text-sm font-semibold text-gray-800">Export as Block</span>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 p-0.5 rounded transition-colors"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>

        <div className="px-5 py-4 space-y-3">
          <div>
            <label className="block text-[11px] font-medium text-gray-600 mb-1">Name *</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              placeholder="my-block"
              className="w-full px-2.5 py-1.5 text-xs border border-gray-200 rounded-lg focus:outline-none focus:border-indigo-400 focus:ring-1 focus:ring-indigo-200"
            />
          </div>
          <div>
            <label className="block text-[11px] font-medium text-gray-600 mb-1">Version</label>
            <input
              type="text"
              value={version}
              onChange={(e) => setVersion(e.target.value)}
              placeholder="0.1.0"
              className="w-full px-2.5 py-1.5 text-xs border border-gray-200 rounded-lg focus:outline-none focus:border-indigo-400 focus:ring-1 focus:ring-indigo-200"
            />
          </div>
          <div>
            <label className="block text-[11px] font-medium text-gray-600 mb-1">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What this block does…"
              rows={2}
              className="w-full px-2.5 py-1.5 text-xs border border-gray-200 rounded-lg focus:outline-none focus:border-indigo-400 focus:ring-1 focus:ring-indigo-200 resize-none"
            />
          </div>
          <div>
            <label className="block text-[11px] font-medium text-gray-600 mb-1">Author</label>
            <input
              type="text"
              value={author}
              onChange={(e) => setAuthor(e.target.value)}
              placeholder="Your name"
              className="w-full px-2.5 py-1.5 text-xs border border-gray-200 rounded-lg focus:outline-none focus:border-indigo-400 focus:ring-1 focus:ring-indigo-200"
            />
          </div>
        </div>

        <div className="px-5 py-3 border-t border-gray-100 flex items-center gap-2 justify-end">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 text-xs font-medium text-gray-500 hover:text-gray-700 transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={exporting || !name.trim()}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-purple-600 hover:bg-purple-700 rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {exporting ? "Exporting…" : "Export Block"}
          </button>
        </div>
      </form>
    </div>
  );
}
