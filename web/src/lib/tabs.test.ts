import { describe, expect, it } from "vitest";
import { availableTabs, nextTab, parseTab, shownTab, urlWithTab } from "./tabs";

describe("parseTab", () => {
  it("reads known tabs and falls back to the transcript", () => {
    expect(parseTab("diff")).toBe("diff");
    expect(parseTab("before-after")).toBe("before-after");
    expect(parseTab(["checks", "diff"])).toBe("checks");
    expect(parseTab("nope")).toBe("transcript");
    expect(parseTab(undefined)).toBe("transcript");
    expect(parseTab(null)).toBe("transcript");
  });
});

describe("availableTabs and shownTab", () => {
  it("offers Before / After only when the run has screenshots", () => {
    expect(availableTabs(false)).toEqual(["transcript", "diff", "checks"]);
    expect(availableTabs(true)).toEqual(["transcript", "diff", "before-after", "checks"]);
  });

  it("shows the transcript while the chosen tab doesn't apply to this run", () => {
    expect(shownTab("before-after", false)).toBe("transcript");
    expect(shownTab("before-after", true)).toBe("before-after");
    expect(shownTab("checks", false)).toBe("checks");
  });
});

describe("urlWithTab", () => {
  it("sets the tab and keeps everything else", () => {
    expect(urlWithTab("http://x.test/tickets/1?a=b#top", "diff")).toBe("/tickets/1?a=b&tab=diff#top");
    expect(urlWithTab("http://x.test/tickets/1?tab=diff", "checks")).toBe("/tickets/1?tab=checks");
  });

  it("drops the parameter for the default tab", () => {
    expect(urlWithTab("http://x.test/tickets/1?tab=diff", "transcript")).toBe("/tickets/1");
  });
});

describe("nextTab", () => {
  const tabs = availableTabs(false);

  it("moves with the arrow keys, wrapping, and jumps with Home and End", () => {
    expect(nextTab(tabs, "transcript", "ArrowRight")).toBe("diff");
    expect(nextTab(tabs, "checks", "ArrowRight")).toBe("transcript");
    expect(nextTab(tabs, "transcript", "ArrowLeft")).toBe("checks");
    expect(nextTab(tabs, "diff", "Home")).toBe("transcript");
    expect(nextTab(tabs, "diff", "End")).toBe("checks");
    expect(nextTab(tabs, "diff", "Enter")).toBeNull();
  });
});
