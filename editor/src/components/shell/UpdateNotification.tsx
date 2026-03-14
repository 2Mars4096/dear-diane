import { useEffect, useState, useCallback } from "react";
import { nativeUpdater, type UpdateInfo, type DownloadProgress } from "../../lib/electronBridge";
import { Download, RefreshCw, X } from "lucide-react";

type UpdateState = "idle" | "available" | "downloading" | "ready" | "error";

export default function UpdateNotification() {
  const [state, setState] = useState<UpdateState>("idle");
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [progress, setProgress] = useState<DownloadProgress | null>(null);
  const [errorMsg, setErrorMsg] = useState("");
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    const cleanups = [
      nativeUpdater.onUpdateAvailable((data) => {
        setInfo(data);
        setState("available");
        setDismissed(false);
      }),
      nativeUpdater.onUpToDate(() => {
        setState("idle");
      }),
      nativeUpdater.onDownloadProgress((data) => {
        setProgress(data);
        setState("downloading");
      }),
      nativeUpdater.onUpdateDownloaded(() => {
        setState("ready");
      }),
      nativeUpdater.onError((msg) => {
        setErrorMsg(msg);
        setState("error");
      }),
    ];

    return () => cleanups.forEach((fn) => fn());
  }, []);

  const handleDownload = useCallback(() => {
    setState("downloading");
    setProgress(null);
    nativeUpdater.download();
  }, []);

  const handleInstall = useCallback(() => {
    nativeUpdater.install();
  }, []);

  if (state === "idle" || dismissed) return null;

  return (
    <div className="flex items-center gap-3 px-4 py-2 text-sm bg-blue-600 text-white shrink-0">
      {state === "available" && (
        <>
          <Download className="w-4 h-4 shrink-0" />
          <span>DAN v{info?.version} is available</span>
          <button
            onClick={handleDownload}
            className="px-2.5 py-0.5 rounded bg-white/20 hover:bg-white/30 font-medium transition-colors"
          >
            Download
          </button>
          <button
            onClick={() => setDismissed(true)}
            className="px-2.5 py-0.5 rounded hover:bg-white/20 transition-colors"
          >
            Later
          </button>
        </>
      )}

      {state === "downloading" && (
        <>
          <RefreshCw className="w-4 h-4 shrink-0 animate-spin" />
          <span>Downloading update{progress ? ` — ${Math.round(progress.percent)}%` : "..."}</span>
          {progress && (
            <div className="flex-1 max-w-xs h-1.5 bg-white/25 rounded-full overflow-hidden">
              <div
                className="h-full bg-white rounded-full transition-all duration-300"
                style={{ width: `${progress.percent}%` }}
              />
            </div>
          )}
        </>
      )}

      {state === "ready" && (
        <>
          <Download className="w-4 h-4 shrink-0" />
          <span>Update ready — restart to apply</span>
          <button
            onClick={handleInstall}
            className="px-2.5 py-0.5 rounded bg-white/20 hover:bg-white/30 font-medium transition-colors"
          >
            Restart Now
          </button>
          <button
            onClick={() => setDismissed(true)}
            className="px-2.5 py-0.5 rounded hover:bg-white/20 transition-colors"
          >
            Later
          </button>
        </>
      )}

      {state === "error" && (
        <>
          <X className="w-4 h-4 shrink-0" />
          <span className="truncate">Update error: {errorMsg}</span>
          <button
            onClick={() => setDismissed(true)}
            className="px-2.5 py-0.5 rounded hover:bg-white/20 transition-colors"
          >
            Dismiss
          </button>
        </>
      )}
    </div>
  );
}
