import { browserFileTarget } from '../../lib/fileTargets';
import { useEffect, useRef, useState } from 'react';
import { droppedFolderPath } from '../workbench/folderDrop';
import { FolderOpen } from 'lucide-react';
import { nativeFs } from '../../lib/electronBridge';
import { pathDocument, type DocumentFile } from './documents';
import './documents.css';

/** External file/folder drops; project settings retains its own drop handlers. */
export default function FileOpener({ onOpen, onFolder }: { onOpen: (file: DocumentFile) => void; onFolder: (path: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const callback = useRef(onOpen); callback.current = onOpen;
  const folderCallback = useRef(onFolder); folderCallback.current = onFolder;
  const [dragging, setDragging] = useState(false);
  const [bounds, setBounds] = useState<DOMRect | null>(null);
  const [error, setError] = useState('');
  const open = async (files: File[]) => {
    setError('');
    for (const file of files) {
      try {
        const path = await nativeFs.droppedFile(file);
        callback.current(path ? pathDocument(path, file.name) : browserFileTarget(file));
      } catch (e) { setError(`Could not open ${file.name}: ${e instanceof Error ? e.message : String(e)}`); }
    }
  };
  const openRef = useRef(open); openRef.current = open;
  useEffect(() => {
    let depth = 0;
    const files = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes('Files');
    const over = (e: DragEvent) => { if (files(e)) { e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'; } };
    const enter = (e: DragEvent) => { if (files(e)) {
      if ((e.target as HTMLElement)?.closest?.("[data-literature-drop], [data-composer-drop]")) { setDragging(false); return; }
      depth++;
      setBounds(document.querySelector('[data-main-drop-area]')?.getBoundingClientRect() ?? null);
      setDragging(!document.querySelector('dialog[open]'));
    } };
    const leave = () => { if (--depth <= 0) { depth = 0; setDragging(false); } };
    const drop = (e: DragEvent) => {
      depth = 0; setDragging(false);
      if (!files(e)) return;
      // Project Settings owns directory drops.
      if ((e.target as HTMLElement)?.closest?.('.wb-folder-drop, .wb-remote-project-folder, [data-literature-drop], [data-composer-drop]')) return;
      e.preventDefault(); e.stopPropagation();
      const items = Array.from(e.dataTransfer?.items ?? []);
      if (items.some(item => item.webkitGetAsEntry?.()?.isDirectory)) {
        setError('');
        const machine = document.querySelector<HTMLMetaElement>('meta[name="dan-remote-machine"]')?.content;
        if (machine) { setError(`To open a folder on ${machine}, use New project and Browse folders.`); return; }
        // Capture native drag data before the browser clears the drop event.
        void droppedFolderPath(e.dataTransfer!).then(result => {
          if (result.path !== undefined) folderCallback.current(result.path);
          else setError(result.error);
        }).catch(e => setError(e instanceof Error ? e.message : 'Could not open this project.'));
        return;
      }
      void openRef.current(Array.from(e.dataTransfer?.files ?? []));
    };
    const reset = () => { depth = 0; setDragging(false); };
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') reset();
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'o' && !e.altKey && !document.querySelector('dialog[open]')) { e.preventDefault(); input.current?.click(); }
    };
    window.addEventListener('blur', reset); window.addEventListener('dragend', reset);
    window.addEventListener('dragenter', enter); window.addEventListener('dragover', over);
    window.addEventListener('dragleave', leave); window.addEventListener('drop', drop, true); window.addEventListener('keydown', key);
    return () => { window.removeEventListener('blur', reset); window.removeEventListener('dragend', reset); window.removeEventListener('dragenter', enter); window.removeEventListener('dragover', over); window.removeEventListener('dragleave', leave); window.removeEventListener('drop', drop, true); window.removeEventListener('keydown', key); };
  }, []);
  return <>
    <button type="button" aria-label="Open file" title="Open file (⌘/Ctrl+O), or drop files into Dear Diane" onClick={() => input.current?.click()}><FolderOpen size={17} /></button>
    <input ref={input} type="file" multiple hidden onChange={e => { void open(Array.from(e.target.files ?? [])); e.target.value = ''; }} />
    {dragging && <div className="dan-file-drop" role="status" style={bounds ? { inset: 'auto', top: bounds.top + 8, left: bounds.left + 8, width: Math.max(0, bounds.width - 16), height: Math.max(0, bounds.height - 16) } : undefined}><div><FolderOpen size={32} aria-hidden="true" /><strong>Drop a folder to open it as a project</strong><span>Or drop PDFs and other files to open them</span></div></div>}
    {error && <div className="dan-file-error" role="alert">{error}<button onClick={() => setError('')}>Dismiss</button></div>}
  </>;
}
