import type { PDFDocumentProxy } from 'pdfjs-dist/types/src/pdf';
import type { PdfSelectionAnchor } from './paper-comments';
import { selectionImage } from './selection-image';

export async function transcribeSelection(document: PDFDocumentProxy, selection: PdfSelectionAnchor): Promise<PdfSelectionAnchor> {
  if (!navigator.onLine) throw Error('Offline. The quote can be transcribed when you reconnect.');
  const blob = await selectionImage(document, selection, true);
  const dataUrl = (blob: Blob) => new Promise<string>((resolve, reject) => {
    const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(Error('Could not read the selected image.')); reader.readAsDataURL(blob);
  });
  const image = await dataUrl(blob);
  const response = await fetch('/api/reader/transcribe', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ image, text: selection.originalQuote ?? selection.quote }), signal: AbortSignal.timeout(65000) });
  const result = await response.json();
  if (!response.ok || typeof result.text !== 'string' || !result.text.trim()) throw Error(result.detail || 'Could not transcribe the selection. Try again.');
  return { ...selection, quote: result.text, quoteSource: 'vision', quoteModel: result.model, originalQuote: selection.originalQuote ?? selection.quote };
}
