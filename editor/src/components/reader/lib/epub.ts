import JSZip from 'jszip';
import { EpubCFI } from 'epubjs';
import type Book from 'epubjs/types/book';
import type Section from 'epubjs/types/section';
import type { PaperComment } from './paper-comments';

export const EPUB_MAX_BYTES = 150 * 1024 * 1024;
export async function checkEpub(bytes: ArrayBuffer) {
  if (bytes.byteLength > EPUB_MAX_BYTES) throw Error('This EPUB is too large (150 MB maximum).');
  const zip = await JSZip.loadAsync(bytes);
  const entries = Object.values(zip.files);
  const size = entries.reduce((sum, entry) => sum + ((entry as unknown as { _data?: { uncompressedSize: number } })._data?.uncompressedSize || 0), 0);
  if (entries.length > 10000 || size > EPUB_MAX_BYTES) throw Error('This EPUB expands beyond the supported size.');
  if (!zip.file('META-INF/container.xml')) throw Error('This file is not a valid EPUB: its book manifest is missing.');
  const encryption = await zip.file('META-INF/encryption.xml')?.async('string');
  if (encryption && /<[^>]*EncryptionMethod[^>]+Algorithm=["'](?!http:\/\/www.idpf.org\/2008\/embedding|http:\/\/ns.adobe.com\/pdf\/enc#RC)/i.test(encryption)) throw Error('This EPUB is DRM protected. Open an unencrypted copy.');
  return zip;
}

/** Book documents are inert: retain publisher layout but never execute scripts or forms. */
export function protectEpubDocument(doc: Document, policyEnabled = true) {
  doc.querySelectorAll('script, iframe, object, embed, form, base, meta[http-equiv]').forEach(node => node.remove());
  doc.querySelectorAll('*').forEach(element => {
    for (const attr of Array.from(element.attributes)) {
      if (/^on/i.test(attr.name) || /^(?:javascript|vbscript):/i.test(attr.value.trim())) element.removeAttribute(attr.name);
    }
    if (element.localName === 'a') {
      const href = element.getAttribute('href') || '';
      if (/^(?:[a-z]+:|\/\/)/i.test(href)) element.removeAttribute('href');
    }
  });
  const head = doc.querySelector('head');
  if (head && policyEnabled) {
    const policy = doc.createElementNS('http://www.w3.org/1999/xhtml', 'meta');
    policy.setAttribute('http-equiv', 'Content-Security-Policy');
    policy.setAttribute('content', "default-src 'none'; img-src blob: data:; style-src 'unsafe-inline' blob: data:; font-src blob: data:; media-src blob: data:; script-src 'none'; connect-src 'none'; form-action 'none'");
    head.prepend(policy);
  }
}

export function downloadEpubBlob(name: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

/** Write standard XHTML highlights and notes into a new EPUB; never change the source. */
export async function exportAnnotatedEpub(bytes: ArrayBuffer, book: Book, comments: PaperComment[]) {
  const zip = await checkEpub(bytes);
  const chapters = new Map<number, PaperComment[]>();
  for (const comment of comments) if (comment.epub) {
    const index = new EpubCFI(comment.epub.cfi).spinePos;
    chapters.set(index, [...(chapters.get(index) || []), comment]);
  }
  for (const [index, notes] of chapters) {
    const section: Section = book.spine.get(index);
    const path = decodeURIComponent(book.resolve(section.href).replace(/^\//, ''));
    const entry = zip.file(path);
    if (!entry) throw Error(`Could not export chapter: ${section.href}`);
    const doc = new DOMParser().parseFromString(await entry.async('string'), 'application/xhtml+xml');
    protectEpubDocument(doc, false);
    const body = doc.querySelector('body');
    if (!body || doc.querySelector('parsererror')) throw Error('Could not read chapter for export.');
    // Resolve all ranges before inserting any elements, retaining original CFI coordinates.
    const ranges = notes.map(note => ({ note, range: new EpubCFI(note.epub!.cfi).toRange(doc) }));
    for (const { note, range } of ranges.reverse()) {
      if (!range) throw Error(`The saved passage in ${note.epub!.label} could not be found.`);
      if (note.kind === 'area') {
        for (const image of Array.from(body.querySelectorAll('img'))) if (range.intersectsNode(image)) { image.setAttribute('style', `${image.getAttribute('style') || ''};outline:3px solid #d8a736;`); image.setAttribute('title', note.text || note.quote); }
      }
      const walker = doc.createTreeWalker(body, NodeFilter.SHOW_TEXT);
      const slices: { node: Text; start: number; end: number }[] = [];
      let node: Node | null;
      while ((node = walker.nextNode())) if (range.intersectsNode(node)) {
        const text = node as Text;
        const start = node === range.startContainer ? range.startOffset : 0;
        const end = node === range.endContainer ? range.endOffset : text.length;
        if (end > start) slices.push({ node: text, start, end });
      }
      for (const slice of slices.reverse()) {
        const r = doc.createRange(); r.setStart(slice.node, slice.start); r.setEnd(slice.node, slice.end);
        const mark = doc.createElementNS('http://www.w3.org/1999/xhtml', 'span');
        mark.setAttribute('style', 'background-color:#f5dc89;color:#29251c;');
        mark.setAttribute('title', note.text || note.quote); r.surroundContents(mark);
      }
    }
    const aside = doc.createElementNS('http://www.w3.org/1999/xhtml', 'section');
    const heading = doc.createElementNS('http://www.w3.org/1999/xhtml', 'h2'); heading.textContent = 'Reading notes'; aside.append(heading);
    for (const note of notes) {
      const p = doc.createElementNS('http://www.w3.org/1999/xhtml', 'p'); p.textContent = `“${note.quote}”${note.text ? ` — ${note.text}` : ''}`; aside.append(p);
    }
    body.append(aside);
    zip.file(path, new XMLSerializer().serializeToString(doc));
  }
  // EPUB requires the first ZIP member to be an uncompressed mimetype entry.
  const output = new JSZip(); output.file('mimetype', 'application/epub+zip', { compression: 'STORE' });
  for (const [name, entry] of Object.entries(zip.files)) if (name !== 'mimetype' && !entry.dir) output.file(name, await entry.async('uint8array'));
  return output.generateAsync({ type: 'blob', mimeType: 'application/epub+zip', compression: 'DEFLATE' });
}
