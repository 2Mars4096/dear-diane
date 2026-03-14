export interface ProblemMatch {
  file: string;
  line: number;
  column?: number;
  severity: "error" | "warning" | "info";
  message: string;
  source: string;
}

interface MatcherDef {
  pattern: RegExp;
  groups: { file?: number; line: number; col?: number; severity?: number; message: number };
}

const MATCHERS: MatcherDef[] = [
  // TypeScript/JavaScript: src/foo.ts(12,5): error TS2322: ...
  {
    pattern: /^(.+)\((\d+),(\d+)\):\s*(error|warning)\s+TS\d+:\s*(.+)$/,
    groups: { file: 1, line: 2, col: 3, severity: 4, message: 5 },
  },
  // ESLint:   12:5  error  Message  rule-name
  {
    pattern: /^\s+(\d+):(\d+)\s+(error|warning)\s+(.+?)\s{2,}\S+$/,
    groups: { line: 1, col: 2, severity: 3, message: 4 },
  },
  // Python traceback: File "foo.py", line 12
  {
    pattern: /^\s+File "(.+)", line (\d+)/,
    groups: { file: 1, line: 2, message: 2 },
  },
  // Rust: error[E0308]: mismatched types\n  --> src/main.rs:12:5
  {
    pattern: /^error\[E\d+\]:\s*(.+)/,
    groups: { line: 0, message: 1 },
  },
  // GCC/Clang: file.c:12:5: error: message
  {
    pattern: /^(.+):(\d+):(\d+):\s*(error|warning|note|info):\s*(.+)$/,
    groups: { file: 1, line: 2, col: 3, severity: 4, message: 5 },
  },
  // Go: file.go:12:5: message
  {
    pattern: /^(.+\.go):(\d+):(\d+):\s*(.+)$/,
    groups: { file: 1, line: 2, col: 3, message: 4 },
  },
  // Generic file:line:col: message
  {
    pattern: /^(.+):(\d+):(\d+):\s*(.+)$/,
    groups: { file: 1, line: 2, col: 3, message: 4 },
  },
  // Generic file:line: message (no column)
  {
    pattern: /^(.+):(\d+):\s*(.+)$/,
    groups: { file: 1, line: 2, message: 3 },
  },
];

// Rust multi-line pattern: captures the file info line after error header
const RUST_LOCATION = /^\s+--> (.+):(\d+):(\d+)/;

function normalizeSeverity(raw?: string): "error" | "warning" | "info" {
  if (!raw) return "error";
  const lower = raw.toLowerCase();
  if (lower === "warning" || lower === "warn") return "warning";
  if (lower === "info" || lower === "note") return "info";
  return "error";
}

export function parseTaskOutput(
  output: string,
  taskSource: string,
): ProblemMatch[] {
  const problems: ProblemMatch[] = [];
  const lines = output.split("\n");

  let pendingRustMsg: string | null = null;

  for (const line of lines) {
    // Handle Rust multi-line: first line is "error[E...]: msg", next is " --> file:line:col"
    if (pendingRustMsg !== null) {
      const rustLoc = RUST_LOCATION.exec(line);
      if (rustLoc) {
        problems.push({
          file: rustLoc[1],
          line: parseInt(rustLoc[2], 10),
          column: parseInt(rustLoc[3], 10),
          severity: "error",
          message: pendingRustMsg,
          source: taskSource,
        });
        pendingRustMsg = null;
        continue;
      }
      pendingRustMsg = null;
    }

    // Check rust error header
    const rustHeader = /^error\[E\d+\]:\s*(.+)/.exec(line);
    if (rustHeader) {
      pendingRustMsg = rustHeader[1];
      continue;
    }

    for (const matcher of MATCHERS) {
      // Skip the standalone rust pattern since we handle it above
      if (matcher === MATCHERS[3]) continue;

      const match = matcher.pattern.exec(line);
      if (match) {
        const file = matcher.groups.file
          ? match[matcher.groups.file] ?? ""
          : "";
        const lineNum = parseInt(match[matcher.groups.line] ?? "1", 10);

        // Skip if file looks like a protocol or has no path-like structure
        if (file && !file.includes("/") && !file.includes("\\") && !file.includes(".")) {
          continue;
        }

        problems.push({
          file,
          line: lineNum,
          column: matcher.groups.col
            ? parseInt(match[matcher.groups.col] ?? "1", 10)
            : undefined,
          severity: normalizeSeverity(
            matcher.groups.severity
              ? match[matcher.groups.severity]
              : undefined,
          ),
          message: match[matcher.groups.message] ?? line.trim(),
          source: taskSource,
        });
        break;
      }
    }
  }

  return problems;
}
