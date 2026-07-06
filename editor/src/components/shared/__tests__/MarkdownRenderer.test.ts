import { describe, expect, it } from "vitest";
import { renderMarkdownToHtml } from "../MarkdownRenderer";

function autoCodeCount(html: string) {
  return html.match(/dan-markdown-auto-code/g)?.length ?? 0;
}

describe("MarkdownRenderer", () => {
  it("auto-highlights code-like files, objects, fields, and calls in prose", () => {
    const html = renderMarkdownToHtml(
      "GameLoop.gd calls AgentNeighborSystem from _ready() and reads _ecs.positions_x before query_2d.",
    );

    expect(html).toContain("dan-markdown-auto-code");
    expect(html).toContain(">GameLoop.gd</code>");
    expect(html).toContain(">AgentNeighborSystem</code>");
    expect(html).toContain(">_ready()</code>");
    expect(html).toContain(">_ecs.positions_x</code>");
    expect(html).not.toContain(">query_2d</code>");
  });

  it("leaves explicit code spans and math placeholders on their own paths", () => {
    const html = renderMarkdownToHtml(
      "Use `GameLoop.gd`, then inspect GameLoop.gd with cost $O(k)$.",
    );

    expect(autoCodeCount(html)).toBe(1);
    expect(html).toContain("dan-markdown-inline-file");
    expect(html).toContain(">GameLoop.gd</code>");
    expect(html).toContain("katex");
    expect(html).not.toContain("DAN_MD_");
  });

  it("can render math-like code spans as inline math on math notes", () => {
    const html = renderMarkdownToHtml(
      "Prime if `ab in P` implies `a in P`. Always `IJ subset I cap J`, and `R/P` is a quotient.",
      { renderMathCodeSpans: true },
    );

    expect(html).toContain("dan-markdown-inline-math-code");
    expect(html).toContain("katex");
    expect(html).not.toContain(">ab in P</code>");
    expect(html).not.toContain(">IJ subset I cap J</code>");
  });

  it("keeps source ids and files as code even when math-code rendering is enabled", () => {
    const html = renderMarkdownToHtml(
      "Use `dm-aa-small-01-structure-and-proof-language`, `decision-math/abstract-algebra-structures-and-proofs`, and `website/styles.css`.",
      { renderMathCodeSpans: true },
    );

    expect(html).toContain(">dm-aa-small-01-structure-and-proof-language</code>");
    expect(html).toContain(">decision-math/abstract-algebra-structures-and-proofs</code>");
    expect(html).toContain("dan-markdown-inline-file");
    expect(html).not.toContain("dan-markdown-inline-math-code");
  });

  it("classifies inline highlights by role", () => {
    const html = renderMarkdownToHtml(
      "Open `website/styles.css`, set `--font-display`, and use `css body { font-family: var(--font-body); }`.",
    );

    expect(html).toContain("dan-markdown-inline-file");
    expect(html).toContain("dan-markdown-inline-symbol");
    expect(html).toContain("dan-markdown-inline-snippet");
  });

  it("can turn off automatic code-like prose highlighting", () => {
    const html = renderMarkdownToHtml(
      "GameLoop.gd calls AgentNeighborSystem, while `explicit.ts` remains explicit.",
      { autoHighlightCode: false },
    );

    expect(autoCodeCount(html)).toBe(0);
    expect(html).toContain("GameLoop.gd calls AgentNeighborSystem");
    expect(html).toContain(">explicit.ts</code>");
  });

  it("does not highlight ordinary prose headings", () => {
    const html = renderMarkdownToHtml("What Changed and Checks are normal words.");

    expect(autoCodeCount(html)).toBe(0);
  });

  it("does not auto-highlight prose abbreviations", () => {
    const html = renderMarkdownToHtml(
      "Approve a scaffold/demo run, e.g., generate a README. Use i.e. for clarification at 3 p.m.",
    );

    expect(autoCodeCount(html)).toBe(0);
    expect(html).toContain("e.g.");
    expect(html).toContain("i.e.");
    expect(html).toContain("p.m.");
  });

  it("keeps bold markers working inside lists with auto-highlighted code", () => {
    const html = renderMarkdownToHtml(
      "- **Browser prototype** (`src/`, `index.html`) — TypeScript/JS ECS.\n" +
        "1. **M1.5** — Wire `AgentNeighborSystem.gd` into `GameLoop.gd`.",
    );

    expect(html).toContain("<strong");
    expect(html).toContain(">Browser prototype</strong>");
    expect(html).toContain(">M1.5</strong>");
    expect(html).not.toContain("**Browser prototype**");
    expect(html).not.toContain("**M1.5**");
  });

  it("does not over-highlight dense unbackticked code fragments", () => {
    const html = renderMarkdownToHtml(
      "Fix _process_agents(delta: float) -> void: var alive := ecs.alive_slots() # Array[int]. " +
        "Then route process_agent_batch_counter through range(start, end) and mm.set_instance_transform(ecs.mm_index[slot], t).",
    );

    expect(html).not.toContain(">_process_agents(delta: float)</code>");
    expect(html).not.toContain(">process_agent_batch_counter</code>");
    expect(html).not.toContain(">range(start, end)</code>");
    expect(html).not.toContain(">mm.set_instance_transform</code>(ecs");
    expect(html).not.toContain(">ecs.mm_index</code>[slot]");
    expect(autoCodeCount(html)).toBeLessThanOrEqual(1);
  });
});
