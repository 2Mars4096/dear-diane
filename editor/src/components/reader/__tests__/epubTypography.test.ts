// @vitest-environment happy-dom
import { expect, it, vi } from 'vitest';
import type Rendition from 'epubjs/types/rendition';
import { applyEpubTypography, setEpubLinkUnderlines } from '../lib/epub-typography';

it('scopes styling to reader mode and limits underlines to links when enabled', () => {
 const registerCss=vi.fn(),select=vi.fn(),override=vi.fn();
 const registerContent=vi.fn();
 const rendition={hooks:{content:{register:registerContent}},themes:{register:vi.fn(),registerCss,select,override}} as unknown as Rendition;
 applyEpubTypography(rendition,'reader');
 let css=registerCss.mock.calls.at(-1)![1] as string;
 expect(css).toContain('body.diane-reader');
 expect(css).toContain('text-decoration:var(--diane-link-decoration, none) !important');
 expect(override).toHaveBeenLastCalledWith('--diane-link-decoration','none');
 expect(css).toMatch(/\.diane-reader a:not\(\[href\]\)\{text-decoration:none !important/);
 applyEpubTypography(rendition,'reader',true);
 css=registerCss.mock.calls.at(-1)![1] as string;
 expect(override).toHaveBeenLastCalledWith('--diane-link-decoration','underline');
 registerCss.mockClear(); select.mockClear();
 setEpubLinkUnderlines(rendition,false);
 expect(registerCss).not.toHaveBeenCalled(); expect(select).not.toHaveBeenCalled();
 expect(override).toHaveBeenLastCalledWith('--diane-link-decoration','none');
 applyEpubTypography(rendition,'original',true);
 expect(select).toHaveBeenLastCalledWith('diane-original');
 expect(registerCss.mock.calls.every(([name])=>name==='diane-reader')).toBe(true);
 expect(registerContent).toHaveBeenCalledOnce();
 const inject=vi.fn(); registerContent.mock.calls[0][0]({addStylesheetCss:inject});
 expect(inject).toHaveBeenCalledWith(expect.stringContaining('body.diane-reader'),'diane-reader');
});
