const LARGE_FILE_THRESHOLD = 1_000_000; // 1 MB
const VERY_LARGE_THRESHOLD = 5_000_000; // 5 MB

export interface LargeFileConfig {
  disableMinimap: boolean;
  disableTokenization: boolean;
  disableBracketPairColorization: boolean;
  disableWordWrap: boolean;
  disableFolding: boolean;
  disableGitBlame: boolean;
  readOnly: boolean;
  warningMessage: string | null;
}

export function getLargeFileConfig(
  contentLength: number,
): LargeFileConfig | null {
  if (contentLength < LARGE_FILE_THRESHOLD) return null;

  if (contentLength >= VERY_LARGE_THRESHOLD) {
    return {
      disableMinimap: true,
      disableTokenization: true,
      disableBracketPairColorization: true,
      disableWordWrap: true,
      disableFolding: true,
      disableGitBlame: true,
      readOnly: false,
      warningMessage: `Large file (${(contentLength / 1_000_000).toFixed(1)}MB). Some features disabled for performance.`,
    };
  }

  return {
    disableMinimap: true,
    disableTokenization: false,
    disableBracketPairColorization: true,
    disableWordWrap: true,
    disableFolding: false,
    disableGitBlame: true,
    readOnly: false,
    warningMessage: `Large file (${(contentLength / 1_000_000).toFixed(1)}MB). Minimap and some features disabled.`,
  };
}
