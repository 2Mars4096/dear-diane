import { useEffect, useRef, useState } from "react";
import { FolderOpen, X } from "lucide-react";
import { droppedFolderPath } from "./folderDrop";

export function ProjectSettings({ name, root, creating, onSave, onClose, onBrowse }: {
  name: string; root: string; creating: boolean;
  onSave: (name: string, root: string) => void;
  onClose: () => void;
  onBrowse: () => Promise<string | null>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [title, setTitle] = useState(name);
  const [path, setPath] = useState(root);
  const [nameEdited, setNameEdited] = useState(false);
  const folderName = path.trim().replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "";
  const projectName = creating && !nameEdited ? folderName || title : title;
  const [dragging, setDragging] = useState(false);
  const [dropError, setDropError] = useState("");
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => { previous?.focus(); };
  }, []);
  return <dialog onClick={(event) => { const box = event.currentTarget.getBoundingClientRect(); if (event.target === event.currentTarget && (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom)) onClose(); }} ref={dialog} className="wb-project-settings" onCancel={onClose} aria-labelledby="project-settings-title">
    <form onSubmit={(event) => { event.preventDefault(); if (projectName.trim()) onSave(projectName.trim(), path.trim()); }}>
      <header><h2 id="project-settings-title">{creating ? "New project" : "Project settings"}</h2><button type="button" onClick={onClose} aria-label="Close project settings"><X size={18} /></button></header>
      <label>Project name<input autoFocus value={projectName} onChange={(event) => { setNameEdited(true); setTitle(event.target.value); }} placeholder="My project" required /></label>
      <label htmlFor="project-folder-path">Folder <span>(optional)</span></label>
      <div className={`wb-folder-drop ${dragging ? "is-dragging" : ""}`}
        onDragOver={(event) => { event.preventDefault(); event.stopPropagation(); event.dataTransfer.dropEffect = "copy"; setDragging(true); }}
        onDragLeave={(event) => { if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setDragging(false); }}
        onDrop={async (event) => {
          event.preventDefault(); event.stopPropagation(); setDragging(false); setDropError("");
          const result = await droppedFolderPath(event.dataTransfer);
          if (result.path !== undefined) setPath(result.path);
          else setDropError(result.error);
        }}>
        <div className="wb-folder-drop-prompt"><FolderOpen size={26} strokeWidth={1.4} aria-hidden="true" /><div className="wb-folder-drop-copy"><strong>{dragging ? "Release to use this folder" : "Drop a folder here"}</strong><span>or <button type="button" onClick={async () => { const folder = await onBrowse(); if (folder) { setPath(folder); setDropError(""); } }}>browse folders</button></span></div></div>
        <input id="project-folder-path" value={path} onChange={(event) => { setPath(event.target.value); setDropError(""); }} placeholder="Or paste the folder path" aria-describedby="project-folder-hint" aria-invalid={Boolean(dropError)} />
      </div>
      <p id="project-folder-hint" role={dropError ? "alert" : undefined}>{dropError}</p>
      <p>Chats in this project share this working folder.</p>
      <footer><button type="button" onClick={onClose}>Cancel</button><button type="submit" disabled={!projectName.trim()}>{creating ? "Create project" : "Save"}</button></footer>
    </form>
  </dialog>;
}
