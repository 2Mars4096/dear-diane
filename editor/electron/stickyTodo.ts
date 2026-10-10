import http, { type IncomingMessage, type ServerResponse } from 'node:http';
import { sessionWindowDestination } from './sessionWindows';

export function handleStickyTodoOpen(req: IncomingMessage, res: ServerResponse, options: {
  backendPort: number; frontendPort: number; open: (destination: string) => void;
}): boolean {
  const match = /^\/api\/sticky-todo\/requests\/([a-f0-9-]{36})\/open$/.exec(req.url || '');
  if (!match) return false;
  const finish = (code: number, body: string) => {
    if (!res.writableEnded) { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(body); }
  };
  // Only native local clients may request a privileged desktop session window.
  if (req.method !== 'POST' || req.headers.origin || req.headers.host !== `127.0.0.1:${options.frontendPort}`) {
    finish(403, '{"detail":"Native local requests only"}'); return true;
  }
  const lookup = http.get(`http://127.0.0.1:${options.backendPort}/api/sticky-todo/requests/${match[1]}`, response => {
    let body = '';
    response.on('data', chunk => { body += chunk; if (body.length > 1048576) response.destroy(); });
    response.on('end', () => {
      try {
        if (response.statusCode !== 200) throw Error('Session unavailable');
        const record = JSON.parse(body);
        if (typeof record.session_id !== 'string' || typeof record.workflow_id !== 'string') throw Error('Invalid session');
        const target = new URL(`http://127.0.0.1:${options.frontendPort}/`);
        target.searchParams.set('session', record.session_id);
        target.searchParams.set('workflow', record.workflow_id);
        const destination = sessionWindowDestination(target.href, target.origin);
        if (!destination) throw Error('Invalid session');
        options.open(destination);
        finish(200, '{"ok":true}');
      } catch { finish(502, '{"detail":"Could not open Diane session"}'); }
    });
    response.on('error', () => finish(502, '{"detail":"Diane response interrupted"}'));
  });
  lookup.setTimeout(5000, () => lookup.destroy());
  lookup.on('error', () => finish(503, '{"detail":"Diane unavailable"}'));
  return true;
}
