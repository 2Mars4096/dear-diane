import { File } from "lucide-react";
import type { ChatAttachment } from "../../types/chat";
import { pathFileTarget } from "../../lib/fileTargets";
import { openFile } from "../../lib/openFile";

export function MessageAttachment({ attachment }: { attachment: ChatAttachment }) {
  const path = attachment.path?.trim() || "";
  const target = path ? pathFileTarget(path, attachment.filename) : null;
  const size = attachment.size == null ? "" : attachment.size >= 1048576 ? `${(attachment.size / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.ceil(attachment.size / 1024))} KB`;
  return <div className="wb-attachment">
    {target ? <a className="wb-attachment-link" href={target.url} title={`Open ${attachment.filename}`} onClick={event => {
      event.preventDefault(); openFile({ file: target });
    }} onContextMenu={event => {
      if (target.source !== 'local' || !window.electronAPI?.shell.fileLink) return;
      event.preventDefault(); void window.electronAPI.shell.fileLink({ href: path, root: target.root || '', menu: true });
    }}><File size={14} aria-hidden="true" /><span>{attachment.filename}</span>{size && <small>{size}</small>}</a>
      : <span className="wb-attachment-missing">{attachment.filename} · Original file unavailable</span>}
    {attachment.caption && <p className="wb-muted">{attachment.caption}</p>}
  </div>;
}
