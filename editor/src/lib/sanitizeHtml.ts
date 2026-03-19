import createDOMPurify from "dompurify";

const EXTRA_ALLOWED_ATTR = [
  "class",
  "target",
  "rel",
  "title",
  "hidden",
  "value",
  "type",
  "viewBox",
  "fill",
  "stroke",
  "stroke-width",
  "stroke-linecap",
  "stroke-linejoin",
  "d",
  "x",
  "y",
  "cx",
  "cy",
  "r",
  "rx",
  "ry",
  "points",
  "aria-hidden",
  "aria-label",
];

export function sanitizeHtml(html: string): string {
  if (typeof window === "undefined") return html;

  const DOMPurify = createDOMPurify(window);
  return DOMPurify.sanitize(html, {
    ALLOW_DATA_ATTR: true,
    ADD_ATTR: EXTRA_ALLOWED_ATTR,
    RETURN_TRUSTED_TYPE: false,
  });
}

