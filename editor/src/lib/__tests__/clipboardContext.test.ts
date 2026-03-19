import { describe, expect, it } from "vitest";
import {
  formatCodeContextForChat,
  parseClipboardCodeContext,
  rememberCopiedCodeContext,
  resolveClipboardCodeContext,
} from "../clipboardContext";

describe("parseClipboardCodeContext", () => {
  it("parses plain-text header with single line", () => {
    const result = parseClipboardCodeContext(
      "// src/app.ts:L42\nconst x = 1;",
    );
    expect(result).toEqual({
      filePath: "src/app.ts",
      startLine: 42,
      endLine: 42,
      lang: "ts",
      code: "const x = 1;",
    });
  });

  it("parses plain-text header with line range", () => {
    const result = parseClipboardCodeContext(
      "// src/utils.py:L10-L25\ndef hello():\n    pass",
    );
    expect(result).toEqual({
      filePath: "src/utils.py",
      startLine: 10,
      endLine: 25,
      lang: "py",
      code: "def hello():\n    pass",
    });
  });

  it("parses HTML with data-dan attributes", () => {
    const html =
      '<pre data-dan-file="src/main.rs" data-dan-start="5" data-dan-end="10" data-dan-lang="rust"><code>fn main() {}</code></pre>';
    const result = parseClipboardCodeContext("// src/main.rs:L5-L10\nfn main() {}", html);
    expect(result).toEqual({
      filePath: "src/main.rs",
      startLine: 5,
      endLine: 10,
      lang: "rust",
      code: "fn main() {}",
    });
  });

  it("returns null for plain text without header", () => {
    expect(parseClipboardCodeContext("just some text")).toBeNull();
  });

  it("does not match comments that look like line references but lack a file extension", () => {
    expect(
      parseClipboardCodeContext("// TODO:Launch the app\nsome code"),
    ).toBeNull();
    expect(
      parseClipboardCodeContext("// config:L10 something\nsome code"),
    ).toBeNull();
  });

  it("handles &quot; entities in HTML code body", () => {
    const html =
      '<pre data-dan-file="test.ts" data-dan-start="1" data-dan-end="1" data-dan-lang="ts"><code>const s = &quot;hello&quot;;</code></pre>';
    const result = parseClipboardCodeContext("", html);
    expect(result?.code).toBe('const s = "hello";');
  });

  it("allows spaces in file paths for explicit plain-text headers", () => {
    const result = parseClipboardCodeContext(
      "// /Users/me/My Project/src/app.ts:L4-L6\nconst x = 1;",
    );
    expect(result).toEqual({
      filePath: "/Users/me/My Project/src/app.ts",
      startLine: 4,
      endLine: 6,
      lang: "ts",
      code: "const x = 1;",
    });
  });

  it("handles HTML entities in code", () => {
    const html =
      '<pre data-dan-file="test.tsx" data-dan-start="1" data-dan-end="1" data-dan-lang="tsx"><code>const x = a &lt; b &amp;&amp; c &gt; d;</code></pre>';
    const result = parseClipboardCodeContext("", html);
    expect(result?.code).toBe("const x = a < b && c > d;");
  });
});

describe("formatCodeContextForChat", () => {
  it("formats single-line context", () => {
    const result = formatCodeContextForChat({
      filePath: "src/app.ts",
      startLine: 42,
      endLine: 42,
      lang: "ts",
      code: "const x = 1;",
    });
    expect(result).toBe("`src/app.ts:L42`\n```ts\nconst x = 1;\n```");
  });

  it("formats multi-line context", () => {
    const result = formatCodeContextForChat({
      filePath: "src/utils.py",
      startLine: 10,
      endLine: 25,
      lang: "python",
      code: "def hello():\n    pass",
    });
    expect(result).toBe(
      "`src/utils.py:L10-L25`\n```python\ndef hello():\n    pass\n```",
    );
  });

  it("infers language from file extension when lang is empty", () => {
    const result = formatCodeContextForChat({
      filePath: "lib/helper.rb",
      startLine: 1,
      endLine: 3,
      lang: "",
      code: "def hi\n  puts 'hi'\nend",
    });
    expect(result).toContain("```rb\n");
  });
});

describe("resolveClipboardCodeContext", () => {
  it("falls back to the last copied editor context when plain text matches", () => {
    rememberCopiedCodeContext("const x = 1;", {
      filePath: "/tmp/demo.ts",
      startLine: 8,
      endLine: 10,
      lang: "ts",
      code: "const x = 1;",
    });

    expect(resolveClipboardCodeContext("const x = 1;")).toEqual({
      filePath: "/tmp/demo.ts",
      startLine: 8,
      endLine: 10,
      lang: "ts",
      code: "const x = 1;",
    });
  });
});
