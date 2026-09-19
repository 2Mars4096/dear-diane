// Ported from learning-assistant lib/paper-history.ts. DAN has no material records,
// so a PDF's identity is its project path unless a content digest is supplied.
export type PaperHistoryMaterial = {
  material_id: string;
  source_asset?: { sha256?: string } | null;
};

export type PaperHistoryStorage = {
  getItem(key: string): string | null;
  removeItem?(key: string): void;
  setItem(key: string, value: string): void;
};

const PDF_SHA256_PATTERN = /^sha256-[a-f0-9]{64}$/;

export function paperHistoryIdentity(material: PaperHistoryMaterial): string {
  const digest = material.source_asset?.sha256 ?? "";
  return PDF_SHA256_PATTERN.test(digest)
    ? digest
    : `material-${material.material_id}`;
}

export function paperHistoryStorage(): PaperHistoryStorage | null {
  return (globalThis as typeof globalThis & {
    localStorage?: PaperHistoryStorage;
  }).localStorage ?? null;
}
