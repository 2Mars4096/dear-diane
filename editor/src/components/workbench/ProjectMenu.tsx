import { useId, useRef, useState } from "react";
import { Download, MoreHorizontal, Pencil, Plus, Trash2 } from "lucide-react";

export function ProjectMenu({ name, onNewChat, onEdit, onImport, onRemove }: {
  name: string; onNewChat: () => void; onEdit: () => void; onImport?: () => void; onRemove: () => void;
}) {
  const headingId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const confirmation = useRef<HTMLDialogElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const close = () => { menu.current?.hidePopover(); trigger.current?.focus(); };
  const dismiss = () => { confirmation.current?.close(); trigger.current?.focus(); };
  return <>
    <button ref={trigger} className="wb-project-new-chat" title="Project actions" aria-label={`Actions for ${name}`} aria-haspopup="menu" aria-expanded={open} onClick={() => {
      if (open) { close(); return; }
      const box = trigger.current!.getBoundingClientRect();
      const panel = menu.current!;
      panel.style.left = `${Math.max(8, Math.min(box.right - 208, window.innerWidth - 216))}px`;
      panel.style.top = `${Math.min(box.bottom + 6, window.innerHeight - 190)}px`;
      panel.showPopover();
      panel.querySelector<HTMLButtonElement>("button")?.focus();
    }}><MoreHorizontal size={14} /></button>
    <div ref={menu} popover="auto" className="wb-project-menu" role="menu" aria-label={`Actions for ${name}`} onToggle={(event) => setOpen(event.newState === "open")} onKeyDown={(event) => {
      if (event.key === "Escape" || event.key === "Tab") { close(); return; }
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const buttons = Array.from(event.currentTarget.querySelectorAll("button"));
      const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
      const index = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (current + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
      buttons[index]?.focus();
    }}>
      <button role="menuitem" onClick={() => { close(); onNewChat(); }}><Plus size={15} />New chat</button>
      <button role="menuitem" onClick={() => { close(); onEdit(); }}><Pencil size={15} />Edit project</button>
      {onImport && <button role="menuitem" onClick={() => { close(); onImport(); }}><Download size={15} />Import native sessions</button>}
      <div className="wb-menu-separator" />
      <button role="menuitem" className="wb-remove-action" onClick={() => { close(); confirmation.current?.showModal(); cancel.current?.focus(); }}><Trash2 size={15} />Remove from Dear Diane</button>
    </div>
    <dialog ref={confirmation} className="wb-project-settings wb-remove-confirmation" aria-labelledby={headingId} onCancel={(event) => { event.preventDefault(); dismiss(); }}>
      <h2 id={headingId}>Remove “{name}” from Dear Diane?</h2>
      <p>This removes the project and its chats from this Dear Diane project list. Your folder, files, and original Codex, Claude Code, and Antigravity sessions will not be deleted. Dear Diane chat history is kept too.</p>
      <footer><button ref={cancel} onClick={dismiss}>Cancel</button><button className="wb-remove-action" onClick={() => { confirmation.current?.close(); onRemove(); }}>Remove from Dear Diane</button></footer>
    </dialog>
  </>;
}
