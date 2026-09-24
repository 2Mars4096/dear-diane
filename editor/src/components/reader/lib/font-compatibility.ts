const key = (path: string) => `dan.reader.font-compatibility.v1:${path}`;

export function readFontCompatibility(path: string): boolean {
  try { return localStorage.getItem(key(path)) === "true"; } catch { return false; }
}

export function rememberFontCompatibility(path: string, enabled: boolean) {
  try {
    if (enabled) localStorage.setItem(key(path), "true");
    else localStorage.removeItem(key(path));
  } catch { /* Still usable for the current reading session. */ }
}
