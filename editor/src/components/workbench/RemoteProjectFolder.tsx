import { useEffect, useRef, useState } from "react";
import { ChevronRight, FolderOpen } from "lucide-react";
import { listWorkspaceRootSuggestions, type WorkspaceRootSuggestion } from "../../lib/api";

/** Lists folders on the connected DAN host, never on the browser's computer. */
export function RemoteProjectFolder({ machine, initialPath, onSelect }: {
  machine: string; initialPath: string; onSelect: (path: string) => void;
}) {
  const [query, setQuery] = useState(initialPath);
  const [folders, setFolders] = useState<WorkspaceRootSuggestion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const generation = useRef(0);
  async function browse(path: string) {
    const request = ++generation.current;
    setLoading(true); setError(""); setFolders([]);
    try {
      let result = await listWorkspaceRootSuggestions(path, 50);
      if (!path) {
        path = result.root.replace(/\/$/, "") + "/";
        result = await listWorkspaceRootSuggestions(path, 50);
      }
      if (request !== generation.current) return;
      setQuery(path);
      // The API always includes the configured workspace. Only show it when
      // browsing that folder; otherwise it would appear under every directory.
      setFolders(result.suggestions.filter(folder => folder.kind !== "current" || folder.path === (path.replace(/\/+$/, "") || "/")));
    } catch {
      if (request === generation.current) setError(`Could not load folders on ${machine}. Check the connection and try again.`);
    } finally { if (request === generation.current) setLoading(false); }
  }
  useEffect(() => {
    void browse(initialPath ? initialPath.replace(/\/$/, "") + "/" : "");
    return () => { ++generation.current; };
  }, []);
  return <section className="wb-remote-folders" aria-label={`Folders on ${machine}`}>
    <label htmlFor="remote-folder-query">Find a folder on {machine}</label>
    <div className="wb-remote-folder-search">
      <input id="remote-folder-query" value={query} placeholder="~/Downloads/local_projects/" onChange={event => { setQuery(event.target.value); ++generation.current; setLoading(false); setFolders([]); setError(""); }} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); void browse(query.trim()); } }} />
      <button type="button" onClick={() => void browse(query.trim())} disabled={loading}>Go</button>
      <button type="button" onClick={() => void browse("~/")} disabled={loading}>Home</button>
    </div>
    {loading && <p role="status">Loading folders…</p>}
    {error && <p role="alert">{error} <button type="button" onClick={() => void browse(query.trim())}>Retry</button></p>}
    {!loading && !error && folders.length === 0 && <p>No matching folders. Enter a remote path or folder prefix and choose Go.</p>}
    <ul aria-label="Remote folders" aria-busy={loading}>
      {folders.map(folder => <li key={folder.path}>
        <button type="button" className="wb-remote-folder-select" onClick={() => onSelect(folder.path)} title={`Use ${folder.path}`}>
          <FolderOpen size={16} aria-hidden="true" /><span><strong>{folder.name}</strong><small>{folder.path}</small></span>
        </button>
        <button type="button" aria-label={`Browse ${folder.path}`} onClick={() => void browse(folder.path.replace(/\/$/, "") + "/")}><ChevronRight size={16} /></button>
      </li>)}
    </ul>
    {folders.length >= 49 && <p>Showing the first matches. Enter a more specific path to find another folder.</p>}
    <p>Select a folder to use it. The arrow opens its subfolders.</p>
  </section>;
}
