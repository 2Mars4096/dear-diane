import type { ReactNode } from 'react';
import { READER_FONTS, type ReaderFont } from './lib/reader-fonts';
import type { EpubTypography } from './lib/epub-typography';
export type ReflowSettings = {
  typography: EpubTypography; setTypography: (value: EpubTypography) => void;
  family: ReaderFont; setFamily: (value: ReaderFont) => void;
  spacing: number; setSpacing: (value: number) => void;
  width: string; setWidth: (value: string) => void;
  underline: boolean; setUnderline: (value: boolean) => void;
  fixed?: boolean;
};
export function ReadingSettings({ layout, reflow, onReset }: { layout: ReactNode; reflow?: ReflowSettings; onReset: () => void }) {
  const unavailable = !reflow || reflow.fixed;
  const publisher = reflow?.typography === 'original';
  return <section className="wb-reading-preferences" aria-label="Reading preferences">
    <header><strong>Reading settings</strong></header>
    <div className="wb-reading-preference-section"><h4>Page</h4>
      <label>Layout{layout}</label>
      <label>Width<select aria-label="Reading width" disabled={unavailable} value={reflow?.width || 'comfortable'} onChange={e=>reflow?.setWidth(e.target.value)}><option value="comfortable">Comfortable</option><option value="wide">Wide</option></select></label>
    </div>
    <div className="wb-reading-preference-section"><h4>Text</h4>
      <label title={unavailable ? 'This document retains its original page typography' : undefined}>Style<select aria-label="Book styling" disabled={unavailable} value={reflow?.typography || 'original'} onChange={e => reflow?.setTypography(e.target.value as EpubTypography)}><option value="reader">Reader</option><option value="original">Publisher original</option></select></label>
      <fieldset disabled={unavailable || publisher} aria-label="Reader typography">
        <label>Font<select aria-label="Reading font" value={reflow?.family || 'georgia'} onChange={e=>reflow?.setFamily(e.target.value as ReaderFont)}>{Object.entries(READER_FONTS).map(([id,font])=><option key={id} value={id}>{font.label}</option>)}</select></label>
        <label>Spacing<select aria-label="Line spacing" value={reflow?.spacing || 1.65} onChange={e=>reflow?.setSpacing(Number(e.target.value))}><option value={1.4}>Compact</option><option value={1.65}>Normal</option><option value={1.9}>Relaxed</option></select></label>
        <label className="wb-reading-toggle"><span>Underline links</span><input type="checkbox" aria-label="Underline links" checked={reflow?.underline || false} onChange={e=>reflow?.setUnderline(e.target.checked)} /></label>
      </fieldset>
    </div>
    <p>{!reflow ? 'PDF text is fixed. Use Zoom in the toolbar; typography controls apply to reflowable EPUBs.' : reflow.fixed ? 'Fixed-layout books retain publisher typography.' : publisher ? 'Publisher original keeps the book’s font and link styling.' : 'Changes apply to this book. Page numbers adjust to the reading layout.'}</p>
    <footer><button type="button" onClick={onReset}>Reset reading settings</button></footer>
  </section>;
}
