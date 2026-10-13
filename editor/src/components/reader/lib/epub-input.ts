// Give visible navigation priority over offscreen whole-book measurement.
let readingInputAt = 0;
export function prioritizeEpubInput() { readingInputAt = performance.now(); }
export async function yieldEpubPagination(signal: AbortSignal) {
  do {
    await new Promise(resolve => window.setTimeout(resolve, 16));
    signal.throwIfAborted();
  } while (performance.now() - readingInputAt < 200);
}

const controls = 'input,textarea,select,[contenteditable], [role="slider"]';
export function epubNavigationKey(event: KeyboardEvent, turn: (direction: number) => void) {
  if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || (event.target as Element)?.closest?.(controls)) return;
  const direction = ['ArrowRight', 'ArrowDown', 'PageDown'].includes(event.key) ? 1 : ['ArrowLeft', 'ArrowUp', 'PageUp'].includes(event.key) ? -1 : 0;
  if (!direction) return;
  event.preventDefault();
  turn(direction);
}

/** Convert wheel gestures to page turns without queuing every trackpad event. */
export function epubWheelNavigation(turn: (direction: number) => void) {
  let accumulated = 0, lastEvent = 0, lastTurn = -Infinity;
  return (event: WheelEvent) => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || (event.target as Element)?.closest?.(controls)) return;
    const delta = Math.abs(event.deltaY) >= Math.abs(event.deltaX) ? event.deltaY : event.deltaX;
    if (!delta) return;
    event.preventDefault();
    const now = performance.now();
    if (now - lastEvent > 160 || Math.sign(delta) !== Math.sign(accumulated)) accumulated = 0;
    lastEvent = now;
    accumulated += delta * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 600 : 1);
    if (Math.abs(accumulated) >= 60 && now - lastTurn >= 240) {
      turn(Math.sign(accumulated)); accumulated = 0; lastTurn = now;
    }
  };
}
