import { useEffect, useState } from 'react';
import type Book from 'epubjs/types/book';
import type { PdfSelectionAnchor } from './lib/paper-comments';

export function EpubReferencePreview({ book, selection }: { book: Book | null; selection?: PdfSelectionAnchor }) {
  const [image, setImage] = useState('');
  const [text, setText] = useState(selection?.quote || '');
  useEffect(() => {
    let cancelled = false; setImage(''); setText(selection?.quote || '');
    if (!book || !selection?.epub) return;
    const epub = selection.epub;
    void book.getRange(epub.cfi).then(async range => {
      if (cancelled || !range) return;
      if (range.collapsed) setText(range.startContainer.parentElement?.textContent?.slice(0, 700) || selection.quote);
      const illustration = range.cloneContents().querySelector('img');
      const src = illustration?.getAttribute('src');
      if (!src || selection.kind !== 'area') return;
      const chapter = new URL(book.resolve(epub.href), 'https://epub.invalid/');
      const asset = new URL(src, chapter);
      if (asset.origin !== 'https://epub.invalid') return;
      const url = await book.archive.createUrl(asset.pathname, { base64: false });
      if (!cancelled) setImage(url);
    }).catch(() => { /* The saved excerpt remains visible if the book changed. */ });
    return () => { cancelled = true; };
  }, [book, selection]);
  return <div className="wb-epub-ref-preview"><q>{text}</q>{image && <figure><img src={image} alt={selection?.quote || 'Saved illustration'} />{selection?.rects.map((rect, index) => <span key={index} style={{ left: `${rect.left * 100}%`, top: `${rect.top * 100}%`, width: `${rect.width * 100}%`, height: `${rect.height * 100}%` }} />)}</figure>}</div>;
}
