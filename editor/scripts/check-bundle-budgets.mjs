import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const assetsDir = join(process.cwd(), "dist", "assets");

const budgets = [
  {
    label: "monaco chunk",
    pattern: /^monaco-.*\.js$/,
    maxBytes: 4_600_000,
  },
  {
    label: "pdf chunk",
    pattern: /^pdf-.*\.js$/,
    maxBytes: 550_000,
  },
  {
    label: "katex chunk",
    pattern: /^katex-.*\.js$/,
    maxBytes: 350_000,
  },
  {
    label: "ResearchMode shell chunk",
    pattern: /^ResearchMode-.*\.js$/,
    maxBytes: 60_000,
  },
  {
    label: "ResearchFurnacePanel chunk",
    pattern: /^ResearchFurnacePanel-.*\.js$/,
    maxBytes: 55_000,
  },
  {
    label: "CodeMode shell chunk",
    pattern: /^CodeMode-.*\.js$/,
    maxBytes: 240_000,
  },
];

function formatKb(bytes) {
  return `${(bytes / 1024).toFixed(1)} kB`;
}

const assetFiles = readdirSync(assetsDir);
const failures = [];
const warnings = [];

console.log("Bundle budget report");

for (const budget of budgets) {
  const file = assetFiles.find((candidate) => budget.pattern.test(candidate));
  if (!file) {
    warnings.push(`Missing expected chunk for ${budget.label}`);
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

if (warnings.length > 0) {
  console.warn("\nBundle budget warnings:");
  for (const warning of warnings) {
    console.warn(`- ${warning}`);
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
