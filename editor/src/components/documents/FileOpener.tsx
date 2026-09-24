import { useEffect, useRef, useState } from 'react';
import { FolderOpen } from 'lucide-react';
import { nativeFs } from '../../lib/electronBridge';
import { pathDocument, type DocumentFile } from './documents';
import './documents.css';

/** File drags open documents; internal app drags and folder setup keep their own handlers. */
export default function FileOpener({ onOpen }: { onOpen: (file: DocumentFile) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const callback = useRef(onOpen); callback.current = onOpen;
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState('');
  const open = async (files: File[]) => {
    setError('');
    for (const file of files) {
      try {
        const path = await nativeFs.droppedFile(file);
        callback.current(path ? pathDocument(path, file.name) : {
          name: file.name, path: `browser:${crypto.randomUUID()}:${file.name}`,
          url: URL.createObjectURL(file), local: file, ownedUrl: true,
        });
      } catch (e) { setError(`Could not open ${file.name}: ${e instanceof Error ? e.message : String(e)}`); }
    }
  };
  const openRef = useRef(open); openRef.current = open;
  useEffect(() => {
    let depth = 0;
    const files = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes('Files');
    const over = (e: DragEvent) => { if (files(e)) { e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'; } };
    const enter = (e: DragEvent) => { if (files(e)) { depth++; setDragging(true); } };
    const leave = () => { if (--depth <= 0) { depth = 0; setDragging(false); } };
    const drop = (e: DragEvent) => {
      depth = 0; setDragging(false);
      if (!files(e)) return;
      // Project Settings owns directory drops.
      if ((e.target as HTMLElement)?.closest?.('.wb-folder-drop')) return;
      e.preventDefault(); e.stopPropagation();
      const items = Array.from(e.dataTransfer?.items ?? []);
      if (items.some(item => item.webkitGetAsEntry?.()?.isDirectory)) { setError('Drop individual files here. Use project settings to open a folder.'); return; }
      void openRef.current(Array.from(e.dataTransfer?.files ?? []));
    };
    const key = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'o' && !e.altKey && !document.querySelector('dialog[open]')) { e.preventDefault(); input.current?.click(); }
    };
    window.addEventListener('dragenter', enter); window.addEventListener('dragover', over);
    window.addEventListener('dragleave', leave); window.addEventListener('drop', drop, true); window.addEventListener('keydown', key);
    return () => { window.removeEventListener('dragenter', enter); window.removeEventListener('dragover', over); window.removeEventListener('dragleave', leave); window.removeEventListener('drop', drop, true); window.removeEventListener('keydown', key); };
  }, []);
  return <>
    <button type="button" aria-label="Open file" title="Open file (⌘/Ctrl+O), or drop files into DAN" onClick={() => input.current?.click()}><FolderOpen size={17} /></button>
    <input ref={input} type="file" multiple hidden onChange={e => { void open(Array.from(e.target.files ?? [])); e.target.value = ''; }} />
    {dragging && <div className="dan-file-drop">Drop files to open in DAN</div>}
    {error && <div className="dan-file-error" role="alert">{error}<button onClick={() => setError('')}>Dismiss</button></div>}
  </>;
}
