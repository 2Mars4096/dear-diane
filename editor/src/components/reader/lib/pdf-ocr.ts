// OCR span types from learning-assistant lib/materials.ts. Diane has no OCR import yet,
// so the reader passes no spans and pdf.js supplies the native text layer.
export type PdfOcrTextSpan = {
  confidence: number | null;
  height: number;
  left: number;
  text: string;
  top: number;
  width: number;
};

export type MaterialPdfOcrPage = {
  layout_version?: number;
  layout_mode?: "auto" | "single" | "spread";
  split?: number | null;
  page_number: number;
  spans: PdfOcrTextSpan[];
};
