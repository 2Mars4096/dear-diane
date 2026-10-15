/** Merge only this window's changed drafts, off the keystroke path. */
export class DraftPersistence {
  private pending = new Map<string, string>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private read: () => Record<string, string>;
  private write: (drafts: Record<string, string>) => void;
  constructor(read: () => Record<string, string>, write: (drafts: Record<string, string>) => void) { this.read = read; this.write = write; }
  set(key: string, value: string) {
    this.pending.set(key, value);
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.flush(), 250);
  }
  flush() {
    clearTimeout(this.timer);
    this.timer = undefined;
    if (!this.pending.size) return;
    const drafts = { ...this.read() };
    for (const [key, value] of this.pending) {
      if (value) drafts[key] = value;
      else delete drafts[key];
    }
    this.pending.clear();
    this.write(drafts);
  }
}
