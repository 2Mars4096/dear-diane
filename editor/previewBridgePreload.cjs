const { ipcRenderer } = require("electron");

const STATE = {
  inspectMode: false,
  selectedElement: null,
  hoverElement: null,
  reportedIssues: new Set(),
};

const STYLE_ID = "dan-preview-bridge-style";
const ROOT_SELECTORS = [
  "main article",
  "article",
  "[role='main'] article",
  "[role='main']",
  "main",
  ".post-content",
  ".single-content",
  ".article-content",
  ".entry-content",
  ".content",
];

function ensureStyles() {
  if (document.getElementById(STYLE_ID)) return;
  if (document.documentElement) {
    document.documentElement.dataset.danEmbeddedPreview = "true";
  }
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
    html[data-dan-embedded-preview="true"],
    html[data-dan-embedded-preview="true"] body {
      min-height: 100% !important;
      overflow-y: auto !important;
    }

    :root[data-dan-preview-inspect="on"] [data-dan-preview-editable="true"] {
      cursor: text !important;
    }

    [data-dan-preview-editable-hover="true"] {
      outline: 2px solid rgba(33, 150, 243, 0.65);
      outline-offset: 6px;
      border-radius: 10px;
    }

    [data-dan-preview-editable-selected="true"] {
      outline: 3px solid rgba(16, 185, 129, 0.92);
      outline-offset: 8px;
      border-radius: 12px;
      box-shadow: 0 0 0 9999px rgba(15, 23, 42, 0.08);
    }

    @media (max-width: 1280px) {
      main {
        display: block !important;
        max-width: none !important;
      }

      .three-col {
        display: grid !important;
        grid-template-columns: minmax(0, 1fr) !important;
        gap: 0 !important;
        max-width: none !important;
        width: 100% !important;
      }

      .toc-sidebar,
      .notes-sidebar,
      .tag-pills,
      .edit-source-link,
      .export-pdf-btn,
      .export-bib-btn,
      .print-only,
      #print-toc,
      #print-footnotes {
        display: none !important;
      }

      .content-area {
        display: block !important;
        flex: none !important;
        order: 0 !important;
        grid-column: 1 !important;
        width: auto !important;
        max-width: none !important;
        min-width: 0 !important;
        padding: 2px 18px 48px !important;
      }

      .content-area.expanded {
        max-width: none !important;
        width: auto !important;
      }

      .page-nav {
        align-items: flex-start !important;
        gap: 8px !important;
        padding: 8px 18px !important;
      }

      .page-nav__back,
      .page-nav__siblings {
        display: none !important;
      }

      .page-nav__title {
        flex: 1 1 auto !important;
        min-width: 0 !important;
        text-align: left !important;
      }

      .page-nav__title h1 {
        font-size: clamp(1.45rem, 5vw, 2rem) !important;
        line-height: 1.24 !important;
      }

      .breadcrumbs {
        padding: 8px 18px 0 !important;
      }
    }
  `;
  document.head.appendChild(style);
}

function normalizeText(value) {
  return String(value || "")
    .replace(/\u00a0/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function reportRuntimeIssue(message) {
  const normalized = normalizeText(message);
  if (!normalized || STATE.reportedIssues.has(normalized)) return;
  STATE.reportedIssues.add(normalized);
  ipcRenderer.sendToHost("dan-preview-runtime-issue", { message: normalized });
}

function resetPageScroll() {
  if (window.location.hash) return;
  try {
    if ("scrollRestoration" in window.history) {
      window.history.scrollRestoration = "manual";
    }
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  } catch {
    window.scrollTo(0, 0);
  }
  document.documentElement.scrollTop = 0;
  if (document.body) {
    document.body.scrollTop = 0;
  }
}

function getEditableKind(element) {
  if (!(element instanceof Element)) return null;
  const tag = element.tagName.toLowerCase();
  if (/^h[1-6]$/.test(tag)) return "heading";
  if (tag === "li") return "list_item";
  if (tag === "blockquote") return "blockquote";
  if (tag === "p" && !element.closest("blockquote, li")) return "paragraph";
  return null;
}

function collectEditableElements(root) {
  if (!(root instanceof Element)) return [];
  return Array.from(root.querySelectorAll("h1, h2, h3, h4, h5, h6, li, blockquote, p"))
    .filter((element) => {
      const kind = getEditableKind(element);
      if (!kind) return false;
      element.dataset.danPreviewEditable = "true";
      return normalizeText(element.innerText || element.textContent || "").length > 0;
    });
}

function findContentRoot() {
  const candidates = ROOT_SELECTORS.flatMap((selector) =>
    Array.from(document.querySelectorAll(selector)),
  );
  let best = null;
  let bestScore = 0;

  for (const candidate of candidates) {
    const editables = collectEditableElements(candidate);
    const score = editables.length;
    if (score > bestScore) {
      best = candidate;
      bestScore = score;
    }
  }

  return bestScore >= 2 ? best : document.body;
}

function clearHoverElement() {
  if (STATE.hoverElement) {
    delete STATE.hoverElement.dataset.danPreviewEditableHover;
    STATE.hoverElement = null;
  }
}

function clearSelectedElement() {
  if (STATE.selectedElement) {
    delete STATE.selectedElement.dataset.danPreviewEditableSelected;
    STATE.selectedElement = null;
  }
}

function setHoverElement(element) {
  if (STATE.hoverElement === element) return;
  clearHoverElement();
  if (!element) return;
  element.dataset.danPreviewEditableHover = "true";
  STATE.hoverElement = element;
}

function setSelectedElement(element) {
  if (STATE.selectedElement === element) return;
  clearSelectedElement();
  if (!element) return;
  element.dataset.danPreviewEditableSelected = "true";
  STATE.selectedElement = element;
}

function resolveEditableTarget(startNode) {
  const root = findContentRoot();
  let element = startNode instanceof Element ? startNode : startNode?.parentElement;

  while (element && element !== document.body) {
    if (root.contains(element)) {
      const kind = getEditableKind(element);
      if (kind) {
        return { element, root, kind };
      }
    }
    element = element.parentElement;
  }

  return null;
}

function serializeSelection(element, root, kind) {
  const editables = collectEditableElements(root);
  const sameKind = editables.filter((entry) => getEditableKind(entry) === kind);
  return {
    kind,
    text: normalizeText(element.innerText || element.textContent || ""),
    allIndex: Math.max(editables.indexOf(element), 0),
    kindIndex: Math.max(sameKind.indexOf(element), 0),
    tagName: element.tagName,
  };
}

function syncInspectMode() {
  document.documentElement.dataset.danPreviewInspect = STATE.inspectMode
    ? "on"
    : "off";

  if (!STATE.inspectMode) {
    clearHoverElement();
    clearSelectedElement();
  }
}

window.addEventListener(
  "click",
  (event) => {
    if (!STATE.inspectMode) return;
    const resolved = resolveEditableTarget(event.target);
    if (!resolved) return;

    event.preventDefault();
    event.stopPropagation();
    setSelectedElement(resolved.element);
    ipcRenderer.sendToHost(
      "dan-preview-selection",
      serializeSelection(resolved.element, resolved.root, resolved.kind),
    );
  },
  true,
);

window.addEventListener(
  "mousemove",
  (event) => {
    if (!STATE.inspectMode) return;
    const resolved = resolveEditableTarget(event.target);
    setHoverElement(resolved?.element ?? null);
  },
  true,
);

window.addEventListener(
  "keydown",
  (event) => {
    if (!STATE.inspectMode || event.key !== "Escape") return;
    clearHoverElement();
    clearSelectedElement();
    ipcRenderer.sendToHost("dan-preview-selection-cleared");
  },
  true,
);

ipcRenderer.on("dan-preview:set-mode", (_event, payload) => {
  STATE.inspectMode = Boolean(payload && payload.inspectMode);
  syncInspectMode();
});

ipcRenderer.on("dan-preview:clear-selection", () => {
  clearHoverElement();
  clearSelectedElement();
});

window.addEventListener(
  "error",
  (event) => {
    const target = event.target;
    if (
      target instanceof HTMLScriptElement ||
      target instanceof HTMLLinkElement ||
      target instanceof HTMLImageElement
    ) {
      const url =
        target.currentSrc ||
        target.src ||
        target.href ||
        target.getAttribute("src") ||
        target.getAttribute("href") ||
        "";
      const label =
        target instanceof HTMLScriptElement
          ? "script"
          : target instanceof HTMLLinkElement
            ? "stylesheet"
            : "resource";
      reportRuntimeIssue(`Failed to load ${label}${url ? `: ${url}` : "."}`);
      return;
    }
    if (event.message) {
      reportRuntimeIssue(event.message);
    }
  },
  true,
);

window.addEventListener("unhandledrejection", (event) => {
  const reason =
    typeof event.reason === "string"
      ? event.reason
      : event.reason && typeof event.reason.message === "string"
        ? event.reason.message
        : null;
  if (reason) {
    reportRuntimeIssue(`Unhandled promise rejection: ${reason}`);
  }
});

function initializeBridge() {
  ensureStyles();
  resetPageScroll();
}

if (document.readyState === "loading") {
  window.addEventListener("DOMContentLoaded", initializeBridge, { once: true });
} else {
  initializeBridge();
}

window.addEventListener("load", () => window.setTimeout(resetPageScroll, 0), {
  once: true,
});
