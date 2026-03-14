export default function CrashRecoveryBanner({
  fileCount,
  onDismiss,
}: {
  fileCount: number;
  onDismiss: () => void;
}) {
  return (
    <div className="flex items-center justify-between px-4 py-2 bg-yellow-600/20 border-b border-yellow-600/30 text-yellow-200 text-xs">
      <span>
        Recovered {fileCount} unsaved file{fileCount !== 1 ? "s" : ""} from a
        previous session. Review and save them, or close to discard.
      </span>
      <button
        onClick={onDismiss}
        className="px-2 py-0.5 rounded bg-yellow-600/30 hover:bg-yellow-600/50 text-yellow-200"
      >
        Dismiss
      </button>
    </div>
  );
}
