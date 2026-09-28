import { workspaceFilePreviewUrl } from './api';

type FileLocation = { name: string; path: string; url: string; root?: string; local?: File; ownedUrl?: boolean };
export type FileTarget = FileLocation & (
  | { source: 'local' | 'remote' }
  | { source: 'browser'; local: File; ownedUrl: true }
);
export function currentFileHost(): 'local' | 'remote' {
  return typeof document !== 'undefined' && document.querySelector('meta[name="dan-remote-machine"]') ? 'remote' : 'local';
}

/** Display names never change the actual filename used by the preview endpoint. */
export function pathFileTarget(path: string, name?: string, options: { root?: string; url?: string; source?: 'local' | 'remote' } = {}): FileTarget {
  const normalized = path.replaceAll('\\', '/');
  const split = normalized.lastIndexOf('/');
  const basename = normalized.slice(split + 1);
  const parent = split < 0 ? undefined : split === 0 ? '/' : split === 2 && /^[a-z]:/i.test(normalized) ? normalized.slice(0, 3) : normalized.slice(0, split);
  return { source: options.source ?? currentFileHost(), name: name ?? basename, path, root: options.root ?? parent,
    url: options.url ?? workspaceFilePreviewUrl(path, parent, basename) };
}
export function browserFileTarget(file: File): FileTarget {
  return { source: 'browser', name: file.name, path: `browser:${crypto.randomUUID()}:${file.name}`,
    url: URL.createObjectURL(file), local: file, ownedUrl: true };
}
