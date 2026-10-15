/** A remembered run is attached only while its socket is connecting or open. */
export class RunStreamConnection {
  private socket: WebSocket | null = null;
  private key = '';
  private frame: number | null = null;
  private pending: (() => void) | null = null;

  schedule(update: () => void): void {
    this.pending = update;
    if (this.frame === null) this.frame = requestAnimationFrame(() => this.flush());
  }

  flush(): void {
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.frame = null;
    const update = this.pending;
    this.pending = null;
    update?.();
  }

  matches(key: string): boolean {
    return this.key === key && this.socket !== null && this.socket.readyState < 2;
  }

  close(): void {
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.frame = null;
    this.pending = null;
    const socket = this.socket;
    this.socket = null;
    this.key = '';
    // Ignore late frames/close callbacks from a session we have left.
    if (socket) {
      socket.onmessage = socket.onclose = socket.onerror = null;
      socket.close();
    }
  }

  connect(key: string, create: () => WebSocket): void {
    this.close();
    this.socket = create();
    this.key = key;
  }
}
