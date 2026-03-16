/**
 * AI-powered code actions: explain, generate tests, docs, refactoring,
 * error fixes, commit messages, and codebase Q&A.
 */

import { requestEditorChatText } from "./editorChat";

async function aiRequest(message: string, action: string): Promise<string> {
  return requestEditorChatText({
    message,
    mode: "ask",
    scope: `code-action:${action}`,
    surfaceContext: { action },
  });
}

export async function explainCode(
  code: string,
  language: string,
): Promise<string> {
  return aiRequest(
    `Explain the following ${language} code concisely:\n\n\`\`\`${language}\n${code}\n\`\`\``,
    "explain",
  );
}

export async function generateTests(
  code: string,
  language: string,
  filePath: string,
): Promise<string> {
  return aiRequest(
    `Generate comprehensive tests for this ${language} code from ${filePath}:\n\n\`\`\`${language}\n${code}\n\`\`\`\n\nUse the standard testing framework for ${language}. Return ONLY the test code.`,
    "generate-tests",
  );
}

export async function generateDocs(
  code: string,
  language: string,
): Promise<string> {
  const docStyle =
    language === "python"
      ? "docstrings"
      : language === "typescript" || language === "javascript"
        ? "JSDoc"
        : "comments";
  return aiRequest(
    `Generate documentation (${docStyle}) for this ${language} code. Return ONLY the documented version of the code:\n\n\`\`\`${language}\n${code}\n\`\`\``,
    "generate-docs",
  );
}

export async function suggestRefactoring(
  code: string,
  language: string,
): Promise<Array<{ description: string; refactored: string }>> {
  const raw = await aiRequest(
    `Suggest refactoring for this ${language} code. For each suggestion, provide a brief description and the refactored code.\n\n\`\`\`${language}\n${code}\n\`\`\`\n\nRespond in JSON array format: [{"description": "...", "refactored": "..."}]`,
    "refactor",
  );
  try {
    const jsonMatch = raw.match(/\[[\s\S]*\]/);
    return jsonMatch ? JSON.parse(jsonMatch[0]) : [];
  } catch {
    return [];
  }
}

export async function suggestErrorFix(
  error: string,
  code: string,
  language: string,
): Promise<string> {
  return aiRequest(
    `Fix this error in ${language} code:\n\nError: ${error}\n\nCode:\n\`\`\`${language}\n${code}\n\`\`\`\n\nReturn ONLY the fixed code.`,
    "fix-error",
  );
}

export async function generateCommitMessage(diff: string): Promise<string> {
  const result = await aiRequest(
    `Generate a concise, conventional commit message for this diff:\n\n${diff}\n\nRespond with ONLY the commit message (no quotes, no explanation).`,
    "commit-message",
  );
  return result.trim().replace(/^["']|["']$/g, "");
}

export async function askCodebase(
  question: string,
  openFiles: Array<{ path: string; content: string }>,
): Promise<string> {
  const fileContext = openFiles
    .slice(0, 5)
    .map((f) => `--- ${f.path} ---\n${f.content.slice(0, 2000)}`)
    .join("\n\n");
  return aiRequest(
    `${question}\n\nRelevant files:\n${fileContext}`,
    "codebase-qa",
  );
}

/** Derive test file path from source path */
export function deriveTestPath(filePath: string): string {
  const parts = filePath.split("/");
  const filename = parts.pop() ?? "file.ts";
  const dotIdx = filename.lastIndexOf(".");
  if (dotIdx === -1) return [...parts, `${filename}.test.ts`].join("/");
  const name = filename.slice(0, dotIdx);
  const ext = filename.slice(dotIdx);
  return [...parts, `${name}.test${ext}`].join("/");
}

/** Strip markdown code fences from AI response */
export function stripFences(text: string): string {
  let s = text.trim();
  const fenceStart = /^```[\w]*\n?/;
  const fenceEnd = /\n?```\s*$/;
  if (fenceStart.test(s) && fenceEnd.test(s)) {
    s = s.replace(fenceStart, "").replace(fenceEnd, "");
  }
  return s;
}
