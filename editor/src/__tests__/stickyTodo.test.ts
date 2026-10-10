import { expect, it } from 'vitest';
import { EventEmitter } from 'node:events';
import http from 'node:http';
import { vi } from 'vitest';
import { handleStickyTodoOpen } from '../../electron/stickyTodo';

it('opens only the session returned by Diane and rejects browser requests', () => {
  const open = vi.fn();
  const response = Object.assign(new EventEmitter(), { statusCode: 200, destroy: vi.fn() });
  const get = vi.spyOn(http, 'get').mockImplementation((_url: any, callback: any) => {
    callback(response);
    return Object.assign(new EventEmitter(), { setTimeout: vi.fn() }) as any;
  });
  const req = { url: '/api/sticky-todo/requests/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/open', method: 'POST', headers: { host:'127.0.0.1:45173' } };
  const res = { writableEnded:false, writeHead:vi.fn(), end:vi.fn() };
  expect(handleStickyTodoOpen(req as any, res as any, {backendPort:8000,frontendPort:45173,open})).toBe(true);
  response.emit('data', JSON.stringify({session_id:'same-session',workflow_id:'existing-project'})); response.emit('end');
  expect(open).toHaveBeenCalledWith('http://127.0.0.1:45173/?session=same-session&workflow=existing-project');
  expect(res.writeHead.mock.calls[0][0]).toBe(200);
  open.mockClear(); get.mockClear(); res.writeHead.mockClear();
  handleStickyTodoOpen({...req,headers:{...req.headers,origin:'https://evil.example'}} as any,res as any,{backendPort:8000,frontendPort:45173,open});
  expect(get).not.toHaveBeenCalled(); expect(open).not.toHaveBeenCalled();
  expect(res.writeHead.mock.calls[0][0]).toBe(403);
  get.mockRestore();
});
