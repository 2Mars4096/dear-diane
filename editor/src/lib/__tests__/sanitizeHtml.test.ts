// @vitest-environment jsdom

import { describe, expect, it } from "vitest";

import { sanitizeHtml } from "../sanitizeHtml";

describe("sanitizeHtml", () => {
  it("strips event handlers and javascript urls", () => {
    const html = sanitizeHtml(
      '<a href="javascript:alert(1)" onclick="alert(1)">bad</a><img src="x" onerror="alert(1)" /><svg><g onload="alert(1)"></g></svg>',
    );

    expect(html).not.toContain("onclick");
    expect(html).not.toContain("onerror");
    expect(html).not.toContain("onload");
    expect(html).not.toContain("javascript:alert");
  });

  it("preserves safe data attributes used by chat renderers", () => {
    const html = sanitizeHtml(
      '<button data-mention-type="file" data-mention-id="abc" class="chip">Open</button><textarea data-code-raw="1" hidden>echo hi</textarea>',
    );

    expect(html).toContain('data-mention-type="file"');
    expect(html).toContain('data-mention-id="abc"');
    expect(html).toContain('data-code-raw="1"');
    expect(html).toContain("<textarea");
  });
});

