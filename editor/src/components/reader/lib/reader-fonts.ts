export const READER_FONTS = {
  georgia: { label: 'Classic serif · Georgia', family: 'Georgia, "Times New Roman", serif' },
  literata: { label: 'Book serif · Literata', family: '"Diane Literata", Georgia, serif' },
  atkinson: { label: 'Clear sans · Atkinson', family: '"Diane Atkinson", Arial, sans-serif' },
  system: { label: 'System sans', family: 'system-ui, sans-serif' },
  typewriter: { label: 'Typewriter', family: '"Courier New", Courier, monospace' },
  handwritten: { label: 'Handwritten · Kalam', family: '"Diane Kalam", cursive' },
};
export type ReaderFont = keyof typeof READER_FONTS;
export const readerFont = (value: unknown): ReaderFont => typeof value === 'string' && Object.hasOwn(READER_FONTS, value) ? value as ReaderFont : 'georgia';
const cache = new Map<ReaderFont, Promise<string>>();

/** Blob URLs keep bundled fonts usable inside the inert EPUB iframe CSP. */
export function loadReaderFont(font: ReaderFont): Promise<string> {
  const files = font === 'literata' ? [['literata-roman.ttf', 'normal', '200 900'], ['literata-italic.ttf', 'italic', '200 900']]
    : font === 'atkinson' ? [['atkinson-roman.ttf', 'normal', '200 800'], ['atkinson-italic.ttf', 'italic', '200 800']]
    : font === 'handwritten' ? [['kalam-regular.ttf', 'normal', '400'], ['kalam-bold.ttf', 'normal', '700']] : [];
  if (!files.length) return Promise.resolve('');
  if (!cache.has(font)) {
    const family = font === 'literata' ? 'Diane Literata' : font === 'atkinson' ? 'Diane Atkinson' : 'Diane Kalam';
    cache.set(font, (async () => {
      const urls: string[] = [];
      try {
        const faces: string[] = [];
        for (const [file, style, weight] of files) {
          const response = await fetch(`/fonts/reader/${file}`);
          if (!response.ok) throw new Error('Reading font could not be loaded.');
          const url = URL.createObjectURL(await response.blob()); urls.push(url);
          faces.push(`@font-face{font-family:"${family}";src:url("${url}") format("truetype");font-style:${style};font-weight:${weight};font-display:swap;}`);
        }
        return faces.join('\n');
      } catch (error) { urls.forEach(URL.revokeObjectURL); cache.delete(font); throw error; }
    })());
  }
  return cache.get(font)!;
}
