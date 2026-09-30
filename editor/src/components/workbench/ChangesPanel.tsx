import { useEffect, useRef, useState } from "react";
import { requestJson } from "../../lib/http";
type Review = { snapshots: { id: string; label: string; created_at: number }[]; selected: string; files: { path: string; diff: string; kind: string; truncated: boolean }[]; omitted: string[]; truncated?: boolean };
export default function ChangesPanel({ root, thread, active = true, onComment }: { root: string; thread: string; active?: boolean; onComment: (text: string) => void }) {
  const [review, setReview] = useState<Review | null>(null);
  const [snapshot, setSnapshot] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const selectionText = useRef({ path: "", text: "" });
  const [revision, refresh] = useState(0);
  useEffect(() => {
    if (!root || !thread || !active) return;
    const controller = new AbortController();
    let pending = false;
    async function load(initial = false) {
      if (pending || controller.signal.aborted) return;
      pending = true;
      if (initial) { setBusy(true); setError(""); }
      try {
        const body = await requestJson<Review>(`/api/workspace-changes?${new URLSearchParams({ root, thread, snapshot })}`, { signal: controller.signal });
        if (!controller.signal.aborted) { setReview(body); setError(""); }
      } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : String(e)); }
      finally { pending = false; if (!controller.signal.aborted) setBusy(false); }
    }
    void load(true);
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void load(); }, 5000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [root, thread, snapshot, revision, active]);
  useEffect(() => {
    const changed = () => {
      const selection = window.getSelection();
      const node = selection?.anchorNode;
      const element = node instanceof Element ? node : node?.parentElement;
      const diff = element?.closest<HTMLElement>("[data-change-path]");
      if (diff && selection?.focusNode && diff.contains(selection.focusNode)) selectionText.current = { path: diff.dataset.changePath || "", text: selection.toString() };
    };
    document.addEventListener("selectionchange", changed);
    return () => document.removeEventListener("selectionchange", changed);
  }, []);
  async function capture() {
    setBusy(true); setError("");
    try {
      const body = await requestJson<Review>("/api/workspace-changes", { method: "POST", body: JSON.stringify({ root, thread }) });
      setSnapshot(""); setReview(body); refresh(n => n + 1);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  if (!root || !thread) return <p className="wb-side-empty">Open a project conversation to review changes.</p>;
  return <section className="wb-changes" aria-label="Changes">
    <div className="wb-changes-toolbar">
      <label>Changes since<select value={snapshot || review?.selected || ""} onChange={event => setSnapshot(event.target.value)} disabled={busy || !review?.snapshots.length}>
        {!review?.snapshots.length && <option value="">No saved boundary</option>}
        {review?.snapshots.map(row => <option key={row.id} value={row.id}>{new Date(row.created_at * 1000).toLocaleTimeString()} · {row.label}</option>)}
      </select></label>
      <details className="wb-review-options"><summary>Review options</summary><button disabled={busy} onClick={() => void capture()}>Mark current state</button></details>
    </div>
    <p className="wb-changes-note">Updates automatically while open. Includes your edits.</p>
    {error && <p role="alert">{error}</p>}
    {busy && <p role="status">Loading changes…</p>}
    {review?.files.map(file => <details key={file.path} open className="wb-change-file"><summary>{file.path} · {file.kind}</summary>
      <pre tabIndex={0} data-change-path={file.path}>{file.diff ? file.diff.split("\n").map((line, index) => <span key={index} data-diff={line.startsWith("+") ? "added" : line.startsWith("-") ? "removed" : undefined}>{line || "\u00a0"}</span>) : `${file.kind} empty file`}</pre>
      {file.truncated && <p>Large diff shortened.</p>}
      <button onMouseDown={event => event.preventDefault()} onClick={event => {
        const selection = window.getSelection();
        const container = event.currentTarget.closest("details");
        const selected = selection?.anchorNode && container?.contains(selection.anchorNode) ? selection.toString() : selectionText.current.path === file.path ? selectionText.current.text : "";
        const label = review.snapshots.find(row => row.id === review.selected)?.label || "saved boundary";
        onComment(`Review ${file.path} since “${label}” (${review.selected}):\n\n${selected || file.diff.slice(0, 12000)}\n\nRequested change: `);
      selectionText.current = { path: "", text: "" };
      }}>Ask for changes</button>
    </details>)}
    {review && !busy && !review.files.length && <p>{review.snapshots.length ? "No text changes since this boundary." : "A boundary is saved before each new request. You can also mark the current state."}</p>}
    {!!review?.omitted.length && <details><summary>{review.omitted.length} files excluded (binary, symlink, unreadable, or size limit)</summary><pre>{review.omitted.join("\n")}</pre></details>}
    {review?.truncated && <p>Showing the first 100 changed files.</p>}
  </section>;
}
