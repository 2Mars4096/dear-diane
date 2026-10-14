/** A remembered run is attached only while its socket is connecting or open. */
export class RunStreamConnection {
  private socket: WebSocket | null = null;
  private key = '';

  matches(key: string): boolean {
    return this.key === key && this.socket !== null && this.socket.readyState < 2;
  }

  close(): void {
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
