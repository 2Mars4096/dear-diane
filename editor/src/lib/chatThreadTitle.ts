export function normalizeThreadTitleInput(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

export function getDisplayThreadTitle(
  title: string | null | undefined,
  fallback = "Untitled chat",
): string {
  const clean = normalizeThreadTitleInput(title ?? "");
  return clean || fallback;
}
