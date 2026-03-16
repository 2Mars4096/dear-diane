export function normalizeThreadTitleInput(value: string): string {
  const clean = value.replace(/\s+/g, " ").trim();
  // Guard against persisted literal placeholders from older payloads.
  if (/^(undefined|null)$/i.test(clean)) return "";
  return clean;
}

const PATH_RE = /(?:^|[\s(])((?:~?\/|\/)[^\s,;:()]+)/g;
const LEADING_REQUEST_RE =
  /^(?:(?:can you|could you|would you|please|i need you to|in this folder)[\s,:-]*)+/i;
const HELP_ME_ACTION_RE =
  /^help me\s+(?=(?:write|create|build|draft|prepare|analyze|review|research|summarize)\b)/i;
const LEADING_ACTION_RE =
  /^(?:write|create|build|draft|prepare|analyze|review|research|summarize)\s+(?:(?:me|us)\s+)?(?:(?:a|an|the)\s+)?/i;

export function deriveDraftThreadTitleFromMessage(
  value: string,
  fallback = "New conversation",
): string {
  let clean = normalizeThreadTitleInput(value);
  clean = clean.replace(/^#{1,6}\s+/, "");
  clean = clean.replace(/^\*\*(.+?)\*\*/, "$1");
  clean = clean.replace(PATH_RE, " ");
  clean = normalizeThreadTitleInput(clean).replace(/^[,:\-\s]+|[,:\-\s]+$/g, "");

  let previous = "";
  while (clean && clean !== previous) {
    previous = clean;
    clean = clean.replace(LEADING_REQUEST_RE, "");
    clean = clean.replace(/^[,:\-\s]+|[,:\-\s]+$/g, "");
  }

  clean = clean.split(/[.;,]/, 1)[0] ?? clean;
  clean = clean.replace(HELP_ME_ACTION_RE, "");
  clean = clean.replace(LEADING_ACTION_RE, "");
  clean = normalizeThreadTitleInput(clean).replace(/^[,:\-\s]+|[,:\-\s]+$/g, "");

  if (!clean) return fallback;
  return clean.length > 50 ? clean.slice(0, 50).trimEnd() + "…" : clean;
}

export function getDisplayThreadTitle(
  title: string | null | undefined,
  fallback = "Untitled chat",
): string {
  const clean = normalizeThreadTitleInput(title ?? "");
  return clean || fallback;
}
