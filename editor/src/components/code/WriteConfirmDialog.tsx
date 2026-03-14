interface WriteConfirmDialogProps {
  filePath: string;
  onAllow: () => void;
  onPin: () => void;
  onCancel: () => void;
}

export default function WriteConfirmDialog({ filePath, onAllow, onPin, onCancel }: WriteConfirmDialogProps) {
  const parentDir = filePath.split("/").slice(0, -1).join("/");
  const parentName = parentDir.split("/").pop() ?? parentDir;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60">
      <div className="bg-gray-900 border border-gray-700 rounded-lg shadow-xl w-[460px] p-5">
        <h3 className="text-sm font-semibold text-white mb-2">Write Outside Workspace</h3>
        <p className="text-xs text-gray-400 mb-1">
          This file is outside your trusted workspace roots:
        </p>
        <p className="text-xs text-blue-400 font-mono mb-4 break-all">{filePath}</p>
        <p className="text-xs text-gray-400 mb-4">
          Editing files outside pinned roots may affect other projects. You can allow this once,
          or pin the parent directory to your workspace.
        </p>
        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-xs text-gray-400 hover:text-white rounded border border-gray-700 hover:border-gray-600"
          >
            Cancel
          </button>
          <button
            onClick={onAllow}
            className="px-3 py-1.5 text-xs text-gray-300 hover:text-white rounded border border-gray-700 hover:border-gray-600"
          >
            Allow Once
          </button>
          <button
            onClick={onPin}
            className="px-3 py-1.5 text-xs text-white bg-blue-600 hover:bg-blue-500 rounded"
          >
            Pin &ldquo;{parentName}&rdquo; to Workspace
          </button>
        </div>
      </div>
    </div>
  );
}
