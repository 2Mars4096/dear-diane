// Keep one typed preload source; Electron loads its compiled CommonJS output.
const { renameSync, copyFileSync } = require("node:fs");
const { resolve } = require("node:path");
const root = resolve(__dirname, "..");
renameSync(resolve(root, "dist-electron/preload.js"), resolve(root, "dist-electron/preload.cjs"));
copyFileSync(resolve(root, "electron/updateInstaller.cjs"), resolve(root, "dist-electron/updateInstaller.cjs"));
