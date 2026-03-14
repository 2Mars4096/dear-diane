type EventHandler = (event: any) => void;

class WsConnectionManager {
  private ws: WebSocket | null = null;
  private handlers: Map<string, Set<EventHandler>> = new Map();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;
  private maxReconnectDelay = 30_000;
  private url: string = "";

  connect(url: string) {
    this.url = url;
    this._connect();
  }

  private _connect() {
    if (this.ws?.readyState === WebSocket.OPEN) return;

    try {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        this.reconnectAttempts = 0;
        this.emit("connection:open", {});
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          const eventType = data.type ?? data.event ?? "message";
          this.emit(eventType, data);
          this.emit("*", data);
        } catch {
          this.emit("message", { raw: event.data });
        }
      };

      this.ws.onclose = () => {
        this.emit("connection:close", {});
        this._scheduleReconnect();
      };

      this.ws.onerror = () => {
        this.emit("connection:error", {});
      };
    } catch {
      this._scheduleReconnect();
    }
  }

  private _scheduleReconnect() {
    if (this.reconnectTimer) return;
    const delay = Math.min(
      1000 * Math.pow(2, this.reconnectAttempts),
      this.maxReconnectDelay,
    );
    this.reconnectAttempts++;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this._connect();
    }, delay);
  }

  on(eventType: string, handler: EventHandler): () => void {
    if (!this.handlers.has(eventType)) this.handlers.set(eventType, new Set());
    this.handlers.get(eventType)!.add(handler);
    return () => {
      this.handlers.get(eventType)?.delete(handler);
    };
  }

  private emit(eventType: string, data: any) {
    this.handlers.get(eventType)?.forEach((h) => h(data));
  }

  send(data: any) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(typeof data === "string" ? data : JSON.stringify(data));
    }
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  disconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.ws?.close();
    this.ws = null;
  }
}

export const wsConnection = new WsConnectionManager();
