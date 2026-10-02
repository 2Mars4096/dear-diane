import type { FileTarget } from '../../lib/fileTargets';
export { pathFileTarget as pathDocument } from '../../lib/fileTargets';
export type DocumentDraft = { text: string; saved: string; revision: string };
export type DocumentFile = FileTarget & { onClose?: () => void; workspaceId?: string; projectName?: string; readingWorkflowId?: string };
export function documentKind(name: string): 'pdf' | 'image' | 'audio' | 'video' | 'text' {
  const ext = name.split('.').pop()?.toLowerCase() ?? '';
  if (ext === 'pdf') return 'pdf';
  if (['png','jpg','jpeg','gif','webp','avif','bmp','svg'].includes(ext)) return 'image';
  if (['mp3','wav','ogg','m4a','flac'].includes(ext)) return 'audio';
  if (['mp4','webm','mov','m4v'].includes(ext)) return 'video';
  return 'text';
}
export function decodeDocument(raw: ArrayBuffer): string {
  if (raw.byteLength > 2_000_000) throw new Error('Text editing supports files up to 2 MB.');
  const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw);
  if (text.includes('\0')) throw new Error('This file is not UTF-8 text. Download it to open in another app.');
  return text;
}
export async function documentResponse(response: Response) {
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : 'The file could not be saved.');
  return value;
}
export function downloadDocument(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
