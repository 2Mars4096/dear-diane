import { READER_FONTS, type ReaderFont } from "./reader-fonts";
import type Rendition from 'epubjs/types/rendition';
export type ReaderTextStyle = { family: ReaderFont; spacing: number; css: string };
export const DEFAULT_READER_TEXT: ReaderTextStyle = { family: 'georgia', spacing: 1.65, css: '' };
export type EpubTypography = 'reader' | 'original';

/** The same typographic rules must be used by visible and measuring renditions. */
export function applyEpubTypography(rendition: Rendition, mode: EpubTypography, underlineLinks = false, text: ReaderTextStyle = DEFAULT_READER_TEXT) {
  rendition.themes.register('diane-original', {});
  const rules: Record<string, Record<string, string>> = {
    body: { 'font-family': `${READER_FONTS[text.family].family} !important`, 'line-height': `${text.spacing} !important`, 'font-optical-sizing': 'auto' },
    'p,li,div,span,a,em,strong,b,i': { 'font-family': 'inherit !important', 'font-size': '1em !important', 'line-height': 'inherit !important', 'font-variant': 'normal !important', 'text-transform': 'none !important' },
    'h1,h2,h3,h4,h5,h6': { 'font-family': 'inherit !important', 'line-height': '1.3 !important', 'font-variant': 'normal !important', 'text-transform': 'none !important' },
    h1: { 'font-size': '1.6em !important' },
    h2: { 'font-size': '1.35em !important' },
    h3: { 'font-size': '1.2em !important' },
    'h4,h5,h6': { 'font-size': '1.1em !important' },
    'a:not([href])': { 'text-decoration': 'none !important' },
    'a[href]': { color: 'inherit !important', 'text-decoration': underlineLinks ? 'underline !important' : 'none !important', 'text-underline-offset': '0.15em' },
    'a[href]:hover,a[href]:focus-visible': { 'text-decoration': 'underline !important' },
    'img,svg,video': { 'max-width': '100% !important', height: 'auto' },
  };
  const css = Object.entries(rules).map(([selectors, declarations]) => {
    const scoped = selectors.split(',').map(selector => selector === 'body' ? 'body.diane-reader' : `.diane-reader ${selector}`).join(',');
    return `${scoped}{${Object.entries(declarations).map(([key, value]) => `${key}:${value}`).join(';')}}`;
  }).join('\n');
  // registerCss replaces the stylesheet; registerRules appends duplicates on every update.
  rendition.themes.registerCss('diane-reader', text.css + '\n' + css);
  rendition.themes.select(mode === 'reader' ? 'diane-reader' : 'diane-original');
}
