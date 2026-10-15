import type { ReactNode } from 'react';
import { READER_FONTS, type ReaderFont } from './lib/reader-fonts';
import type { EpubTypography } from './lib/epub-typography';
export type ReflowSettings = {
  typography: EpubTypography; setTypography: (value: EpubTypography) => void;
  font: number; setFont: (value: number) => void;
  family: ReaderFont; setFamily: (value: ReaderFont) => void;
  spacing: number; setSpacing: (value: number) => void;
  width: string; setWidth: (value: string) => void;
  underline: boolean; setUnderline: (value: boolean) => void;
  fixed?: boolean;
};
export function ReadingSettings({ layout, reflow, onReset }: { layout: ReactNode; reflow?: ReflowSettings; onReset: () => void }) {
  const unavailable = !reflow || reflow.fixed;
  const publisher = reflow?.typography === 'original';
  return <>
    <label>Layout{layout}</label>
    <label title={unavailable ? 'This document retains its original page typography' : undefined}>Book styling<select aria-label="Book styling" disabled={unavailable} value={reflow?.typography || 'original'} onChange={e => reflow?.setTypography(e.target.value as EpubTypography)}><option value="reader">Reader</option><option value="original">Publisher original</option></select></label>
    <label>Text size<select aria-label="Reading text size" disabled={unavailable} value={reflow?.font || 100} onChange={e => reflow?.setFont(Number(e.target.value))}>{Array.from({length:14},(_,i)=>70+i*10).map(value=><option key={value} value={value}>{value}%</option>)}</select></label>
    <fieldset disabled={unavailable || publisher} aria-label="Reader typography">
      <label>Font<select aria-label="Reading font" value={reflow?.family || 'georgia'} onChange={e=>reflow?.setFamily(e.target.value as ReaderFont)}>{Object.entries(READER_FONTS).map(([id,font])=><option key={id} value={id}>{font.label}</option>)}</select></label>
      <label>Line spacing<select aria-label="Line spacing" value={reflow?.spacing || 1.65} onChange={e=>reflow?.setSpacing(Number(e.target.value))}><option value={1.4}>Compact</option><option value={1.65}>Normal</option><option value={1.9}>Relaxed</option></select></label>
      <label>Underline links<input type="checkbox" aria-label="Underline links" checked={reflow?.underline || false} onChange={e=>reflow?.setUnderline(e.target.checked)} /></label>
    </fieldset>
    <label>Reading width<select aria-label="Reading width" disabled={unavailable} value={reflow?.width || 'comfortable'} onChange={e=>reflow?.setWidth(e.target.value)}><option value="comfortable">Comfortable</option><option value="wide">Wide</option></select></label>
    <p>{!reflow ? 'PDF text is fixed. Use Zoom in the toolbar; typography controls apply to reflowable EPUBs.' : reflow.fixed ? 'Fixed-layout books retain publisher typography.' : 'Page numbers follow text size and reading width. Publisher original keeps the book’s font and link styling.'}</p>
    <button type="button" onClick={onReset}>Reset reading settings</button>
  </>;
}
