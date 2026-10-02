import { useEffect, useRef, useState } from "react";
import { GitFork, Pencil, RefreshCw } from "lucide-react";

export function RequestActions({ text, disabled, onEdit, onRegenerate, onFork, response = false }: {
  response?: boolean;
  text: string; disabled?: boolean;
  onEdit?: (text: string) => Promise<void>;
  onRegenerate?: () => void; onFork?: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(text);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const field = useRef<HTMLTextAreaElement>(null);
  const editButton = useRef<HTMLButtonElement>(null);
  const savingRef = useRef(false);
  useEffect(() => { if (editing) field.current?.focus(); }, [editing]);
  const cancel = () => { setEditing(false); setError(""); requestAnimationFrame(() => editButton.current?.focus()); };
  async function save() {
    if (!onEdit || disabled || savingRef.current || !draft.trim()) return;
    savingRef.current = true; setSaving(true); setError("");
    try { await onEdit(draft.trim()); cancel(); }
    catch (error) { setError(error instanceof Error ? error.message : "Could not resend. Your edit is still here."); }
    finally { savingRef.current = false; setSaving(false); }
  }
  return <div className={`wb-request-actions${response ? " wb-response-actions" : ""}`} aria-label={response ? "Response actions" : "Request actions"} data-editing={editing}>
    {editing ? <form className="wb-request-edit" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <textarea ref={field} aria-label="Edit last message" value={draft} disabled={saving} rows={4} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => {
        if (event.nativeEvent.isComposing) return;
        if (event.key === "Escape" && !saving) { event.preventDefault(); cancel(); }
        if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); void save(); }
      }} />
      <p>Resending replaces the answer below. Existing attachments are kept.</p>
      {error && <p role="alert">{error}</p>}
      <div><button type="button" disabled={saving} onClick={cancel}>Cancel</button><button type="submit" disabled={disabled || saving || !draft.trim()}>{saving ? "Resending…" : "Save & resend"}</button></div>
    </form> : <>
      {onEdit && <button ref={editButton} aria-label="Edit" type="button" disabled={disabled} onClick={() => { setDraft(text); setEditing(true); }} title={disabled ? "Wait for the current run to finish or stop it before editing" : "Edit and resend this message"}><Pencil size={12} aria-hidden="true" /></button>}
      {onRegenerate && <button type="button" disabled={disabled} onClick={onRegenerate} aria-label="Regenerate" title="Run this request again and replace the answer below"><RefreshCw size={12} aria-hidden="true" /></button>}
      {onFork && <button type="button" disabled={disabled} onClick={onFork} aria-label="Fork" title="Continue this conversation in a new chat"><GitFork size={12} aria-hidden="true" /></button>}
    </>}
  </div>;
}
