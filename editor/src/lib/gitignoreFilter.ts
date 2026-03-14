/**
 * Minimal .gitignore pattern matcher.
 * Supports: *.ext, dir/, exact name, simple wildcards, negation with !
 */
export function parseGitignore(content: string): (filePath: string) => boolean {
  const patterns = content
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#"));

  return (filePath: string) => {
    const name = filePath.split("/").pop() ?? filePath;
    let ignored = false;

    for (const pattern of patterns) {
      let p = pattern;
      const negate = p.startsWith("!");
      if (negate) p = p.slice(1);

      let matches = false;

      if (p.endsWith("/")) {
        const dir = p.slice(0, -1);
        if (
          filePath.includes("/" + dir + "/") ||
          filePath.endsWith("/" + dir) ||
          name === dir
        ) {
          matches = true;
        }
      } else if (p.startsWith("*.")) {
        if (name.endsWith(p.slice(1))) matches = true;
      } else if (p.includes("*") || p.includes("?")) {
        const regex = new RegExp(
          "^" + p.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".") + "$",
        );
        if (regex.test(name) || regex.test(filePath)) matches = true;
      } else if (p.includes("/")) {
        if (filePath.endsWith("/" + p) || filePath === p) matches = true;
      } else {
        if (name === p) matches = true;
      }

      if (matches) {
        ignored = !negate;
      }
    }

    return ignored;
  };
}
