import { describe, expect, it } from "vitest";

import type { ChatMessage } from "../../types/chat";
import type { ChatThreadSummary } from "../api";
import {
  buildBranchedThreadTitle,
  buildThreadBranchTree,
  getEditBranchTarget,
  getExploreBranchTarget,
  getRegenerateBranchTarget,
  isSyntheticAttachmentSummary,
} from "../chatBranching";

function makeMessage(
  id: string,
  role: ChatMessage["role"],
  content: string,
  extra: Partial<ChatMessage> = {},
): ChatMessage {
  return {
    id,
    role,
    content,
    timestamp: 1,
    ...extra,
  };
}

function makeThread(
  id: string,
  extra: Partial<ChatThreadSummary> = {},
): ChatThreadSummary {
  return {
    id,
    title: `Thread ${id}`,
    workflow_id: "wf-1",
    message_count: 2,
    created_at: "2026-03-17T10:00:00.000Z",
    updated_at: "2026-03-17T10:00:00.000Z",
    ...extra,
  };
}

describe("chatBranching helpers", () => {
  it("detects synthetic attachment-only summaries", () => {
    expect(
      isSyntheticAttachmentSummary("Attached paper.pdf", [
        { filename: "paper.pdf", path: "/tmp/paper.pdf" },
      ]),
    ).toBe(true);
    expect(
      isSyntheticAttachmentSummary("Please summarize this", [
        { filename: "paper.pdf", path: "/tmp/paper.pdf" },
      ]),
    ).toBe(false);
  });

  it("builds an edit branch target from a past user turn", () => {
    const messages = [
      makeMessage("u1", "user", "First prompt"),
      makeMessage("a1", "assistant", "First reply"),
      makeMessage("u2", "user", "Attached paper.pdf", {
        attachments: [{ filename: "paper.pdf", path: "/tmp/paper.pdf" }],
      }),
      makeMessage("a2", "assistant", "Second reply"),
    ];

    const target = getEditBranchTarget(messages, "u2");

    expect(target).toEqual({
      historyBefore: messages.slice(0, 2),
      content: "",
      attachments: [
        {
          id: expect.any(String),
          kind: "file",
          name: "paper.pdf",
          path: "/tmp/paper.pdf",
          size: undefined,
          mimeType: undefined,
          caption: undefined,
          source: undefined,
        },
      ],
    });
  });

  it("builds a regenerate branch target from the preceding user turn", () => {
    const messages = [
      makeMessage("u1", "user", "First prompt"),
      makeMessage("a1", "assistant", "First reply"),
      makeMessage("u2", "user", "Rewrite this section"),
      makeMessage("a2", "assistant", "Here is the rewrite", {
        toolCalls: [
          {
            id: "tool-1",
            toolName: "file_write",
            argsPreview: "{}",
            status: "success",
          },
        ],
      }),
    ];

    const target = getRegenerateBranchTarget(messages, "a2");

    expect(target).toEqual({
      historyBefore: messages.slice(0, 2),
      content: "Rewrite this section",
      attachments: [],
    });
  });

  it("builds an explore branch target including the assistant message", () => {
    const messages = [
      makeMessage("u1", "user", "First prompt"),
      makeMessage("a1", "assistant", "First reply"),
      makeMessage("u2", "user", "Follow up"),
      makeMessage("a2", "assistant", "Second reply"),
    ];

    const target = getExploreBranchTarget(messages, "a1");
    expect(target).toEqual({
      historyUpToHere: [messages[0], messages[1]],
    });

    expect(getExploreBranchTarget(messages, "u1")).toBeNull();
    expect(getExploreBranchTarget(messages, "missing")).toBeNull();
  });

  it("builds readable branch titles", () => {
    expect(
      buildBranchedThreadTitle("Energy outlook", "Please compare oil and gas"),
    ).toBe("compare oil and gas (branch)");
    expect(buildBranchedThreadTitle("Energy outlook", "")).toBe(
      "Energy outlook (branch)",
    );
  });

  it("builds branch ancestry and sibling groups from thread summaries", () => {
    const root = makeThread("root", {
      created_at: "2026-03-17T09:00:00.000Z",
      updated_at: "2026-03-17T09:00:00.000Z",
    });
    const siblingA = makeThread("branch-a", {
      parent_thread_id: "root",
      branch_point_message_id: "assistant-1",
      branch_type: "explore",
      created_at: "2026-03-17T10:00:00.000Z",
      updated_at: "2026-03-17T10:01:00.000Z",
    });
    const siblingB = makeThread("branch-b", {
      parent_thread_id: "root",
      branch_point_message_id: "assistant-1",
      branch_type: "explore",
      created_at: "2026-03-17T10:05:00.000Z",
      updated_at: "2026-03-17T10:06:00.000Z",
    });
    const regenerateBranch = makeThread("branch-c", {
      parent_thread_id: "root",
      branch_point_message_id: "assistant-2",
      branch_type: "regenerate",
      created_at: "2026-03-17T10:10:00.000Z",
      updated_at: "2026-03-17T10:11:00.000Z",
    });

    const tree = buildThreadBranchTree([
      regenerateBranch,
      siblingB,
      root,
      siblingA,
    ]);

    expect(tree.rootIds).toEqual(["root"]);
    expect(tree.childrenByParentId.root).toEqual([
      "branch-a",
      "branch-b",
      "branch-c",
    ]);
    expect(tree.ancestryByThreadId["branch-b"]).toEqual(["root"]);
    expect(tree.siblingInfoByThreadId["branch-a"]).toMatchObject({
      count: 2,
      position: 1,
      threadIds: ["branch-a", "branch-b"],
    });
    expect(tree.siblingInfoByThreadId["branch-c"]).toMatchObject({
      count: 1,
      position: 1,
      threadIds: ["branch-c"],
    });
  });

  it("treats orphaned or legacy threads as roots", () => {
    const recentLegacy = makeThread("legacy", {
      updated_at: "2026-03-17T12:00:00.000Z",
    });
    const orphan = makeThread("orphan", {
      parent_thread_id: "missing-parent",
      branch_point_message_id: "assistant-9",
      branch_type: "edit",
      updated_at: "2026-03-17T11:00:00.000Z",
    });

    const tree = buildThreadBranchTree([orphan, recentLegacy]);

    expect(tree.rootIds).toEqual(["legacy", "orphan"]);
    expect(tree.ancestryByThreadId.legacy).toEqual([]);
    expect(tree.ancestryByThreadId.orphan).toEqual([]);
  });

  it("groups legacy siblings even when branch point ids are blank", () => {
    const root = makeThread("root");
    const first = makeThread("child-1", {
      parent_thread_id: "root",
      branch_point_message_id: "",
      created_at: "2026-03-17T10:00:00.000Z",
      updated_at: "2026-03-17T10:00:00.000Z",
    });
    const second = makeThread("child-2", {
      parent_thread_id: "root",
      branch_point_message_id: null,
      created_at: "2026-03-17T10:02:00.000Z",
      updated_at: "2026-03-17T10:02:00.000Z",
    });

    const tree = buildThreadBranchTree([root, second, first]);

    expect(tree.siblingInfoByThreadId["child-1"]).toMatchObject({
      count: 2,
      position: 1,
      threadIds: ["child-1", "child-2"],
    });
    expect(tree.siblingInfoByThreadId["child-2"]).toMatchObject({
      count: 2,
      position: 2,
      threadIds: ["child-1", "child-2"],
    });
  });

  it("separates sibling counts by branch type for the same branch point", () => {
    const root = makeThread("root");
    const exploreA = makeThread("explore-a", {
      parent_thread_id: "root",
      branch_point_message_id: "assistant-1",
      branch_type: "explore",
      created_at: "2026-03-17T10:00:00.000Z",
    });
    const exploreB = makeThread("explore-b", {
      parent_thread_id: "root",
      branch_point_message_id: "assistant-1",
      branch_type: "explore",
      created_at: "2026-03-17T10:01:00.000Z",
    });
    const regenerate = makeThread("regen-1", {
      parent_thread_id: "root",
      branch_point_message_id: "assistant-1",
      branch_type: "regenerate",
      created_at: "2026-03-17T10:02:00.000Z",
    });

    const tree = buildThreadBranchTree([root, regenerate, exploreB, exploreA]);

    expect(tree.siblingInfoByThreadId["explore-a"]).toMatchObject({
      count: 2,
      position: 1,
      threadIds: ["explore-a", "explore-b"],
    });
    expect(tree.siblingInfoByThreadId["explore-b"]).toMatchObject({
      count: 2,
      position: 2,
      threadIds: ["explore-a", "explore-b"],
    });
    expect(tree.siblingInfoByThreadId["regen-1"]).toMatchObject({
      count: 1,
      position: 1,
      threadIds: ["regen-1"],
    });
  });
});
