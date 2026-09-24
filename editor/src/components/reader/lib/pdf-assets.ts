/** Workers resolve relative asset paths against their script, not the app page. */
export function pdfAssetBase(base: string, pageUrl: string): string {
  return new URL('pdfjs/', new URL(base, pageUrl)).href;
}
