import { nativeExtension } from "../electronBridge";

export interface IconThemeDefinition {
  iconDefinitions: Record<string, { iconPath: string }>;
  file?: string;
  folder?: string;
  folderExpanded?: string;
  fileExtensions?: Record<string, string>;
  fileNames?: Record<string, string>;
  folderNames?: Record<string, string>;
  folderNamesExpanded?: Record<string, string>;
  languageIds?: Record<string, string>;
}

export interface ResolvedIcon {
  src: string;
  alt: string;
}

class IconThemeManager {
  private theme: IconThemeDefinition | null = null;
  private iconCache = new Map<string, string>();
  private extensionPath = "";

  async loadTheme(extensionPath: string, themePath: string): Promise<void> {
    const content = await nativeExtension.readFile(`${extensionPath}/${themePath}`);
    if (!content) return;

    try {
      this.theme = JSON.parse(content);
    } catch {
      console.warn(`Failed to parse icon theme at ${extensionPath}/${themePath}`);
      return;
    }
    this.extensionPath = extensionPath;
    this.iconCache.clear();
  }

  getFileIcon(fileName: string, languageId?: string): ResolvedIcon | null {
    if (!this.theme) return null;

    const fileNameIcon = this.theme.fileNames?.[fileName];
    if (fileNameIcon) return this.resolveIcon(fileNameIcon, fileName);

    const ext = fileName.split(".").pop()?.toLowerCase() ?? "";
    const extIcon = this.theme.fileExtensions?.[ext];
    if (extIcon) return this.resolveIcon(extIcon, fileName);

    const parts = fileName.split(".");
    for (let i = 1; i < parts.length; i++) {
      const compoundExt = parts.slice(i).join(".");
      const compoundIcon = this.theme.fileExtensions?.[compoundExt];
      if (compoundIcon) return this.resolveIcon(compoundIcon, fileName);
    }

    if (languageId && this.theme.languageIds?.[languageId]) {
      return this.resolveIcon(this.theme.languageIds[languageId], fileName);
    }

    if (this.theme.file) return this.resolveIcon(this.theme.file, fileName);

    return null;
  }

  getFolderIcon(folderName: string, expanded: boolean): ResolvedIcon | null {
    if (!this.theme) return null;

    if (expanded) {
      const expandedIcon = this.theme.folderNamesExpanded?.[folderName];
      if (expandedIcon) return this.resolveIcon(expandedIcon, folderName);
      if (this.theme.folderExpanded) return this.resolveIcon(this.theme.folderExpanded, folderName);
    }

    const folderIcon = this.theme.folderNames?.[folderName];
    if (folderIcon) return this.resolveIcon(folderIcon, folderName);

    if (this.theme.folder) return this.resolveIcon(this.theme.folder, folderName);

    return null;
  }

  private resolveIcon(defId: string, name: string): ResolvedIcon | null {
    const def = this.theme?.iconDefinitions?.[defId];
    if (!def?.iconPath) return null;

    if (this.iconCache.has(defId)) {
      return { src: this.iconCache.get(defId)!, alt: name };
    }

    const iconPath = `${this.extensionPath}/${def.iconPath}`;
    this.iconCache.set(defId, iconPath);
    return { src: iconPath, alt: name };
  }

  get hasTheme(): boolean {
    return this.theme !== null;
  }

  reset() {
    this.theme = null;
    this.iconCache.clear();
    this.extensionPath = "";
  }
}

export const iconThemeManager = new IconThemeManager();
