import { useMemo, type MouseEvent } from "react";
import katex from "katex";
import "katex/dist/katex.min.css";
import { Marked, Renderer } from "marked";
import hljs from "../../lib/hljs";
import { sanitizeHtml } from "../../lib/sanitizeHtml";

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

const INLINE_CODE_BASE_CLASS =
  "dan-markdown-inline-code break-words font-mono text-[0.88em]";
const INLINE_CODE_STYLE_BY_KIND = {
  code: "dan-markdown-inline-symbol px-0.5 font-semibold text-slate-800 dark:text-slate-100",
  file: "dan-markdown-inline-file rounded border border-amber-200/70 bg-amber-50/65 px-1 py-0.5 font-semibold text-amber-950 dark:border-amber-800/70 dark:bg-amber-950/25 dark:text-amber-100",
  snippet:
    "dan-markdown-inline-snippet px-0.5 font-medium text-slate-700 dark:text-slate-200",
  url: "dan-markdown-inline-url rounded bg-sky-50/45 px-1 py-0.5 font-medium text-sky-800 underline decoration-sky-300 underline-offset-2 dark:bg-sky-950/25 dark:text-sky-100 dark:decoration-sky-700",
};
const MATH_PLACEHOLDER_RE = /(\u0000DAN_MD_\d+\u0000)/g;
const MATH_PLACEHOLDER_PART_RE = /^\u0000DAN_MD_\d+\u0000$/;
const PROSE_DOTTED_ABBREVIATIONS = new Set(["a.m", "e.g", "i.e", "n.b", "p.m", "p.s", "u.k", "u.s"]);
const AUTO_CODE_TOKEN_RE =
  /(^|[^\w./$-])((?:[\w.-]+\/)*[\w.-]+\.(?:c|cc|cpp|css|csv|gd|go|h|hpp|html|ini|java|json|jsx|log|md|mdx|py|rs|sh|sql|toml|ts|tsx|txt|xml|ya?ml)\b|_?[A-Za-z][A-Za-z0-9_]*(?:\._?[A-Za-z][A-Za-z0-9_]*)+(?:\(\))?|_[A-Za-z][A-Za-z0-9_]*(?:\(\))?|[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b|[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+\b)(?=$|[^\w/])/g;
const INLINE_FILE_EXT_RE =
  /\.(?:c|cc|cpp|css|csv|gd|go|h|hpp|html|ini|java|json|jsx|log|md|mdx|pdf|png|jpe?g|rs|sh|sql|svg|toml|ts|tsx|txt|xml|ya?ml)\b/i;

interface MarkdownRenderOptions {
  autoHighlightCode?: boolean;
}

function shouldAutoHighlightCodeToken(source: string, tokenStart: number, token: string): boolean {
  if (token.length > 72) return false;
  if (PROSE_DOTTED_ABBREVIATIONS.has(token.replace(/\.+$/, "").toLowerCase())) return false;

  const tokenEnd = tokenStart + token.length;
  const nextCharacter = source[tokenEnd] ?? "";
  if ((nextCharacter === "(" || nextCharacter === "[") && !token.endsWith("()")) return false;

  return true;
}

function inlineCodeKind(text: string): keyof typeof INLINE_CODE_STYLE_BY_KIND {
  const trimmed = text.trim();
  if (/^https?:\/\//i.test(trimmed)) return "url";
  if (
    trimmed.length <= 96 &&
    !/\s/.test(trimmed) &&
    (INLINE_FILE_EXT_RE.test(trimmed) ||
      /[/\\]/.test(trimmed) ||
      /^(?:README|AGENTS|PRODUCT|package|tsconfig|vite\.config)\b/i.test(trimmed))
  ) {
    return "file";
  }
  if (
    trimmed.length > 36 ||
    (/[\s;]/.test(trimmed) && /[{}=<>()]/.test(trimmed)) ||
    /^(?:html|css|js|ts|tsx|json|bash|sh|python|py)\s/i.test(trimmed)
  ) {
    return "snippet";
  }
  return "code";
}

function inlineCodeClass(text: string, auto = false) {
  const kind = inlineCodeKind(text);
  return [
    INLINE_CODE_BASE_CLASS,
    INLINE_CODE_STYLE_BY_KIND[kind],
    `dan-markdown-inline-${kind}`,
    auto ? "dan-markdown-auto-code" : "",
  ]
    .filter(Boolean)
    .join(" ");
}

function renderAutoCodeText(value: string): string {
  return value
    .split(MATH_PLACEHOLDER_RE)
    .map((part) => {
      if (!part) return "";
      if (MATH_PLACEHOLDER_PART_RE.test(part)) return part;
      return escapeHtml(part).replace(AUTO_CODE_TOKEN_RE, (match, prefix, token, offset, source) => {
        const tokenStart = offset + prefix.length;
        if (!shouldAutoHighlightCodeToken(source, tokenStart, token)) {
          return match;
        }
        return `${prefix}<code class="${inlineCodeClass(token, true)}">${token}</code>`;
      });
    })
    .join("");
}

function renderKatex(source: string, displayMode: boolean): string {
  try {
    return katex.renderToString(source.trim(), {
      displayMode,
      throwOnError: false,
      strict: false,
    });
  } catch {
    return `<code>${escapeHtml(source)}</code>`;
  }
}

function createRenderer(options: MarkdownRenderOptions = {}): Marked {
  const autoHighlightCode = options.autoHighlightCode !== false;
  const renderer = new Renderer();

  renderer.text = function text(token: any) {
    if (Array.isArray(token.tokens) && token.tokens.length > 0) {
      return this.parser.parseInline(token.tokens);
    }
    const text = String(token.text ?? token.raw ?? "");
    return autoHighlightCode ? renderAutoCodeText(text) : escapeHtml(text);
  };

  renderer.heading = function heading(token: any) {
    const text = this.parser.parseInline(token.tokens);
    const depth = Math.min(Math.max(Number(token.depth) || 1, 1), 6);
    const classes = [
      "mt-4 mb-2 text-xl font-semibold leading-tight text-slate-950 dark:text-slate-100",
      "mt-4 mb-2 text-base font-semibold leading-tight text-slate-900 dark:text-slate-100",
      "mt-3 mb-1 text-sm font-semibold leading-tight text-slate-900 dark:text-slate-100",
      "mt-3 mb-1 text-xs font-semibold uppercase tracking-[0.08em] text-slate-600 dark:text-slate-300",
      "mt-2 mb-1 text-xs font-semibold text-slate-600 dark:text-slate-300",
      "mt-2 mb-1 text-xs font-semibold text-slate-600 dark:text-slate-300",
    ];
    return `<h${depth} class="${classes[depth - 1]}">${text}</h${depth}>`;
  };

  renderer.paragraph = function paragraph(token: any) {
    const text = this.parser.parseInline(token.tokens);
    return `<p class="my-2 leading-6 text-slate-700 dark:text-slate-300">${text}</p>`;
  };

  renderer.code = ({ text, lang }: { text: string; lang?: string }) => {
    let highlighted = escapeHtml(text);
    try {
      highlighted =
        lang && hljs.getLanguage(lang)
          ? hljs.highlight(text, { language: lang }).value
          : hljs.highlightAuto(text).value;
    } catch {
      highlighted = escapeHtml(text);
    }
    const label = lang
      ? `<span class="dan-markdown-code-label absolute right-3 top-2 text-[10px] text-slate-400">${escapeHtml(lang)}</span>`
      : "";
    return (
      `<div class="dan-markdown-code-frame relative my-3 overflow-hidden rounded-md border border-slate-800 bg-slate-950">` +
      label +
      `<pre class="dan-markdown-code-scroll m-0 overflow-x-auto p-3 pr-12 text-xs leading-5 text-slate-100"><code>${highlighted}</code></pre>` +
      `</div>`
    );
  };

  renderer.codespan = ({ text }: { text: string }) =>
    `<code class="${inlineCodeClass(text)}">${escapeHtml(text)}</code>`;

  renderer.blockquote = function blockquote(token: any) {
    const body = this.parser.parse(token.tokens);
    return `<blockquote class="my-3 border-l-2 border-slate-300 pl-3 text-slate-600 dark:border-slate-600 dark:text-slate-300">${body}</blockquote>`;
  };

  renderer.list = function list(token: any) {
    let body = "";
    for (const item of token.items) body += this.listitem(item);
    const tag = token.ordered ? "ol" : "ul";
    const cls = token.ordered
      ? "my-2 ml-5 list-decimal space-y-1 text-slate-700 dark:text-slate-300"
      : "my-2 ml-5 list-disc space-y-1 text-slate-700 dark:text-slate-300";
    const start = token.ordered && token.start !== 1 ? ` start="${token.start}"` : "";
    return `<${tag} class="${cls}"${start}>${body}</${tag}>`;
  };

  renderer.listitem = function listitem(token: any) {
    let text = this.parser.parse(token.tokens);
    if (token.task) {
      text = `${this.checkbox({ type: "checkbox", raw: "", checked: !!token.checked })}${text}`;
    }
    return `<li class="leading-6">${text}</li>`;
  };

  renderer.table = function table(token: any) {
    let headerCells = "";
    for (const cell of token.header) headerCells += this.tablecell(cell);
    const header = this.tablerow({ text: headerCells });

    let rows = "";
    for (const row of token.rows) {
      let cells = "";
      for (const cell of row) cells += this.tablecell(cell);
      rows += this.tablerow({ text: cells });
    }

    return (
      `<div class="my-3 overflow-x-auto rounded-md border border-slate-200 dark:border-slate-700">` +
      `<table class="min-w-full border-collapse text-sm">` +
      `<thead class="bg-slate-50 dark:bg-slate-900">${header}</thead>` +
      `<tbody>${rows}</tbody>` +
      `</table></div>`
    );
  };

  renderer.tablerow = ({ text }: { text: string }) => `<tr>${text}</tr>`;

  renderer.tablecell = function tablecell(token: any) {
    const content = this.parser.parseInline(token.tokens);
    const tag = token.header ? "th" : "td";
    const cls = token.header
      ? "border-b border-slate-200 px-3 py-2 text-left text-xs font-semibold text-slate-600 dark:border-slate-700 dark:text-slate-300"
      : "border-b border-slate-100 px-3 py-2 text-slate-700 dark:border-slate-800 dark:text-slate-300";
    const style = token.align ? ` style="text-align:${token.align}"` : "";
    return `<${tag} class="${cls}"${style}>${content}</${tag}>`;
  };

  renderer.hr = () => `<hr class="my-4 border-slate-200 dark:border-slate-700" />`;

  renderer.link = function link(token: any) {
    const text = this.parser.parseInline(token.tokens);
    const href = String(token.href ?? "");
    if (/^\s*javascript\s*:/i.test(href)) return escapeHtml(text);
    const safeHref = /^(https?:|mailto:|#)/.test(href) ? href : "#";
    return `<a href="${safeHref}" target="_blank" rel="noopener noreferrer" class="text-slate-950 underline decoration-slate-300 underline-offset-2 hover:decoration-slate-900 dark:text-slate-100 dark:decoration-slate-600">${text}</a>`;
  };

  renderer.strong = function strong(token: any) {
    return `<strong class="font-semibold text-slate-950 dark:text-slate-100">${this.parser.parseInline(token.tokens)}</strong>`;
  };
  renderer.em = function em(token: any) {
    return `<em>${this.parser.parseInline(token.tokens)}</em>`;
  };
  renderer.del = function del(token: any) {
    return `<del>${this.parser.parseInline(token.tokens)}</del>`;
  };

  return new Marked({ renderer, async: false });
}

const markedInstance = createRenderer();
const markedNoAutoCodeInstance = createRenderer({ autoHighlightCode: false });

export function renderMarkdownToHtml(
  source: string,
  options: MarkdownRenderOptions = {},
): string {
  const preserved: string[] = [];
  const preserve = (html: string) => {
    preserved.push(html);
    return `\u0000DAN_MD_${preserved.length - 1}\u0000`;
  };

  let text = source
    .replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) =>
      preserve(`<div class="my-3 overflow-x-auto text-center">${renderKatex(tex, true)}</div>`),
    )
    .replace(/(?<!\$)\$(?!\$)([^\n$]+?)\$(?!\$)/g, (_, tex) =>
      preserve(renderKatex(tex, false)),
    );

  const instance = options.autoHighlightCode === false ? markedNoAutoCodeInstance : markedInstance;
  let html = instance.parse(text) as string;
  preserved.forEach((value, index) => {
    html = html.replaceAll(`\u0000DAN_MD_${index}\u0000`, value);
  });
  return sanitizeHtml(html);
}

export default function MarkdownRenderer({
  content,
  className = "",
  autoHighlightCode = true,
  onClick,
}: {
  content: string;
  className?: string;
  autoHighlightCode?: boolean;
  onClick?: (event: MouseEvent<HTMLDivElement>) => void;
}) {
  const html = useMemo(
    () => renderMarkdownToHtml(content, { autoHighlightCode }),
    [autoHighlightCode, content],
  );
  return (
    <div
      className={`dan-markdown text-sm ${className}`}
      dangerouslySetInnerHTML={{ __html: html }}
      onClick={onClick}
    />
  );
}
