import { useState, useEffect, useCallback } from "react";
import { X, Settings, Package, Zap } from "lucide-react";
import type { ProjectDetection } from "../../lib/workspaceIntelligence";

interface ProjectDetectionToastProps {
  detection: ProjectDetection;
  onConfigure: () => void;
  onDismiss: () => void;
}

export default function ProjectDetectionToast({
  detection,
  onConfigure,
  onDismiss,
}: ProjectDetectionToastProps) {
  const [visible, setVisible] = useState(false);
  const [exiting, setExiting] = useState(false);

  const handleDismiss = useCallback(() => {
    setExiting(true);
    setTimeout(onDismiss, 300);
  }, [onDismiss]);

  useEffect(() => {
    requestAnimationFrame(() => setVisible(true));
    const timer = setTimeout(() => handleDismiss(), 5000);
    return () => clearTimeout(timer);
  }, [handleDismiss]);

  const labels: string[] = [];
  if (detection.type !== "unknown") {
    labels.push(detection.type.charAt(0).toUpperCase() + detection.type.slice(1));
  }
  labels.push(...detection.frameworks);

  if (labels.length === 0) return null;

  return (
    <div
      className={`fixed bottom-12 right-4 z-50 flex items-start gap-3 rounded-lg border border-[#3c3c3c] bg-[#252526] px-4 py-3 shadow-2xl transition-all duration-300 ${
        visible && !exiting
          ? "translate-y-0 opacity-100"
          : "translate-y-4 opacity-0"
      }`}
      style={{ maxWidth: 380 }}
    >
      <div className="mt-0.5 rounded-md bg-blue-500/10 p-1.5">
        <Zap size={14} className="text-blue-400" />
      </div>

      <div className="flex-1 min-w-0">
        <div className="text-xs font-medium text-gray-200">
          Detected Project
        </div>
        <div className="mt-1 flex flex-wrap gap-1.5">
          {labels.map((label) => (
            <span
              key={label}
              className="inline-flex items-center gap-1 rounded-md bg-blue-500/10 px-2 py-0.5 text-[11px] font-medium text-blue-300"
            >
              <Package size={10} />
              {label}
            </span>
          ))}
        </div>
        {detection.packageManager && (
          <div className="mt-1.5 text-[10px] text-gray-500">
            Package manager: {detection.packageManager}
          </div>
        )}
        <button
          onClick={() => {
            onConfigure();
            handleDismiss();
          }}
          className="mt-2 inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium text-blue-400 transition-colors hover:bg-blue-500/10"
        >
          <Settings size={10} />
          Configure
        </button>
      </div>

      <button
        onClick={handleDismiss}
        className="mt-0.5 rounded p-0.5 text-gray-500 transition-colors hover:bg-[#3c3c3c] hover:text-gray-300"
      >
        <X size={12} />
      </button>
    </div>
  );
}
