import { describe, expect, it } from "vitest";
import {
  formatDiffPct,
  formatSize,
  generalScreenshotErrors,
  hasScreenshots,
  hasVisibleChange,
  isArtifactUrl,
  otherReviewErrors,
  screenshotGroups,
  stepWord,
  tailText,
  testReport,
  type RunReview,
} from "./artifacts";
import type { Artifact, ReviewError } from "./types";

let seq = 0;
function artifact(kind: string, label: string | null, meta: Artifact["meta"] = null): Artifact {
  seq += 1;
  const id = `a${seq}`;
  return { id, kind, label, url: `/api/artifacts/${id}`, meta };
}

const shots = (): Artifact[] => [
  artifact("test_report", "npm test", { exit_code: 1, passed: false, duration_s: 12.3, truncated: true }),
  artifact("screenshot_before", "/settings", { width: 1280, height: 800 }),
  artifact("screenshot_after", "/settings", { width: 1280, height: 800 }),
  artifact("screenshot_diff", "/settings", { width: 1280, height: 800, diff_pixels: 1234, diff_pct: 1.21 }),
  artifact("screenshot_before", "/", { width: 1280, height: 800 }),
  artifact("screenshot_after", "/", { width: 1280, height: 800 }),
  artifact("screenshot_diff", "/", { diff_pixels: 0, diff_pct: 0 }),
];

const error = (step: string, label: string | null, message = "went wrong"): ReviewError => ({
  step,
  label,
  message,
});

describe("screenshotGroups", () => {
  it("groups before, after and diff by route, in capture order", () => {
    const groups = screenshotGroups({ artifacts: shots() });
    expect(groups.map((g) => g.label)).toEqual(["/settings", "/"]);
    const settings = groups[0]!;
    expect(settings.before).toMatchObject({ width: 1280, height: 800 });
    expect(settings.after?.url).toMatch(/^\/api\/artifacts\//);
    expect(settings.diff).toMatchObject({ diffPixels: 1234, diffPct: 1.21 });
    expect(groups[1]!.diff).toMatchObject({ diffPixels: 0, diffPct: 0, width: null });
  });

  it("puts a route's manifest errors in its group, and gives a failed route its own", () => {
    const run: RunReview = {
      artifacts: shots(),
      review_errors: [
        error("screenshot_after", "/settings", "timed out"),
        error("screenshot_before", "/billing", "404"),
        error("app_after", null, "app did not answer on :3000 within 90 s"),
        error("setup", null, "npm ci exited 1"),
      ],
    };
    const groups = screenshotGroups(run);
    expect(groups.map((g) => g.label)).toEqual(["/settings", "/", "/billing"]);
    expect(groups[0]!.errors.map((e) => e.message)).toEqual(["timed out"]);
    expect(groups[2]).toMatchObject({ before: null, after: null, diff: null });
    expect(generalScreenshotErrors(run).map((e) => e.step)).toEqual(["app_after"]);
    expect(otherReviewErrors(run).map((e) => e.step)).toEqual(["setup"]);
  });

  it("files a rejected route artifact under its route, and a test error with the tests", () => {
    const run: RunReview = {
      artifacts: [],
      review_errors: [
        error("artifacts", "/settings", "not a PNG"),
        error("artifacts", "npm test", "too large"),
      ],
    };
    expect(screenshotGroups(run).map((g) => g.label)).toEqual(["/settings"]);
    expect(otherReviewErrors(run).map((e) => e.label)).toEqual(["npm test"]);
  });

  it("ignores artifacts that aren't served by nexTix and unknown kinds", () => {
    const run: RunReview = {
      artifacts: [
        { id: "x", kind: "screenshot_before", label: "/", url: "https://evil.example/x.png", meta: null },
        artifact("screenshot_thumb", "/"),
      ],
    };
    expect(screenshotGroups(run)).toEqual([]);
    expect(hasScreenshots(run)).toBe(false);
  });

  it("says whether the Before / After tab has anything to show", () => {
    expect(hasScreenshots(null)).toBe(false);
    expect(hasScreenshots({ artifacts: [artifact("test_report", "npm test")] })).toBe(false);
    expect(hasScreenshots({ artifacts: shots() })).toBe(true);
    // The app never started: no screenshots, but the tab explains why.
    expect(hasScreenshots({ review_errors: [error("app_before", null)] })).toBe(true);
    expect(hasScreenshots({ review_errors: [error("setup", null)] })).toBe(false);
  });
});

describe("isArtifactUrl", () => {
  it("accepts only same-origin artifact paths", () => {
    expect(isArtifactUrl("/api/artifacts/0b8f3c1e-5d2a-4e7b-9c61-2f4a8d9e0b13")).toBe(true);
    expect(isArtifactUrl("/api/artifacts/../tickets")).toBe(false);
    expect(isArtifactUrl("//evil.example/api/artifacts/x")).toBe(false);
    expect(isArtifactUrl("javascript:alert(1)")).toBe(false);
    expect(isArtifactUrl(null)).toBe(false);
  });
});

describe("formatDiffPct", () => {
  it("says how much changed, and says so plainly at zero", () => {
    expect(formatDiffPct(1.21, 1234)).toBe("1.21% of pixels changed");
    expect(formatDiffPct(1.2)).toBe("1.2% of pixels changed");
    expect(formatDiffPct(100)).toBe("100% of pixels changed");
    expect(formatDiffPct(0, 0)).toBe("No visible change");
    expect(formatDiffPct(0.3, 0)).toBe("No visible change");
    expect(formatDiffPct(0.004)).toBe("Less than 0.01% of pixels changed");
    expect(formatDiffPct(null, 5120)).toBe("5,120 pixels changed");
    expect(formatDiffPct(null, null)).toBe("Changes highlighted");
  });

  it("trusts the pixel count over a percentage the runner rounded down to zero", () => {
    // 5 pixels of 1280 × 800 is 0.0005%, which the runner stores as diff_pct 0.
    expect(formatDiffPct(0, 5)).toBe("5 pixels changed (under 0.01%)");
    expect(formatDiffPct(0, 1)).toBe("1 pixel changed (under 0.01%)");
    expect(formatDiffPct(0.004, 40)).toBe("40 pixels changed (under 0.01%)");
    expect(formatDiffPct(0, null)).toBe("No visible change");
    expect(hasVisibleChange({ diffPct: 0, diffPixels: 5 })).toBe(true);
    expect(hasVisibleChange({ diffPct: 0, diffPixels: 0 })).toBe(false);
    expect(hasVisibleChange({ diffPct: 0, diffPixels: null })).toBe(false);
    expect(hasVisibleChange({ diffPct: null, diffPixels: null })).toBe(true);
  });

  it("formats sizes when both sides are known", () => {
    expect(formatSize({ id: "a", url: "/api/artifacts/a", width: 1280, height: 800 })).toBe("1280 × 800");
    expect(formatSize({ id: "a", url: "/api/artifacts/a", width: null, height: 800 })).toBeNull();
    expect(formatSize(null)).toBeNull();
  });
});

describe("testReport", () => {
  it("finds the report and whether its output was cut", () => {
    expect(testReport({ artifacts: shots() })).toMatchObject({ truncated: true });
    expect(testReport({ artifacts: [] })).toBeNull();
    expect(testReport(null)).toBeNull();
  });
});

describe("tailText", () => {
  const log = Array.from({ length: 100 }, (_, i) => `line ${i + 1}`).join("\n") + "\n";

  it("keeps the last lines, where a failure is reported", () => {
    const tail = tailText(log, 10);
    expect(tail.text.split("\n")).toEqual(Array.from({ length: 10 }, (_, i) => `line ${91 + i}`));
    expect(tail).toMatchObject({ totalLines: 100, hiddenLines: 90 });
  });

  it("returns everything when it fits", () => {
    expect(tailText("ok\n", 10)).toEqual({ text: "ok", totalLines: 1, hiddenLines: 0 });
    expect(tailText("", 10)).toEqual({ text: "", totalLines: 0, hiddenLines: 0 });
  });

  it("caps characters too, starting on a whole line", () => {
    const tail = tailText(log, 50, 40);
    expect(tail.text.length).toBeLessThanOrEqual(40);
    expect(tail.text.startsWith("line ")).toBe(true);
    expect(tail.text.endsWith("line 100")).toBe(true);
    expect(tail.hiddenLines).toBe(100 - tail.text.split("\n").length);
  });

  it("clips one enormous line rather than flooding the page", () => {
    const tail = tailText("x".repeat(50_000), 60, 1000);
    expect(tail.text.length).toBe(1000);
    expect(tail.totalLines).toBe(1);
  });
});

describe("stepWord", () => {
  it("turns manifest step names into words", () => {
    expect(stepWord("app_after")).toBe("App after");
    expect(stepWord("setup")).toBe("Setup");
    expect(stepWord("")).toBe("Review");
  });
});
