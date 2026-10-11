// @vitest-environment happy-dom
import { describe, it, expect } from 'vitest';
import JSZip from 'jszip';
import { checkEpub, protectEpubDocument, exportAnnotatedEpub } from '../lib/epub';
import { normalizePaperComments, type PaperComment } from '../lib/paper-comments';
import { normalizePaperReferenceTray } from '../lib/paper-reference-tags';
import type Book from 'epubjs/types/book';
const xml = '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter</title></head><body><p>The little bird crossed the river.</p><p>Another paragraph.</p></body></html>';
const location = { cfi: 'epubcfi(/6/2!/4/2,/1:4,/1:15)', href: 'chapter.xhtml', label: 'Crossing the river' };
const note: PaperComment = { commentId: 'one', materialId: '/book.epub', createdAt: '2026-10-10T00:00:00Z', updatedAt: '2026-10-10T00:00:00Z', pageNumber: 1, rotation: 0, rects: [], quote: 'little bird', text: 'A note', epub: location };
async function archive() { const zip = new JSZip(); zip.file('mimetype', 'application/epub+zip'); zip.file('META-INF/container.xml', '<container/>'); zip.file('chapter.xhtml', xml); return zip.generateAsync({ type: 'arraybuffer' }); }
describe('EPUB storage and documents', () => {
  it('keeps text-location notes and multiple same-chapter references without PDF rectangles', () => {
    expect(normalizePaperComments([note], '/book.epub')[0].epub).toEqual(location);
    const tags = ['one','two'].map(tagId => ({ tagId, createdAt: note.createdAt, pageNumber: 1, label: tagId, selection: { ...note, epub: { ...location, cfi: location.cfi.replace('1:4', tagId === 'one' ? '1:4' : '1:5') } } }));
    const restored = normalizePaperReferenceTray({ tags }, '/book.epub');
    expect(restored.tags).toHaveLength(2); expect(restored.tags[1].selection?.epub?.label).toBe(location.label);
    expect(normalizePaperComments([{ ...note, epub: { ...location, cfi: 'javascript:alert(1)' } }], '/book.epub')).toHaveLength(0);
  });
  it('rejects invalid and encrypted books before rendering', async () => {
    await expect(checkEpub(new ArrayBuffer(2))).rejects.toThrow();
    const zip = new JSZip(); zip.file('META-INF/container.xml', '<container/>'); zip.file('META-INF/encryption.xml', '<encryption><EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/></encryption>');
    await expect(checkEpub(await zip.generateAsync({ type: 'arraybuffer' }))).rejects.toThrow('DRM protected');
    zip.remove('META-INF/container.xml'); await expect(checkEpub(await zip.generateAsync({ type: 'arraybuffer' }))).rejects.toThrow('manifest');
  });
  it('removes active content while preserving text, tables, and images', () => {
    const doc = new DOMParser().parseFromString('<html><head></head><body><script>alert(1)</script><p onclick="steal()">Read me</p><iframe></iframe><a href="javascript:steal()">bad</a><img src="blob:local"/><table><tr><td>42</td></tr></table></body></html>', 'text/html');
    protectEpubDocument(doc);
    expect(doc.querySelector('script,iframe,[onclick]')).toBeNull(); expect(doc.querySelector('a')?.hasAttribute('href')).toBe(false);
    expect(doc.querySelector('img')?.getAttribute('src')).toBe('blob:local'); expect(doc.querySelector('td')?.textContent).toBe('42');
    expect(doc.querySelector('meta')?.getAttribute('content')).toContain("script-src 'none'");
  });
  it('exports a separate valid container and preserves original assets', async () => {
    const bytes = await archive();
    const blob = await exportAnnotatedEpub(bytes, {} as Book, []);
    const output = await JSZip.loadAsync(await blob.arrayBuffer());
    expect(Object.keys(output.files)[0]).toBe('mimetype');
    expect(await output.file('chapter.xhtml')!.async('string')).toBe(xml);
    expect(await output.file('mimetype')!.async('string')).toBe('application/epub+zip');
    expect(await (await JSZip.loadAsync(bytes)).file('chapter.xhtml')!.async('string')).toBe(xml);
  });
});
