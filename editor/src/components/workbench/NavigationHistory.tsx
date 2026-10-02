import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight } from 'lucide-react';
import type { MainTab } from './mainTabState';
type Visit = Pick<MainTab, 'id' | 'session'>;
const key = (tab: Visit) => `${tab.id}:${tab.session?.workflowId || ''}:${tab.session?.id || ''}`;
export default function NavigationHistory({ active, onNavigate }: { active: MainTab | undefined; onNavigate: (visit: Visit) => void }) {
  const state = useRef<{ visits: Visit[]; index: number; restoring: string }>({ visits: [], index: -1, restoring: '' });
  const callback = useRef(onNavigate); callback.current = onNavigate;
  const [position, setPosition] = useState({ index: 0, count: 1 });
  useEffect(() => {
    if (!active) return;
    const current = state.current;
    const visit = { id: active.id, session: active.session };
    if (current.restoring) {
      const restored = current.restoring === key(visit);
      current.restoring = '';
      if (restored) return;
    }
    if (current.visits[current.index] && key(current.visits[current.index]) === key(visit)) return;
    current.visits = [...current.visits.slice(0, current.index + 1), visit]; current.index = current.visits.length - 1;
    const entry = { ...window.history.state, danNavigation: current.index };
    if (current.index === 0) window.history.replaceState(entry, ''); else window.history.pushState(entry, '');
    setPosition({ index: current.index, count: current.visits.length });
  }, [active?.id, active?.session?.id, active?.session?.workflowId]);
  useEffect(() => {
    const move = (direction: number) => { const current = state.current; if (current.index + direction >= 0 && current.index + direction < current.visits.length) window.history.go(direction); };
    const pop = (event: PopStateEvent) => {
      const current = state.current, index = event.state?.danNavigation;
      if (!Number.isInteger(index) || !current.visits[index]) return;
      current.index = index; current.restoring = key(current.visits[index]);
      setPosition({ index, count: current.visits.length }); callback.current(current.visits[index]);
    };
    const native = (event: Event) => move((event as CustomEvent<number>).detail);
    const mouse = (event: MouseEvent) => { if (event.button === 3 || event.button === 4) { event.preventDefault(); if (event.type === 'mouseup') move(event.button === 3 ? -1 : 1); } };
    const keyboard = (event: KeyboardEvent) => {
      if ((event.key === 'BrowserBack' || event.key === 'BrowserForward')) { event.preventDefault(); move(event.key === 'BrowserBack' ? -1 : 1); return; }
      if (event.metaKey && ['ArrowLeft','ArrowRight'].includes(event.key) && !(event.target instanceof Element && event.target.closest('input,textarea,[contenteditable=true]'))) { event.preventDefault(); move(event.key === 'ArrowLeft' ? -1 : 1); return; }
      if ((event.altKey && ['ArrowLeft','ArrowRight'].includes(event.key)) || (event.metaKey && ['[',']'].includes(event.key))) { event.preventDefault(); move(['ArrowLeft','['].includes(event.key) ? -1 : 1); }
    };
    window.addEventListener('dan:navigate', native); window.addEventListener('popstate', pop); window.addEventListener('mousedown', mouse); window.addEventListener('mouseup', mouse); window.addEventListener('keydown', keyboard);
    return () => { window.removeEventListener('dan:navigate', native); window.removeEventListener('popstate', pop); window.removeEventListener('mousedown', mouse); window.removeEventListener('mouseup', mouse); window.removeEventListener('keydown', keyboard); };
  }, []);
  return <nav className="wb-history" aria-label="Navigation history"><button aria-label="Back" title="Back (Alt+← or ⌘[)" disabled={position.index === 0} onClick={() => window.history.back()}><ArrowLeft size={16} /></button><button aria-label="Forward" title="Forward (Alt+→ or ⌘])" disabled={position.index >= position.count - 1} onClick={() => window.history.forward()}><ArrowRight size={16} /></button></nav>;
}
