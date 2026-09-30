// Copies tesseract.js worker/core and the bundled English model into public/tesseract so the
// Reader can OCR scanned pages offline. Runs before dev/build; the folder is gitignored.
import { copyFileSync, cpSync, mkdirSync, existsSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const root = dirname(dirname(fileURLToPath(import.meta.url)));
const out = join(root, "public", "tesseract");
mkdirSync(out, { recursive: true });
const copies = [
  ["node_modules/tesseract.js/dist/worker.min.js", "worker.min.js"],
  ["node_modules/tesseract.js-core/tesseract-core-simd-lstm.wasm.js", "tesseract-core-simd-lstm.wasm.js"],
  ["node_modules/tesseract.js-core/tesseract-core-simd-lstm.wasm", "tesseract-core-simd-lstm.wasm"],
  ["node_modules/tesseract.js-core/tesseract-core-lstm.wasm.js", "tesseract-core-lstm.wasm.js"],
  ["node_modules/tesseract.js-core/tesseract-core-lstm.wasm", "tesseract-core-lstm.wasm"],
];
for (const language of ["eng", "chi_sim", "chi_tra"]) {
  const langDir = join(root, `node_modules/@tesseract.js-data/${language}/4.0.0_best_int`);
  for (const name of readdirSync(langDir)) if (name.startsWith(`${language}.`)) copies.push([join(langDir, name), name]);
}
for (const [from, to] of copies) {
  const source = from.startsWith("/") ? from : join(root, from);
  if (!existsSync(source)) { console.warn(`copy-ocr-assets: missing ${from}`); continue; }
  copyFileSync(source, join(out, to));
}
// pdf.js font data: without it, ligatures and standard-font glyphs render blank.
for (const folder of ["cmaps", "standard_fonts", "wasm"]) {
  const from = join(root, "node_modules/pdfjs-dist", folder);
  if (existsSync(from)) cpSync(from, join(root, "public", "pdfjs", folder), { recursive: true });
}
console.log(`copy-ocr-assets: ${copies.length} files → public/tesseract`);
