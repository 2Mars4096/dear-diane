import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

// Electron's virtual ASAR directories must not intercept bundle hashing/removal.
const load = createRequire(path.join(process.cwd(), "package.json"));
export const physicalFs: typeof fs = process.versions.electron ? load("original-fs") : fs;
