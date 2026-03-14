import { useState, useEffect } from "react";
import { FolderOpen, Lightbulb, X } from "lucide-react";
import { useCodeStore } from "../../store/useCodeStore";
import {
  detectProjectType,
  type ProjectDetection,
} from "../../lib/workspaceIntelligence";

const TYPE_ICONS: Record<string, React.ReactNode> = {
  node: <span className="text-green-400">&#x2B22;</span>,
  python: <span className="text-yellow-400">&pi;</span>,
  rust: <span className="text-orange-400">Rs</span>,
  go: <span className="text-cyan-400 font-semibold">Go</span>,
  java: <span className="text-red-400">J</span>,
};

export default function WorkspaceInfo() {
  const [detection, setDetection] = useState<ProjectDetection | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const pinnedRoots = useCodeStore((s) => s.pinnedRoots);

  useEffect(() => {
    if (pinnedRoots.length === 0) return;
    let cancelled = false;
    detectProjectType(pinnedRoots[0]).then((d) => {
      if (!cancelled && d.type !== "unknown") setDetection(d);
    });
    return () => {
      cancelled = true;
    };
  }, [pinnedRoots]);

  if (!detection || dismissed) return null;

  return (
    <div className="flex items-center gap-3 px-3 py-1.5 border-b border-gray-800 bg-blue-900/5 shrink-0">
      <div className="flex items-center gap-1.5 text-[11px] text-gray-300">
        {TYPE_ICONS[detection.type] ?? <FolderOpen size={12} />}
        <span className="font-medium">{detection.name}</span>
        {detection.version && (
          <span className="text-gray-600">v{detection.version}</span>
        )}
        {detection.packageManager && (
          <span className="text-[9px] px-1 py-0.5 bg-gray-800 text-gray-500 rounded">
            {detection.packageManager}
          </span>
        )}
      </div>

      {detection.frameworks.length > 0 && (
        <div className="flex items-center gap-1">
          {detection.frameworks.map((f) => (
            <span
              key={f}
              className="text-[9px] px-1.5 py-0.5 bg-purple-900/20 text-purple-400 rounded"
            >
              {f}
            </span>
          ))}
        </div>
      )}

      {detection.recommendedExtensions.length > 0 && (
        <div className="flex items-center gap-1 text-[10px] text-gray-500">
          <Lightbulb size={10} className="text-yellow-500" />
          <span>
            Suggested:{" "}
            {detection.recommendedExtensions.slice(0, 3).join(", ")}
          </span>
          {detection.recommendedExtensions.length > 3 && (
            <span className="text-gray-600">
              +{detection.recommendedExtensions.length - 3}
            </span>
          )}
        </div>
      )}

      <button
        onClick={() => setDismissed(true)}
        className="ml-auto text-gray-600 hover:text-gray-300 transition-colors"
      >
        <X size={12} />
      </button>
    </div>
  );
}
