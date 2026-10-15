// @vitest-environment happy-dom
import { expect, it, vi } from 'vitest';
import type Rendition from 'epubjs/types/rendition';
import { applyEpubTypography } from '../lib/epub-typography';

it('scopes styling to reader mode and limits underlines to links when enabled', () => {
 const registerCss=vi.fn(),select=vi.fn();
 const rendition={themes:{register:vi.fn(),registerCss,select}} as unknown as Rendition;
 applyEpubTypography(rendition,'reader');
 let css=registerCss.mock.calls.at(-1)![1] as string;
 expect(css).toContain('body.diane-reader');
 expect(css).toMatch(/\.diane-reader a\[href\]\{[^}]*text-decoration:none !important/);
 expect(css).toMatch(/\.diane-reader a:not\(\[href\]\)\{text-decoration:none !important/);
 applyEpubTypography(rendition,'reader',true);
 css=registerCss.mock.calls.at(-1)![1] as string;
 expect(css).toMatch(/\.diane-reader a\[href\]\{[^}]*text-decoration:underline !important/);
 applyEpubTypography(rendition,'original',true);
 expect(select).toHaveBeenLastCalledWith('diane-original');
 expect(registerCss.mock.calls.every(([name])=>name==='diane-reader')).toBe(true);
});
