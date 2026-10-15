import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import http from 'node:http';
import { afterEach, expect, it, vi } from 'vitest';
import { proxyWebSocket } from '../../electron/webSocketProxy';

afterEach(() => vi.restoreAllMocks());
function fixture() {
  const request = Object.assign(new EventEmitter(), { destroy: vi.fn(), end: vi.fn(), setTimeout: vi.fn() });
  vi.spyOn(http, 'request').mockReturnValue(request as unknown as http.ClientRequest);
  const client = new PassThrough();
  // Avoid echo loops in this transport fixture while preserving socket events.
  vi.spyOn(client, 'pipe').mockReturnValue(client);
  const upstream = Object.assign(new PassThrough(), { setTimeout: vi.fn() });
  vi.spyOn(upstream, 'pipe').mockReturnValue(upstream);
  const clientWrite = vi.spyOn(client, 'write'), upstreamWrite = vi.spyOn(upstream, 'write');
  proxyWebSocket({ url: '/api/events', headers: {} } as http.IncomingMessage, client, Buffer.from('client-head'), 8000);
  return { request, client, upstream, clientWrite, upstreamWrite };
}
it('handles broken pipes on either socket without an uncaught exception', () => {
  for (const side of ['client', 'upstream'] as const) {
    const f = fixture();
    f.request.emit('upgrade', { headers: { 'sec-websocket-accept': 'accepted' } }, f.upstream, Buffer.from('server-head'));
    expect(f.upstreamWrite).toHaveBeenCalledWith(Buffer.from('client-head'));
    expect(f.clientWrite).toHaveBeenCalledWith(Buffer.from('server-head'));
    expect(() => f[side].emit('error', Object.assign(new Error('write EPIPE'), { code: 'EPIPE' }))).not.toThrow();
    expect(f.client.destroyed).toBe(true);
    expect(f.upstream.destroyed).toBe(true);
    expect(f.request.destroy).toHaveBeenCalled();
  }
});
it('closes a late upstream upgrade after the client has gone away', () => {
  const f = fixture();
  f.client.emit('close');
  f.request.emit('upgrade', { headers: {} }, f.upstream, Buffer.alloc(0));
  expect(f.upstream.destroyed).toBe(true);
  expect(f.clientWrite).not.toHaveBeenCalled();
  expect(() => f.upstream.emit('error', new Error('closed'))).not.toThrow();
});
it('closes the client when the backend rejects the upgrade', () => {
  const f = fixture();
  const response = { resume: vi.fn() };
  f.request.emit('response', response);
  expect(response.resume).toHaveBeenCalled();
  expect(f.client.destroyed).toBe(true);
});
