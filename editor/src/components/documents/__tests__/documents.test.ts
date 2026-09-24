import { expect, it } from 'vitest';
import { decodeDocument, documentKind, pathDocument } from '../documents';
it('classifies viewable media and preserves file roots with spaces', () => {
  expect(documentKind('PAPER.PDF')).toBe('pdf');
  expect(documentKind('photo.png')).toBe('image');
  expect(documentKind('notes.md')).toBe('text');
  expect(documentKind('script.py')).toBe('text');
  expect(pathDocument('/tmp/my folder/notes.md','notes.md').root).toBe('/tmp/my folder');
});
it('rejects binary, invalid UTF-8, and oversized input without lossy decoding', () => {
  expect(() => decodeDocument(new Uint8Array([0xff]).buffer)).toThrow();
  expect(() => decodeDocument(new Uint8Array([65,0,66]).buffer)).toThrow();
  expect(() => decodeDocument(new ArrayBuffer(2_000_001))).toThrow();
  expect(decodeDocument(new TextEncoder().encode('\ufeff中文\r\n').buffer)).toBe('\ufeff中文\r\n');
});
