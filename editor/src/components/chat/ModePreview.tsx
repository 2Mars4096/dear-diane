import { useAppStore, type AppMode } from "../../store/useAppStore";

function CodeModePreview() {
  return (
    <div className="w-[200px] h-[120px] rounded border border-gray-700 bg-gray-900 flex overflow-hidden text-[8px] select-none">
      <div className="w-10 bg-gray-800 border-r border-gray-700 flex flex-col gap-0.5 p-1">
        <div className="h-1.5 w-full rounded-sm bg-gray-600" />
        <div className="h-1.5 w-5 rounded-sm bg-gray-700 ml-1" />
        <div className="h-1.5 w-4 rounded-sm bg-gray-700 ml-1" />
        <div className="h-1.5 w-6 rounded-sm bg-gray-700 ml-1" />
        <div className="h-1.5 w-full rounded-sm bg-gray-600 mt-1" />
        <div className="h-1.5 w-5 rounded-sm bg-gray-700 ml-1" />
      </div>
      <div className="flex-1 flex flex-col">
        <div className="h-5 bg-gray-800 border-b border-gray-700 flex items-center gap-1 px-1">
          <div className="h-2 w-8 rounded-sm bg-blue-500/30" />
          <div className="h-2 w-6 rounded-sm bg-gray-700" />
        </div>
        <div className="flex-1 bg-gray-900 p-1 flex flex-col gap-0.5">
          <div className="h-1 w-3/4 rounded-sm bg-gray-800" />
          <div className="h-1 w-1/2 rounded-sm bg-gray-800" />
          <div className="h-1 w-2/3 rounded-sm bg-blue-900/40" />
          <div className="h-1 w-1/2 rounded-sm bg-gray-800" />
          <div className="h-1 w-3/5 rounded-sm bg-gray-800" />
        </div>
        <div className="h-8 bg-gray-800 border-t border-gray-700 p-1 flex flex-col gap-0.5">
          <div className="h-1 w-2/3 rounded-sm bg-green-900/40" />
          <div className="h-1 w-1/2 rounded-sm bg-gray-700" />
        </div>
      </div>
    </div>
  );
}

function ResearchModePreview() {
  return (
    <div className="w-[200px] h-[120px] rounded border border-gray-700 bg-gray-900 flex overflow-hidden text-[8px] select-none">
      <div className="flex-1 flex flex-col border-r border-gray-700">
        <div className="h-5 bg-gray-800 border-b border-gray-700 flex items-center px-1">
          <div className="h-2 w-10 rounded-sm bg-emerald-500/30" />
        </div>
        <div className="flex-1 bg-gray-900 p-1.5 flex flex-col gap-1">
          <div className="h-1.5 w-3/4 rounded-sm bg-gray-700" />
          <div className="h-1 w-full rounded-sm bg-gray-800" />
          <div className="h-1 w-full rounded-sm bg-gray-800" />
          <div className="h-1 w-2/3 rounded-sm bg-gray-800" />
          <div className="h-1.5 w-1/2 rounded-sm bg-gray-700 mt-1" />
          <div className="h-1 w-full rounded-sm bg-gray-800" />
          <div className="h-1 w-3/4 rounded-sm bg-gray-800" />
        </div>
      </div>
      <div className="w-16 flex flex-col bg-gray-850">
        <div className="h-5 bg-gray-800 border-b border-gray-700 flex items-center px-1">
          <div className="h-2 w-8 rounded-sm bg-teal-500/30" />
        </div>
        <div className="flex-1 p-1 flex flex-col gap-1">
          <div className="h-4 w-full rounded-sm bg-gray-800 border border-gray-700" />
          <div className="h-4 w-full rounded-sm bg-gray-800 border border-gray-700" />
          <div className="h-4 w-full rounded-sm bg-gray-800 border border-gray-700" />
        </div>
      </div>
    </div>
  );
}

interface Props {
  mode: AppMode;
  onClick?: () => void;
}

export default function ModePreview({ mode, onClick }: Props) {
  const content = (() => {
    switch (mode) {
      case "development":
        return <CodeModePreview />;
      case "research":
        return <ResearchModePreview />;
      default:
        return null;
    }
  })();

  if (!content) return null;

  return (
    <button
      onClick={onClick}
      className="shrink-0 opacity-80 hover:opacity-100 transition-opacity cursor-pointer"
      title={`Click to switch to ${mode} mode`}
    >
      {content}
    </button>
  );
}
