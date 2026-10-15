import http from 'node:http';
import type { Duplex } from 'node:stream';

/** Own both sides of an upgraded connection, including disconnects during setup. */
export function proxyWebSocket(req: http.IncomingMessage, socket: Duplex, head: Buffer, port: number) {
  if (socket.destroyed) return;
  let upstream: Duplex | undefined;
  let closed = false;
  const proxy = http.request({ hostname: '127.0.0.1', port, path: req.url, method: 'GET', headers: { ...req.headers, host: `127.0.0.1:${port}` } });
  const close = () => {
    if (closed) return;
    closed = true;
    proxy.destroy();
    upstream?.unpipe(socket);
    if (upstream) socket.unpipe(upstream);
    upstream?.destroy();
    socket.destroy();
  };
  socket.on('error', close);
  socket.once('close', close);
  socket.once('end', close);
  proxy.on('error', close);
  proxy.setTimeout(10000, close);
  proxy.once('response', response => { response.resume(); close(); });
  proxy.once('upgrade', (response, connection, upstreamHead) => {
    upstream = connection;
    connection.on('error', close);
    connection.once('close', close);
    connection.once('end', close);
    connection.setTimeout(0);
    if (closed || socket.destroyed) { connection.destroy(); return; }
    socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
      + Object.entries(response.headers).filter(([key]) => !['upgrade', 'connection'].includes(key.toLowerCase()))
        .map(([key, value]) => `${key}: ${value}`).join('\r\n') + '\r\n\r\n');
    if (upstreamHead.length) socket.write(upstreamHead);
    if (head.length) connection.write(head);
    connection.pipe(socket);
    socket.pipe(connection);
  });
  proxy.end();
}
