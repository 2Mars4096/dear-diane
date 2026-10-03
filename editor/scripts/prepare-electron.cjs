// Keep one typed preload source; Electron loads its compiled CommonJS output.
const { renameSync, copyFileSync, mkdirSync } = require("node:fs");
const { resolve } = require("node:path");
const root = resolve(__dirname, "..");
renameSync(resolve(root, "dist-electron/preload.js"), resolve(root, "dist-electron/preload.cjs"));
copyFileSync(resolve(root, "electron/updateInstaller.cjs"), resolve(root, "dist-electron/updateInstaller.cjs"));

// Keep native tray assets beside the compiled main process, including Retina scale.
mkdirSync(resolve(root, "dist-electron/icons"), { recursive: true });
for (const name of ["trayTemplate.png", "trayTemplate@2x.png"]) {
  copyFileSync(resolve(root, "resources/icons", name), resolve(root, "dist-electron/icons", name));
}
copyFileSync(resolve(root, "resources/icons/icon.png"), resolve(root, "dist-electron/icons/trayColor.png"));
