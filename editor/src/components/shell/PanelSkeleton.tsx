/**
 * Loading skeletons for panels and mode surfaces.
 * Match visual density of actual content.
 */

export function PanelSkeleton({ lines = 5 }: { lines?: number }) {
  const widths = [75, 55, 90, 40, 65, 80, 50, 70, 45, 85];
  return (
    <div className="p-4 space-y-3 animate-pulse">
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="flex items-center gap-3">
          <div className="w-4 h-4 rounded bg-gray-200 dark:bg-gray-800 flex-shrink-0" />
          <div
            className="h-3 rounded bg-gray-200 dark:bg-gray-800"
            style={{ width: `${widths[i % widths.length]}%` }}
          />
        </div>
      ))}
    </div>
  );
}

export function ChatSkeleton() {
  return (
    <div className="flex-1 p-6 space-y-6 animate-pulse">
      <div className="flex gap-3">
        <div className="w-8 h-8 rounded-full bg-gray-200 dark:bg-gray-800 flex-shrink-0" />
        <div className="flex-1 space-y-2">
          <div className="h-3 w-24 rounded bg-gray-200 dark:bg-gray-800" />
          <div className="h-3 w-3/4 rounded bg-gray-200 dark:bg-gray-800" />
          <div className="h-3 w-1/2 rounded bg-gray-200 dark:bg-gray-800" />
        </div>
      </div>
      <div className="flex gap-3 justify-end">
        <div className="space-y-2">
          <div className="h-3 w-48 rounded bg-gray-200 dark:bg-gray-800" />
        </div>
        <div className="w-8 h-8 rounded-full bg-gray-200 dark:bg-gray-800 flex-shrink-0" />
      </div>
    </div>
  );
}

export function EditorSkeleton() {
  const lineWidths = [85, 60, 45, 90, 30, 70, 55, 80, 40, 65, 75, 50, 88, 35, 72, 58, 82, 42, 68, 52];
  return (
    <div className="flex-1 flex flex-col bg-gray-900">
      <div className="h-9 border-b border-gray-800 flex items-center gap-2 px-2 animate-pulse">
        <div className="h-5 w-24 rounded bg-gray-800" />
        <div className="h-5 w-20 rounded bg-gray-800" />
      </div>
      <div className="flex-1 p-4 space-y-1.5 animate-pulse">
        {Array.from({ length: 20 }).map((_, i) => (
          <div key={i} className="flex gap-2">
            <div className="w-8 h-3 rounded bg-gray-800/50 flex-shrink-0" />
            <div
              className="h-3 rounded bg-gray-800/30"
              style={{ width: `${lineWidths[i % lineWidths.length]}%` }}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
