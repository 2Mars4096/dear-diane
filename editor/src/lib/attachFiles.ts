import { nativeFs } from './electronBridge';
import { currentFileHost } from './fileTargets';
import type { ComposerAttachmentDraft } from './composerAttachments';
export const MAX_ATTACHMENTS = 4;
export const MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024;

export function transferFiles(data: Pick<DataTransfer, 'items' | 'files'>): File[] {
  const files = Array.from(data.files || []);
  return files.length ? files : Array.from(data.items || []).filter(item => item.kind === 'file').map(item => item.getAsFile()).filter((file): file is File => !!file);
}
export async function prepareAttachments(files: File[], source: string) {
  const results = await Promise.allSettled(files.map(async file => {
    if (file.size > MAX_ATTACHMENT_BYTES) throw Error(`${file.name}: attachments support files up to 20 MB.`);
    let path = currentFileHost() === 'local' ? await nativeFs.droppedFile(file) : null;
    if (!path) {
      const response = await fetch('/api/chat-attachments', { method:'POST', headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(file.name || 'Attachment')}, body:file, signal:AbortSignal.timeout(60000) });
      if (!response.ok) throw Error(`${file.name}: could not attach file (${response.status}).`);
      path = (await response.json()).path;
    }
    if (!path) throw Error(`${file.name}: no saved attachment path returned.`);
    let dataUrl: string | undefined;
    if (file.type.startsWith('image/')) {
      dataUrl = await new Promise<string>((resolve,reject) => { const reader=new FileReader(); reader.onload=()=>resolve(String(reader.result)); reader.onerror=()=>reject(Error(`Could not read ${file.name}`)); reader.readAsDataURL(file); });
    }
    return {id:crypto.randomUUID(),kind:file.type.startsWith('image/')?'figure':'file',name:file.name,path,size:file.size,mimeType:file.type || 'application/octet-stream',source,dataUrl} as ComposerAttachmentDraft;
  }));
  return { attachments:results.flatMap(result=>result.status==='fulfilled'?[result.value]:[]), errors:results.flatMap(result=>result.status==='rejected'?[String(result.reason?.message || result.reason)]:[]) };
}
