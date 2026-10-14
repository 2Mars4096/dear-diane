import { expect, it, vi } from 'vitest';
import { RunStreamConnection } from '../runStreamConnection';

function socket() {
  return { readyState: 0, close: vi.fn(), onmessage: vi.fn(), onclose: vi.fn(), onerror: vi.fn() };
}
it('reconnects the same running session after leaving and reopening it', () => {
  const connection = new RunStreamConnection();
  const first = socket();
  connection.connect('thread:run', () => first as unknown as WebSocket);
  expect(connection.matches('thread:run')).toBe(true);
  connection.close();
  expect(connection.matches('thread:run')).toBe(false);
  expect(first.close).toHaveBeenCalledOnce();
  expect(first.onmessage).toBeNull();
  const reopened = socket();
  const receive = vi.fn();
  reopened.onmessage = receive;
  connection.connect('thread:run', () => reopened as unknown as WebSocket);
  reopened.onmessage();
  expect(receive).toHaveBeenCalledOnce();
  expect(connection.matches('thread:run')).toBe(true);
});
it('allows retry after network closure and detaches an old session on replacement', () => {
  const connection = new RunStreamConnection();
  const first = socket();
  connection.connect('a:run', () => first as unknown as WebSocket);
  first.readyState = 3;
  expect(connection.matches('a:run')).toBe(false);
  const second = socket();
  connection.connect('b:run', () => second as unknown as WebSocket);
  expect(connection.matches('a:run')).toBe(false);
  expect(connection.matches('b:run')).toBe(true);
  expect(first.onclose).toBeNull();
  second.readyState = 2;
  expect(connection.matches('b:run')).toBe(false);
});
