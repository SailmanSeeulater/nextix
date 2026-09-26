import { describe, expect, it } from "vitest";
import {
  checkDuration,
  checkOutcome,
  safeLink,
  shortSha,
  sortChecks,
  summarizeChecks,
} from "./checks";
import type { CheckRun } from "./types";

function check(overrides: Partial<CheckRun> = {}): CheckRun {
  return {
    id: 1,
    name: "build",
    status: "completed",
    conclusion: "success",
    html_url: "https://github.com/a/b/runs/1",
    app_name: "GitHub Actions",
    started_at: "2026-09-26T10:00:00Z",
    completed_at: "2026-09-26T10:01:05Z",
    ...overrides,
  };
}

describe("checkOutcome", () => {
  it("words each status and conclusion with a tone", () => {
    expect(checkOutcome(check())).toEqual({ tone: "passed", word: "Passed" });
    expect(checkOutcome(check({ conclusion: "failure" }))).toEqual({ tone: "failed", word: "Failed" });
    expect(checkOutcome(check({ conclusion: "timed_out" })).tone).toBe("failed");
    expect(checkOutcome(check({ conclusion: "action_required" })).tone).toBe("failed");
    expect(checkOutcome(check({ conclusion: "skipped" }))).toEqual({ tone: "neutral", word: "Skipped" });
    expect(checkOutcome(check({ status: "in_progress", conclusion: null }))).toEqual({
      tone: "running",
      word: "Running",
    });
    expect(checkOutcome(check({ status: "queued", conclusion: null })).word).toBe("Queued");
    expect(checkOutcome(check({ status: "pending", conclusion: null })).word).toBe("Waiting");
    expect(checkOutcome(check({ conclusion: "something_new" })).word).toBe("Done");
  });
});

describe("checkDuration", () => {
  const now = Date.parse("2026-09-26T10:02:00Z");

  it("measures finished checks, and running ones up to now", () => {
    expect(checkDuration(check(), now)).toBe("1m 05s");
    expect(checkDuration(check({ status: "in_progress", completed_at: null }), now)).toBe("2m 00s");
    expect(checkDuration(check({ started_at: null }), now)).toBeNull();
    expect(checkDuration(check({ status: "queued", completed_at: null }), now)).toBeNull();
  });
});

describe("sortChecks and summarizeChecks", () => {
  const runs = [
    check({ id: 1, name: "lint" }),
    check({ id: 2, name: "test", conclusion: "failure" }),
    check({ id: 3, name: "deploy", status: "in_progress", conclusion: null }),
    check({ id: 4, name: "audit", conclusion: "skipped" }),
    check({ id: 5, name: "build" }),
  ];

  it("puts failures first, then running checks, then the rest by name", () => {
    expect(sortChecks(runs).map((c) => c.name)).toEqual(["test", "deploy", "build", "lint", "audit"]);
  });

  it("summarizes in one line", () => {
    expect(summarizeChecks(runs)).toMatchObject({
      total: 5,
      failed: 1,
      passed: 2,
      pending: 1,
      text: "5 checks: 1 failed, 1 pending, 2 passed",
    });
    expect(summarizeChecks([check()]).text).toBe("1 check: 1 passed");
    expect(summarizeChecks([]).text).toBe("0 checks");
  });
});

describe("links and shas", () => {
  it("only links to https", () => {
    expect(safeLink("https://github.com/a/b/runs/1")).toBe("https://github.com/a/b/runs/1");
    expect(safeLink("javascript:alert(1)")).toBeNull();
    expect(safeLink("http://example.com")).toBeNull();
    expect(safeLink("not a url")).toBeNull();
    expect(safeLink(null)).toBeNull();
  });

  it("shortens a commit sha", () => {
    expect(shortSha("3f9a1c2d4e5f")).toBe("3f9a1c2");
    expect(shortSha(null)).toBeNull();
  });
});
