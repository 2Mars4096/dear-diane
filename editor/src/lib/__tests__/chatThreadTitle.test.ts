import { describe, expect, it } from "vitest";

import {
  deriveDraftThreadTitleFromMessage,
  getDisplayThreadTitle,
  normalizeThreadTitleInput,
} from "../chatThreadTitle";

describe("chatThreadTitle helpers", () => {
  it("normalizes user-entered thread titles", () => {
    expect(normalizeThreadTitleInput("  Oil   price   outlook  ")).toBe(
      "Oil price outlook",
    );
  });

  it("returns empty string for blank user-entered titles", () => {
    expect(normalizeThreadTitleInput("   \n\t  ")).toBe("");
  });

  it("provides a fallback display title", () => {
    expect(getDisplayThreadTitle("", "Untitled chat")).toBe("Untitled chat");
    expect(getDisplayThreadTitle("Energy Outlook", "Untitled chat")).toBe(
      "Energy Outlook",
    );
  });

  it("derives cleaner draft titles from path-heavy requests", () => {
    expect(
      deriveDraftThreadTitleFromMessage(
        "can you /Users/lizhi/Dropbox/CUHK-phd/projects/supply-chain-report in this folder, Help me write a comprehensive auto supply chain report of 2026. in tex. you can download and cite figures by searching online.",
      ),
    ).toBe("comprehensive auto supply chain report of 2026");
  });
});
