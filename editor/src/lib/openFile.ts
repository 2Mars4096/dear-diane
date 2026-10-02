import { currentFileHost, pathFileTarget, type FileTarget } from './fileTargets';

export type FileOpenRequest = { file?: FileTarget; href?: string; root?: string };
export function openFile(request: FileOpenRequest) {
  window.dispatchEvent(new CustomEvent<FileOpenRequest>('dan:open-file', { detail: request }));
}

export async function resolveFileRequest(request: FileOpenRequest): Promise<FileTarget> {
  if (request.file) return request.file;
  const href = request.href || '';
  const root = request.root || '';
  if (currentFileHost() === 'local' && window.electronAPI?.shell.fileLink) {
    const result = await window.electronAPI.shell.fileLink({ href, root, resolveOnly: true });
    if (!result.ok || !result.path) throw Error(result.error || 'Could not resolve this file.');
    return pathFileTarget(result.path);
  }
  let path = decodeURIComponent(href).replace(/(?::\d+(?::\d+)?|#L\d+(?:C\d+)?(?:-L?\d+)?)$/, '');
  if (/^file:/i.test(path)) path = new URL(path).pathname;
  if (!path || /[\x00-\x1f]/.test(path) || path.startsWith('//') || (/^[a-z][a-z\d+.-]*:/i.test(path) && !/^[a-z]:[\\/]/i.test(path))) throw Error('Invalid file path.');
  if (path.startsWith('~/')) throw Error('Use an absolute path to open this file.');
  if (!path.startsWith('/') && !/^[a-z]:[\\/]/i.test(path)) {
    if (!root) throw Error('Select a project folder to open relative file links.');
    path = `${root}/${path}`;
  }
  const parts: string[] = [];
  for (const part of path.replaceAll('\\', '/').split('/')) {
    if (part === '..') parts.pop(); else if (part !== '.') parts.push(part);
  }
  return pathFileTarget(parts.join('/'));
}
