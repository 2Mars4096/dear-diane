// ---------------------------------------------------------------------------
// Snippet system + Monaco language feature registrations
// ---------------------------------------------------------------------------

interface Snippet {
  prefix: string;
  body: string[];
  description: string;
}

export const BUILT_IN_SNIPPETS: Record<string, Snippet[]> = {
  typescript: [
    { prefix: "func", body: ["function ${1:name}(${2:params}) {", "\t$0", "}"], description: "Function declaration" },
    { prefix: "afunc", body: ["async function ${1:name}(${2:params}) {", "\t$0", "}"], description: "Async function" },
    { prefix: "imp", body: ["import { $1 } from '${2:module}';$0"], description: "Import statement" },
    { prefix: "log", body: ["console.log($1);$0"], description: "Console log" },
    { prefix: "if", body: ["if (${1:condition}) {", "\t$0", "}"], description: "If statement" },
    { prefix: "ife", body: ["if (${1:condition}) {", "\t$2", "} else {", "\t$0", "}"], description: "If-else" },
    { prefix: "trycatch", body: ["try {", "\t$1", "} catch (${2:error}) {", "\t$0", "}"], description: "Try-catch block" },
    { prefix: "class", body: ["class ${1:Name} {", "\tconstructor(${2:params}) {", "\t\t$0", "\t}", "}"], description: "Class" },
    { prefix: "react", body: ["export default function ${1:Component}() {", "\treturn (", "\t\t<div>$0</div>", "\t);", "}"], description: "React component" },
    { prefix: "useState", body: ["const [${1:state}, set${1/(.*)/${1:/capitalize}/}] = useState(${2:initial});$0"], description: "React useState" },
    { prefix: "useEffect", body: ["useEffect(() => {", "\t$0", "}, [${1:deps}]);"], description: "React useEffect" },
    { prefix: "forof", body: ["for (const ${1:item} of ${2:iterable}) {", "\t$0", "}"], description: "For-of loop" },
    { prefix: "switch", body: ["switch (${1:key}) {", "\tcase ${2:value}:", "\t\t$0", "\t\tbreak;", "\tdefault:", "\t\tbreak;", "}"], description: "Switch statement" },
    { prefix: "promise", body: ["new Promise((resolve, reject) => {", "\t$0", "})"], description: "Promise" },
    { prefix: "arrow", body: ["const ${1:name} = (${2:params}) => {", "\t$0", "};"], description: "Arrow function" },
  ],
  javascript: [
    { prefix: "func", body: ["function ${1:name}(${2:params}) {", "\t$0", "}"], description: "Function declaration" },
    { prefix: "afunc", body: ["async function ${1:name}(${2:params}) {", "\t$0", "}"], description: "Async function" },
    { prefix: "imp", body: ["import { $1 } from '${2:module}';$0"], description: "Import statement" },
    { prefix: "req", body: ["const ${1:name} = require('${2:module}');$0"], description: "Require" },
    { prefix: "log", body: ["console.log($1);$0"], description: "Console log" },
    { prefix: "if", body: ["if (${1:condition}) {", "\t$0", "}"], description: "If statement" },
    { prefix: "trycatch", body: ["try {", "\t$1", "} catch (${2:error}) {", "\t$0", "}"], description: "Try-catch block" },
    { prefix: "class", body: ["class ${1:Name} {", "\tconstructor(${2:params}) {", "\t\t$0", "\t}", "}"], description: "Class" },
    { prefix: "forof", body: ["for (const ${1:item} of ${2:iterable}) {", "\t$0", "}"], description: "For-of loop" },
  ],
  python: [
    { prefix: "def", body: ["def ${1:name}(${2:params}):", "\t$0"], description: "Function" },
    { prefix: "adef", body: ["async def ${1:name}(${2:params}):", "\t$0"], description: "Async function" },
    { prefix: "class", body: ["class ${1:Name}:", "\tdef __init__(self${2:, params}):", "\t\t$0"], description: "Class" },
    { prefix: "if", body: ["if ${1:condition}:", "\t$0"], description: "If statement" },
    { prefix: "ife", body: ["if ${1:condition}:", "\t$2", "else:", "\t$0"], description: "If-else" },
    { prefix: "for", body: ["for ${1:item} in ${2:iterable}:", "\t$0"], description: "For loop" },
    { prefix: "while", body: ["while ${1:condition}:", "\t$0"], description: "While loop" },
    { prefix: "with", body: ["with ${1:expression} as ${2:var}:", "\t$0"], description: "With statement" },
    { prefix: "try", body: ["try:", "\t$1", "except ${2:Exception} as ${3:e}:", "\t$0"], description: "Try-except" },
    { prefix: "tryf", body: ["try:", "\t$1", "except ${2:Exception} as ${3:e}:", "\t$4", "finally:", "\t$0"], description: "Try-except-finally" },
    { prefix: "main", body: ['if __name__ == "__main__":', "\t$0"], description: "Main guard" },
    { prefix: "dataclass", body: ["@dataclass", "class ${1:Name}:", "\t${2:field}: ${3:type}$0"], description: "Dataclass" },
    { prefix: "lcomp", body: ["[${1:expr} for ${2:item} in ${3:iterable}]$0"], description: "List comprehension" },
  ],
  rust: [
    { prefix: "fn", body: ["fn ${1:name}(${2:params}) ${3:-> ReturnType }{", "\t$0", "}"], description: "Function" },
    { prefix: "pfn", body: ["pub fn ${1:name}(${2:params}) ${3:-> ReturnType }{", "\t$0", "}"], description: "Public function" },
    { prefix: "struct", body: ["struct ${1:Name} {", "\t${2:field}: ${3:Type},$0", "}"], description: "Struct" },
    { prefix: "enum", body: ["enum ${1:Name} {", "\t$0", "}"], description: "Enum" },
    { prefix: "impl", body: ["impl ${1:Type} {", "\t$0", "}"], description: "Impl block" },
    { prefix: "trait", body: ["trait ${1:Name} {", "\t$0", "}"], description: "Trait" },
    { prefix: "match", body: ["match ${1:expr} {", "\t${2:pattern} => $0,", "}"], description: "Match" },
    { prefix: "test", body: ["#[test]", "fn ${1:test_name}() {", "\t$0", "}"], description: "Test function" },
  ],
  go: [
    { prefix: "func", body: ["func ${1:name}(${2:params}) ${3:returnType} {", "\t$0", "}"], description: "Function" },
    { prefix: "mfunc", body: ["func (${1:r} *${2:Type}) ${3:name}(${4:params}) ${5:returnType} {", "\t$0", "}"], description: "Method" },
    { prefix: "struct", body: ["type ${1:Name} struct {", "\t$0", "}"], description: "Struct" },
    { prefix: "iface", body: ["type ${1:Name} interface {", "\t$0", "}"], description: "Interface" },
    { prefix: "if", body: ["if ${1:condition} {", "\t$0", "}"], description: "If statement" },
    { prefix: "iferr", body: ["if err != nil {", "\t$0", "}"], description: "If err" },
    { prefix: "for", body: ["for ${1:i} := 0; ${1:i} < ${2:n}; ${1:i}++ {", "\t$0", "}"], description: "For loop" },
    { prefix: "forr", body: ["for ${1:i}, ${2:v} := range ${3:collection} {", "\t$0", "}"], description: "For range" },
    { prefix: "switch", body: ["switch ${1:expr} {", "case ${2:val}:", "\t$0", "default:", "}"], description: "Switch" },
  ],
  html: [
    { prefix: "html5", body: ['<!DOCTYPE html>', '<html lang="en">', "<head>", '\t<meta charset="UTF-8">', '\t<meta name="viewport" content="width=device-width, initial-scale=1.0">', "\t<title>${1:Document}</title>", "</head>", "<body>", "\t$0", "</body>", "</html>"], description: "HTML5 boilerplate" },
    { prefix: "div", body: ["<div${1: class=\"$2\"}>", "\t$0", "</div>"], description: "Div" },
    { prefix: "a", body: ['<a href="${1:url}">${2:text}</a>$0'], description: "Anchor" },
    { prefix: "img", body: ['<img src="${1:url}" alt="${2:description}" />$0'], description: "Image" },
    { prefix: "ul", body: ["<ul>", "\t<li>$0</li>", "</ul>"], description: "Unordered list" },
    { prefix: "input", body: ['<input type="${1:text}" name="${2:name}" ${3:placeholder="$4"} />$0'], description: "Input" },
    { prefix: "form", body: ['<form action="${1:url}" method="${2:post}">', "\t$0", "</form>"], description: "Form" },
  ],
  css: [
    { prefix: "flex", body: ["display: flex;", "align-items: ${1:center};", "justify-content: ${2:center};$0"], description: "Flexbox" },
    { prefix: "grid", body: ["display: grid;", "grid-template-columns: ${1:repeat(3, 1fr)};", "gap: ${2:1rem};$0"], description: "CSS Grid" },
    { prefix: "media", body: ["@media (${1:max-width}: ${2:768px}) {", "\t$0", "}"], description: "Media query" },
    { prefix: "var", body: ["var(--${1:name})$0"], description: "CSS variable" },
    { prefix: "transition", body: ["transition: ${1:all} ${2:0.3s} ${3:ease};$0"], description: "Transition" },
    { prefix: "animate", body: ["@keyframes ${1:name} {", "\tfrom { $2 }", "\tto { $0 }", "}"], description: "Keyframes" },
  ],
};

let snippetsRegistered = false;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function registerSnippetProviders(monaco: any): void {
  if (snippetsRegistered) return;
  snippetsRegistered = true;

  for (const [language, snippets] of Object.entries(BUILT_IN_SNIPPETS)) {
    monaco.languages.registerCompletionItemProvider(language, {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      provideCompletionItems(model: any, position: any) {
        const word = model.getWordUntilPosition(position);
        const range = {
          startLineNumber: position.lineNumber,
          endLineNumber: position.lineNumber,
          startColumn: word.startColumn,
          endColumn: word.endColumn,
        };
        return {
          suggestions: snippets
            .filter((s) => s.prefix.startsWith(word.word))
            .map((s) => ({
              label: s.prefix,
              kind: monaco.languages.CompletionItemKind.Snippet,
              documentation: s.description,
              detail: `Snippet: ${s.description}`,
              insertText: s.body.join("\n"),
              insertTextRules:
                monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
              range,
              sortText: `0_${s.prefix}`,
            })),
        };
      },
    });
  }
}

// ---------------------------------------------------------------------------
// Color provider for CSS/SCSS/LESS
// ---------------------------------------------------------------------------

const HEX_RE = /#([0-9a-fA-F]{3,8})\b/g;
const RGB_RE = /rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(?:\s*,\s*([\d.]+))?\s*\)/g;
const HSL_RE = /hsla?\(\s*(\d{1,3})\s*,\s*(\d{1,3})%\s*,\s*(\d{1,3})%(?:\s*,\s*([\d.]+))?\s*\)/g;

function hexToRgba(hex: string) {
  let h = hex.replace("#", "");
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  if (h.length === 4) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2] + h[3] + h[3];
  const r = parseInt(h.slice(0, 2), 16) / 255;
  const g = parseInt(h.slice(2, 4), 16) / 255;
  const b = parseInt(h.slice(4, 6), 16) / 255;
  const a = h.length >= 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1;
  return { red: r, green: g, blue: b, alpha: a };
}

function hslToRgb(h: number, s: number, l: number) {
  s /= 100;
  l /= 100;
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => {
    const k = (n + h / 30) % 12;
    return l - a * Math.max(Math.min(k - 3, 9 - k, 1), -1);
  };
  return { red: f(0), green: f(8), blue: f(4) };
}

function toHex(n: number): string {
  return Math.round(n * 255)
    .toString(16)
    .padStart(2, "0");
}

let colorProviderRegistered = false;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function registerColorProvider(monaco: any): void {
  if (colorProviderRegistered) return;
  colorProviderRegistered = true;

  const languages = ["css", "scss", "less", "html", "javascript", "typescript"];

  for (const lang of languages) {
    monaco.languages.registerColorProvider(lang, {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      provideDocumentColors(model: any) {
        const text = model.getValue();
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const colors: any[] = [];

        let match: RegExpExecArray | null;

        HEX_RE.lastIndex = 0;
        while ((match = HEX_RE.exec(text)) !== null) {
          const offset = match.index;
          const pos = model.getPositionAt(offset);
          const endPos = model.getPositionAt(offset + match[0].length);
          colors.push({
            color: hexToRgba(match[0]),
            range: {
              startLineNumber: pos.lineNumber,
              startColumn: pos.column,
              endLineNumber: endPos.lineNumber,
              endColumn: endPos.column,
            },
          });
        }

        RGB_RE.lastIndex = 0;
        while ((match = RGB_RE.exec(text)) !== null) {
          const offset = match.index;
          const pos = model.getPositionAt(offset);
          const endPos = model.getPositionAt(offset + match[0].length);
          colors.push({
            color: {
              red: parseInt(match[1]) / 255,
              green: parseInt(match[2]) / 255,
              blue: parseInt(match[3]) / 255,
              alpha: match[4] ? parseFloat(match[4]) : 1,
            },
            range: {
              startLineNumber: pos.lineNumber,
              startColumn: pos.column,
              endLineNumber: endPos.lineNumber,
              endColumn: endPos.column,
            },
          });
        }

        HSL_RE.lastIndex = 0;
        while ((match = HSL_RE.exec(text)) !== null) {
          const offset = match.index;
          const pos = model.getPositionAt(offset);
          const endPos = model.getPositionAt(offset + match[0].length);
          const rgb = hslToRgb(
            parseInt(match[1]),
            parseInt(match[2]),
            parseInt(match[3]),
          );
          colors.push({
            color: {
              ...rgb,
              alpha: match[4] ? parseFloat(match[4]) : 1,
            },
            range: {
              startLineNumber: pos.lineNumber,
              startColumn: pos.column,
              endLineNumber: endPos.lineNumber,
              endColumn: endPos.column,
            },
          });
        }

        return colors;
      },

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      provideColorPresentations(_model: any, colorInfo: any) {
        const { red, green, blue, alpha } = colorInfo.color;
        const r = Math.round(red * 255);
        const g = Math.round(green * 255);
        const b = Math.round(blue * 255);

        const presentations = [
          { label: `#${toHex(red)}${toHex(green)}${toHex(blue)}${alpha < 1 ? toHex(alpha) : ""}` },
        ];
        if (alpha < 1) {
          presentations.push({ label: `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(2)})` });
        } else {
          presentations.push({ label: `rgb(${r}, ${g}, ${b})` });
        }
        return presentations;
      },
    });
  }
}

// ---------------------------------------------------------------------------
// Linked editing for HTML tags
// ---------------------------------------------------------------------------

let linkedEditingRegistered = false;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function registerLinkedEditingProvider(monaco: any): void {
  if (linkedEditingRegistered) return;
  linkedEditingRegistered = true;

  monaco.languages.registerLinkedEditingRangeProvider("html", {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    provideLinkedEditingRanges(model: any, position: any) {
      const line = model.getLineContent(position.lineNumber);
      const col = position.column - 1;

      const openTagRe = /<(\w[\w-]*)/g;
      const closeTagRe = /<\/(\w[\w-]*)/g;

      let tagName: string | null = null;
      let isOpen = false;
      let tagCol = 0;

      let m: RegExpExecArray | null;
      openTagRe.lastIndex = 0;
      while ((m = openTagRe.exec(line)) !== null) {
        const start = m.index + 1;
        const end = start + m[1].length;
        if (col >= start && col <= end) {
          tagName = m[1];
          isOpen = true;
          tagCol = start;
          break;
        }
      }
      if (!tagName) {
        closeTagRe.lastIndex = 0;
        while ((m = closeTagRe.exec(line)) !== null) {
          const start = m.index + 2;
          const end = start + m[1].length;
          if (col >= start && col <= end) {
            tagName = m[1];
            isOpen = false;
            tagCol = start;
            break;
          }
        }
      }

      if (!tagName) return null;

      const text = model.getValue();
      const selfClosingTags = new Set([
        "br", "hr", "img", "input", "meta", "link", "area", "base",
        "col", "embed", "param", "source", "track", "wbr",
      ]);
      if (selfClosingTags.has(tagName.toLowerCase())) return null;

      const allTagsRe = new RegExp(`<(/?)${tagName}(?=[\\s>/])`, "g");
      const tags: Array<{
        line: number;
        col: number;
        isClose: boolean;
        offset: number;
      }> = [];

      allTagsRe.lastIndex = 0;
      let tm: RegExpExecArray | null;
      while ((tm = allTagsRe.exec(text)) !== null) {
        const pos = model.getPositionAt(tm.index + (tm[1] ? 2 : 1));
        tags.push({
          line: pos.lineNumber,
          col: pos.column,
          isClose: !!tm[1],
          offset: tm.index,
        });
      }

      const currentTag = tags.find(
        (t) =>
          t.line === position.lineNumber &&
          t.col === tagCol + 1 &&
          t.isClose === !isOpen,
      ) ?? tags.find(
        (t) =>
          t.line === position.lineNumber &&
          t.col === tagCol + 1,
      );
      if (!currentTag) return null;

      let matchTag: typeof tags[0] | undefined;
      if (isOpen) {
        let depth = 0;
        for (const t of tags) {
          if (t.offset <= currentTag.offset) {
            if (!t.isClose) depth++;
            continue;
          }
          if (t.isClose) {
            depth--;
            if (depth === 0) {
              matchTag = t;
              break;
            }
          } else {
            depth++;
          }
        }
      } else {
        let depth = 0;
        for (let i = tags.length - 1; i >= 0; i--) {
          const t = tags[i];
          if (t.offset >= currentTag.offset) {
            if (t.isClose) depth++;
            continue;
          }
          if (!t.isClose) {
            depth--;
            if (depth === 0) {
              matchTag = t;
              break;
            }
          } else {
            depth++;
          }
        }
      }

      if (!matchTag) return null;

      const ranges = [
        {
          startLineNumber: currentTag.line,
          startColumn: currentTag.col,
          endLineNumber: currentTag.line,
          endColumn: currentTag.col + tagName.length,
        },
        {
          startLineNumber: matchTag.line,
          startColumn: matchTag.col,
          endLineNumber: matchTag.line,
          endColumn: matchTag.col + tagName.length,
        },
      ];

      return { ranges, wordPattern: /[\w-]+/ };
    },
  });
}

// ---------------------------------------------------------------------------
// Emmet abbreviation expansion (common subset)
// ---------------------------------------------------------------------------

interface EmmetNode {
  tag: string;
  id: string;
  classes: string[];
  text: string;
  repeat: number;
  children: EmmetNode[];
}

function parseEmmetToken(token: string): EmmetNode {
  const node: EmmetNode = {
    tag: "div",
    id: "",
    classes: [],
    text: "",
    repeat: 1,
    children: [],
  };

  let rest = token;

  const repeatMatch = rest.match(/\*(\d+)$/);
  if (repeatMatch) {
    node.repeat = parseInt(repeatMatch[1]);
    rest = rest.slice(0, -repeatMatch[0].length);
  }

  const textMatch = rest.match(/\{([^}]*)\}/);
  if (textMatch) {
    node.text = textMatch[1];
    rest = rest.replace(textMatch[0], "");
  }

  const parts = rest.split(/(?=[.#])/);
  for (const part of parts) {
    if (part.startsWith("#")) {
      node.id = part.slice(1);
    } else if (part.startsWith(".")) {
      node.classes.push(part.slice(1));
    } else if (part) {
      node.tag = part;
    }
  }

  return node;
}

function emmetNodeToHtml(node: EmmetNode, indent: number): string {
  const pad = "\t".repeat(indent);
  const attrs: string[] = [];
  if (node.id) attrs.push(`id="${node.id}"`);
  if (node.classes.length) attrs.push(`class="${node.classes.join(" ")}"`);
  const attrStr = attrs.length ? " " + attrs.join(" ") : "";

  const voidTags = new Set([
    "br", "hr", "img", "input", "meta", "link", "area",
    "base", "col", "embed", "param", "source", "track", "wbr",
  ]);

  const lines: string[] = [];
  for (let i = 0; i < node.repeat; i++) {
    if (voidTags.has(node.tag)) {
      lines.push(`${pad}<${node.tag}${attrStr} />`);
    } else if (node.children.length > 0) {
      lines.push(`${pad}<${node.tag}${attrStr}>`);
      for (const child of node.children) {
        lines.push(emmetNodeToHtml(child, indent + 1));
      }
      lines.push(`${pad}</${node.tag}>`);
    } else {
      const content = node.text || "$0";
      lines.push(`${pad}<${node.tag}${attrStr}>${content}</${node.tag}>`);
    }
  }
  return lines.join("\n");
}

function expandEmmet(abbrev: string): string | null {
  if (!abbrev || abbrev.length < 2) return null;
  if (/^[a-z]+$/i.test(abbrev) && !["div", "span", "p", "ul", "ol", "li", "header", "footer", "nav", "main", "section", "article", "aside", "form", "table", "tr", "td", "th", "thead", "tbody", "h1", "h2", "h3", "h4", "h5", "h6", "a", "button", "img", "input", "label", "select", "textarea"].includes(abbrev)) {
    return null;
  }

  const siblings = abbrev.split("+");
  const results: string[] = [];

  for (const sibling of siblings) {
    const nestParts = sibling.split(">");
    let rootNode: EmmetNode | null = null;
    let currentParent: EmmetNode | null = null;

    for (const part of nestParts) {
      const node = parseEmmetToken(part.trim());
      if (!rootNode) {
        rootNode = node;
        currentParent = node;
      } else if (currentParent) {
        currentParent.children.push(node);
        currentParent = node;
      }
    }

    if (rootNode) {
      results.push(emmetNodeToHtml(rootNode, 0));
    }
  }

  return results.length > 0 ? results.join("\n") : null;
}

const EMMET_PATTERN = /^[a-zA-Z][a-zA-Z0-9]*([.#][a-zA-Z][\w-]*)*(\*\d+)?(\{[^}]*\})?(>[a-zA-Z][a-zA-Z0-9]*([.#][a-zA-Z][\w-]*)*(\*\d+)?(\{[^}]*\})?)*(\+[a-zA-Z][a-zA-Z0-9]*([.#][a-zA-Z][\w-]*)*(\*\d+)?(\{[^}]*\})?)*$/;
const IMPLICIT_EMMET = /^[.#][a-zA-Z]/;

let emmetRegistered = false;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function registerEmmetProvider(monaco: any): void {
  if (emmetRegistered) return;
  emmetRegistered = true;

  const emmetLanguages = ["html", "javascript", "typescript"];

  for (const lang of emmetLanguages) {
    monaco.languages.registerCompletionItemProvider(lang, {
      triggerCharacters: [">", "+", "*", ".", "#", "{", "}"],
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      provideCompletionItems(model: any, position: any) {
        const lineContent = model.getLineContent(position.lineNumber);
        const textBeforeCursor = lineContent.substring(0, position.column - 1).trim();

        if (
          !EMMET_PATTERN.test(textBeforeCursor) &&
          !IMPLICIT_EMMET.test(textBeforeCursor)
        ) {
          return { suggestions: [] };
        }

        const expanded = expandEmmet(textBeforeCursor);
        if (!expanded) return { suggestions: [] };

        const wordStart = lineContent.lastIndexOf(textBeforeCursor, position.column - 1);
        const range = {
          startLineNumber: position.lineNumber,
          endLineNumber: position.lineNumber,
          startColumn: wordStart + 1,
          endColumn: position.column,
        };

        return {
          suggestions: [
            {
              label: `Emmet: ${textBeforeCursor}`,
              kind: monaco.languages.CompletionItemKind.Snippet,
              documentation: `Expand Emmet abbreviation: ${textBeforeCursor}`,
              insertText: expanded,
              insertTextRules:
                monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
              range,
              sortText: "0_emmet",
            },
          ],
        };
      },
    });
  }
}

// ---------------------------------------------------------------------------
// Utility: detect language from file path
// ---------------------------------------------------------------------------

export function detectLanguageFromPath(filePath: string): string {
  const ext = filePath.split(".").pop()?.toLowerCase() ?? "";
  const MAP: Record<string, string> = {
    ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript",
    py: "python", rs: "rust", go: "go", json: "json", md: "markdown",
    html: "html", css: "css", scss: "scss", yaml: "yaml", yml: "yaml",
    toml: "toml", sh: "shell", bash: "shell", zsh: "shell", sql: "sql",
    graphql: "graphql", xml: "xml", svg: "xml", txt: "plaintext",
  };
  return MAP[ext] ?? "plaintext";
}
