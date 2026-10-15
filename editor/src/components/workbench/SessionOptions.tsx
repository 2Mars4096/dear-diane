import { MoreHorizontal } from 'lucide-react';
import { useDismissDetails } from './useDismissDetails';

export function SessionOptions({ id, onStatus }: { id: string; onStatus: (text: string) => void }) {
  const menu = useDismissDetails();
  return <details ref={menu} className="wb-chat-options" onKeyDown={event => {
    if (event.key === 'Escape') {
      event.preventDefault();
      if (menu.current) { menu.current.open = false; menu.current.querySelector('summary')?.focus(); }
    }
  }}>
    <summary aria-label="Session options" title="Session options"><MoreHorizontal size={16} /></summary>
    <div><button onClick={() => {
      void navigator.clipboard.writeText(id).then(() => onStatus('Session ID copied'), () => onStatus(`Session ID: ${id}`));
      if (menu.current) menu.current.open = false;
    }}>Copy session ID <code>{id}</code></button></div>
  </details>;
}
