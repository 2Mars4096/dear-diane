import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const assetsDir = join(process.cwd(), "dist", "assets");

const budgets = [
  {
    label: "workspace shell chunk",
    pattern: /^ChunkWorkspaceApp-.*\.js$/,
    maxBytes: 450_000,
  },
  {
    label: "entry chunk",
    pattern: /^index-.*\.js$/,
    maxBytes: 60_000,
  },
  {
    label: "katex chunk",
    pattern: /^katex-.*\.js$/,
    maxBytes: 350_000,
  },
  {
    label: "content rendering chunk",
    pattern: /^content-.*\.js$/,
    maxBytes: 190_000,
  },
  {
    label: "React/vendor chunk",
    pattern: /^vendor-.*\.js$/,
    maxBytes: 250_000,
  },
];

const archivedSurfacePatterns = [
  {
    label: "classic application shell",
    pattern: /^AppShell-.*\.js$/,
  },
  {
    label: "legacy V2 chat shell",
    pattern: /^ChatV2App-.*\.js$/,
  },
  {
    label: "Operations/network graph",
    pattern: /^(OperationsMode|graph)-.*\.js$/,
  },
  {
    label: "Code mode",
    pattern: /^(CodeMode|monaco|terminal)-.*\.js$/,
  },
  {
    label: "Research mode",
    pattern: /^(ResearchMode|ResearchFurnacePanel|pdf)-.*\.js$/,
  },
];

function formatKb(bytes) {
  return `${(bytes / 1024).toFixed(1)} kB`;
}

const assetFiles = readdirSync(assetsDir);
const failures = [];

console.log("Bundle budget report");

for (const budget of budgets) {
  const file = assetFiles.find((candidate) => budget.pattern.test(candidate));
  if (!file) {
    failures.push(`Missing required active chunk for ${budget.label}`);
    continue;
  }

  const size = statSync(join(assetsDir, file)).size;
  const status = size <= budget.maxBytes ? "ok" : "over";
  console.log(
    `- ${budget.label}: ${file} ${formatKb(size)} / budget ${formatKb(budget.maxBytes)} (${status})`,
  );

  if (size > budget.maxBytes) {
    failures.push(
      `${budget.label} exceeded budget: ${formatKb(size)} > ${formatKb(budget.maxBytes)}`,
    );
  }
}

console.log("\nArchived surface exclusion report");
for (const archivedSurface of archivedSurfacePatterns) {
  const matches = assetFiles.filter((candidate) =>
    archivedSurface.pattern.test(candidate),
  );
  if (matches.length > 0) {
    failures.push(
      `${archivedSurface.label} leaked into the active bundle: ${matches.join(", ")}`,
    );
    console.log(`- ${archivedSurface.label}: present (${matches.join(", ")})`);
  } else {
    console.log(`- ${archivedSurface.label}: absent (ok)`);
  }
}

if (failures.length > 0) {
  console.error("\nBundle budget failures:");
  for (const failure of failures) {
    console.error(`- ${failure}`);
  }
  process.exit(1);
}

console.log("\nAll bundle budgets passed.");
