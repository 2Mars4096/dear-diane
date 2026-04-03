import { MessageSquareText, Terminal as TerminalIcon } from "lucide-react";

const PRIMARY_TAB_LABELS: Record<"editor" | "reader" | "furnace", string> = {
  editor: "Writing desk",
  reader: "Reader",
  furnace: "Furnace",
};

export function ResearchStatusStrip({
  primaryTab,
  activePaperTitle,
  paperCount,
  runningTrainingCount,
  showContextPanel,
}: {
  primaryTab: "editor" | "reader" | "furnace";
  activePaperTitle: string | null;
  paperCount: number;
  runningTrainingCount: number;
  showContextPanel: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 bg-gradient-to-r from-white via-stone-50 to-emerald-50/70 px-3 py-2 text-[11px] dark:border-gray-800 dark:from-[#252526] dark:via-[#202226] dark:to-emerald-500/5">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <span className="inline-flex items-center rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300">
          Research desk
        </span>
        <span className="inline-flex items-center rounded-full border border-gray-200 bg-white/80 px-2 py-0.5 text-[10px] font-medium text-gray-600 dark:border-gray-700 dark:bg-gray-800/80 dark:text-gray-300">
          {PRIMARY_TAB_LABELS[primaryTab]}
        </span>
        <span className="truncate text-gray-600 dark:text-gray-400">
          {activePaperTitle ? `Active paper: ${activePaperTitle}` : "No paper selected"}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-gray-500 dark:text-gray-400">
        <span>{`${paperCount} papers`}</span>
        {runningTrainingCount > 0 && <span>{`${runningTrainingCount} training live`}</span>}
        <span>{`Context ${showContextPanel ? "open" : "closed"} · cmd+i`}</span>
        <span className="inline-flex items-center gap-1">
          <MessageSquareText size={11} className="text-blue-500 dark:text-blue-300" />
          Chat cmd+j
        </span>
        <span className="inline-flex items-center gap-1">
          <TerminalIcon size={11} />
          Terminal cmd+`
        </span>
      </div>
    </div>
  );
}
