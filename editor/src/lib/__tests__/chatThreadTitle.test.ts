import { describe, expect, it } from "vitest";

import {
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
});
