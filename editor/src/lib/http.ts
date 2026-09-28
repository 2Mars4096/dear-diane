/** Shared same-origin JSON transport. Domain clients own their endpoint contracts. */
export type RequestOptions = RequestInit & { timeoutMs?: number };

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(`${status}: ${message}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

async function responseError(response: Response) {
  const text = await response.text();
  let detail: unknown;
  try { detail = JSON.parse(text).detail; } catch { /* A proxy may return plain text or HTML. */ }
  const message = typeof detail === 'string' ? detail : Array.isArray(detail)
    ? detail.map(item => typeof item?.msg === 'string' ? item.msg : '').filter(Boolean).join('. ')
    : !text.trim().startsWith('<') ? text.slice(0, 300) : '';
  return new ApiError(response.status, message || response.statusText || 'Request failed', detail);
}

export async function requestJson<T>(url: string, options: RequestOptions = {}): Promise<T> {
  const { timeoutMs = 10_000, signal: upstream, ...init } = options;
  const controller = new AbortController();
  const abort = () => controller.abort(upstream?.reason);
  if (upstream?.aborted) abort();
  else upstream?.addEventListener('abort', abort, { once: true });
  let timedOut = false;
  const timer = timeoutMs > 0 ? setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs) : null;
  const headers = new Headers(init.headers);
  if (!headers.has('Content-Type') && typeof init.body === 'string') headers.set('Content-Type', 'application/json');
  try {
    const response = await fetch(url, { ...init, headers, signal: controller.signal });
    if (!response.ok) throw await responseError(response);
    if (response.status === 204) return undefined as T;
    // Keep cancellation/deadlines active while the response body is being consumed.
    return await response.json() as T;
  } catch (error) {
    if (timedOut && !upstream?.aborted) throw new Error(`Request timed out after ${timeoutMs}ms: ${url}`);
    throw error;
  } finally {
    if (timer !== null) clearTimeout(timer);
    upstream?.removeEventListener('abort', abort);
  }
}
