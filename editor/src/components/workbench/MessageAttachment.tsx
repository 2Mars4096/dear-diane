import { useState } from "react";
import { ExternalLink, File } from "lucide-react";
import type { ChatAttachment } from "../../types/chat";
import { isElectron, nativeShell } from "../../lib/electronBridge";
import { workspaceFilePreviewUrl } from "../../lib/api";

export function MessageAttachment({ attachment }: { attachment: ChatAttachment }) {
  const [error, setError] = useState("");
  const [opening, setOpening] = useState(false);
  const path = attachment.path?.trim() || "";
  const normalized = path.replaceAll("\\", "/");
  const split = normalized.lastIndexOf("/");
  const url = path ? workspaceFilePreviewUrl(path, split >= 0 ? normalized.slice(0, split) || "/" : undefined, normalized.slice(split + 1)) : "";
  const size = attachment.size == null ? "" : attachment.size >= 1048576 ? `${(attachment.size / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.ceil(attachment.size / 1024))} KB`;
  return <div className="wb-attachment">
    {path ? <a className="wb-attachment-link" href={url} target="_blank" rel="noopener noreferrer" title={`Open ${attachment.filename}`} aria-busy={opening} onClick={async event => {
      if (!isElectron()) return;
      event.preventDefault();
      if (opening) return;
      setOpening(true); setError("");
      try { if (!await nativeShell.openPath(path)) setError("Could not open this file. It may have moved or no app is available to open it."); }
      catch { setError("Could not open this file. Check that it still exists."); }
      finally { setOpening(false); }
    }}><File size={14} aria-hidden="true" /><span>{attachment.filename}</span>{size && <small>{size}</small>}<ExternalLink size={12} aria-hidden="true" /></a>
      : <span className="wb-attachment-missing">{attachment.filename} · Original file unavailable</span>}
    {attachment.caption && <p className="wb-muted">{attachment.caption}</p>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
