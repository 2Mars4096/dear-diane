import { app, BrowserWindow, ipcMain, dialog, Tray, Menu, nativeImage } from "electron";
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;

const isDev = !app.isPackaged;
const VITE_DEV_URL = "http://localhost:5173";

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    title: "DAN",
    titleBarStyle: "hiddenInset",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (isDev) {
    mainWindow.loadURL(VITE_DEV_URL);
    mainWindow.webContents.openDevTools({ mode: "detach" });
  } else {
    mainWindow.loadFile(path.join(__dirname, "../dist/index.html"));
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function createTray() {
  const icon = nativeImage.createFromDataURL(
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAABHNCSVQICAgIfAhkiAAAADlJREFUOI1jYBhsgJGBgYGBgYHhPwMDA8P/////MzAwMDAQC5gYSABDwwBGIgxgZGAgCwxtA0YBAACbvxAJkHDJOAAAAABJRU5ErkJggg=="
  );
  tray = new Tray(icon);
  const contextMenu = Menu.buildFromTemplate([
    { label: "Open DAN", click: () => mainWindow?.show() || createWindow() },
    { type: "separator" },
    { label: "Quit", click: () => app.quit() },
  ]);
  tray.setToolTip("DAN");
  tray.setContextMenu(contextMenu);
  tray.on("click", () => mainWindow?.show() || createWindow());
}

// --- IPC Handlers: file operations for the renderer ---

ipcMain.handle("dialog:openFile", async (_event, options: { filters?: Array<{ name: string; extensions: string[] }>; multiple?: boolean }) => {
  if (!mainWindow) return null;
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: options.multiple ? ["openFile", "multiSelections"] : ["openFile"],
    filters: options.filters,
  });
  if (result.canceled) return null;
  return result.filePaths;
});

ipcMain.handle("dialog:openDirectory", async () => {
  if (!mainWindow) return null;
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ["openDirectory"],
  });
  if (result.canceled) return null;
  return result.filePaths[0];
});

ipcMain.handle("dialog:saveFile", async (_event, options: { defaultPath?: string; filters?: Array<{ name: string; extensions: string[] }> }) => {
  if (!mainWindow) return null;
  const result = await dialog.showSaveDialog(mainWindow, {
    defaultPath: options.defaultPath,
    filters: options.filters,
  });
  if (result.canceled) return null;
  return result.filePath;
});

ipcMain.handle("fs:readFile", async (_event, filePath: string) => {
  return fs.readFileSync(filePath, "utf-8");
});

ipcMain.handle("fs:writeFile", async (_event, filePath: string, content: string) => {
  fs.writeFileSync(filePath, content, "utf-8");
});

ipcMain.handle("fs:readDir", async (_event, dirPath: string) => {
  const entries = fs.readdirSync(dirPath, { withFileTypes: true });
  return entries.map((e) => ({ name: e.name, isDirectory: e.isDirectory() }));
});

ipcMain.handle("fs:stat", async (_event, filePath: string) => {
  const stat = fs.statSync(filePath);
  return { size: stat.size, mtime: stat.mtimeMs, isDirectory: stat.isDirectory() };
});

// --- App lifecycle ---

app.whenReady().then(() => {
  createWindow();
  createTray();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
