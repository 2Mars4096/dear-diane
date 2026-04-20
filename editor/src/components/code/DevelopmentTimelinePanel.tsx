import { useState, type ReactNode } from "react";
import { Clock3, History } from "lucide-react";

import LocalHistoryPanel from "./LocalHistoryPanel";
import OrganismLogTimelinePanel from "./OrganismLogTimelinePanel";

type TimelineTab = "organism" | "history";

export default function DevelopmentTimelinePanel() {
  const [activeTab, setActiveTab] = useState<TimelineTab>("organism");

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-950">
        <div className="flex px-2">
          <TimelineTabButton
            active={activeTab === "organism"}
            icon={<Clock3 size={13} />}
            label="Organism Logs"
            onClick={() => setActiveTab("organism")}
          />
          <TimelineTabButton
            active={activeTab === "history"}
            icon={<History size={13} />}
            label="File History"
            onClick={() => setActiveTab("history")}
          />
        </div>
      </div>

      <div className="min-h-0 flex-1">
        {activeTab === "organism" ? <OrganismLogTimelinePanel /> : <LocalHistoryPanel />}
      </div>
    </div>
  );
}

function TimelineTabButton({
  active,
  icon,
  label,
  onClick,
}: {
  active: boolean;
  icon: ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 border-b-2 px-3 py-2 text-[11px] font-semibold uppercase tracking-[0.14em] transition ${
        active
          ? "border-blue-600 text-blue-700 dark:border-blue-400 dark:text-blue-200"
          : "border-transparent text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100"
      }`}
    >
      {icon}
      {label}
    </button>
  );
}
