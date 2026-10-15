import { expect, it, vi } from 'vitest';
import { DraftPersistence } from '../draftPersistence';

it('keeps storage off the typing path and merges other windows at flush', () => {
  vi.useFakeTimers();
  let stored = { 'chat:a': 'old', 'chat:b': 'other' };
  const read = vi.fn(() => stored);
  const write = vi.fn((value) => { stored = value; });
  const drafts = new DraftPersistence(read, write);
  try {
    drafts.set('chat:a', 'h'); drafts.set('chat:a', 'hello');
    expect(read).not.toHaveBeenCalled(); expect(write).not.toHaveBeenCalled();
    stored['chat:b'] = 'changed elsewhere';
    vi.advanceTimersByTime(250);
    expect(stored).toEqual({ 'chat:a': 'hello', 'chat:b': 'changed elsewhere' });
    expect(write).toHaveBeenCalledOnce();
    drafts.set('chat:a', ''); drafts.flush();
    expect(stored).toEqual({ 'chat:b': 'changed elsewhere' });
    vi.runAllTimers(); expect(write).toHaveBeenCalledTimes(2);
  } finally { vi.useRealTimers(); }
});

it('preserves drafts from multiple sessions during a rapid switch and flush', () => {
  vi.useFakeTimers();
  const write = vi.fn();
  try {
    const drafts = new DraftPersistence(() => ({}), write);
    drafts.set('chat:a', 'latest before switching');
    drafts.set('chat:b', 'new session');
    drafts.flush();
    expect(write).toHaveBeenCalledWith({ 'chat:a': 'latest before switching', 'chat:b': 'new session' });
  } finally { vi.useRealTimers(); }
});
