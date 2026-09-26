import { describe, expect, it } from "vitest";
import {
  QUEUE_PATIENCE_MS,
  activeRun,
  applyUsage,
  attemptLabel,
  canRetry,
  exitReasonWord,
  isActiveStatus,
  laterStatus,
  laterTime,
  mergeCard,
  mergeRunSummary,
  mergeRuns,
  noWorkerAvailable,
  runFields,
  runReadout,
  sortRuns,
  toNumber,
  upsertRun,
} from "./runs";
import type { RunDetail, TicketCard, TicketDetail } from "./types";

const NOW = Date.parse("2026-09-25T10:10:00Z");

function run(overrides: Partial<RunDetail> = {}): RunDetail {
  return {
    id: "r1",
    attempt: 1,
    trigger: "initial",
    status: "running",
    agent_id: "agent-3fa9c1",
    branch: "nextix/issue-7",
    queued_at: "2026-09-25T10:00:00Z",
    started_at: "2026-09-25T10:01:00Z",
    finished_at: null,
    last_heartbeat: "2026-09-25T10:09:55Z",
    exit_reason: null,
    input_tokens: 0,
    output_tokens: 0,
    cost_usd: 0,
    ...overrides,
  };
}

function card(overrides: Partial<TicketCard> = {}): TicketCard {
  return {
    id: "t1",
    repo: "acme/widgets",
    issue_number: 7,
    title: "Fix the thing",
    labels: ["nextix", "ui"],
    issue_state: "open",
    issue_url: "https://github.com/acme/widgets/issues/7",
    pr_number: null,
    pr_state: null,
    pr_url: null,
    column: "doing",
    latest_run: null,
    updated_at: "2026-09-25T10:00:00Z",
    ...overrides,
  };
}

const field = (fields: { label: string; value: string }[], label: string) =>
  fields.find((f) => f.label === label);

describe("active runs and retry", () => {
  it("counts queued, claimed and running as active", () => {
    expect(["queued", "claimed", "running"].every(isActiveStatus)).toBe(true);
    expect(["succeeded", "failed", "timed_out", "needs_input", "cancelled"].some(isActiveStatus)).toBe(
      false,
    );
  });

  it("finds the active run", () => {
    expect(activeRun([run({ id: "a", status: "failed" }), run({ id: "b", status: "queued" })])?.id).toBe(
      "b",
    );
    expect(activeRun([run({ status: "succeeded" })])).toBeNull();
  });

  it("offers retry on Failed, Todo and Needs Input tickets with no active run", () => {
    const failed = [run({ status: "failed" })];
    expect(canRetry("failed", failed)).toBe(true);
    expect(canRetry("needs_input", [run({ status: "needs_input" })])).toBe(true);
    expect(canRetry("todo", [])).toBe(true);
    expect(canRetry("failed", [...failed, run({ id: "r2", status: "queued" })])).toBe(false);
    expect(canRetry("in_review", [run({ status: "succeeded" })])).toBe(false);
    expect(canRetry("done", [])).toBe(false);
    expect(canRetry("doing", [])).toBe(false);
  });
});

describe("noWorkerAvailable", () => {
  it("says so once a run has sat queued past the patience window", () => {
    const queued = run({ status: "queued", started_at: null });
    const at = Date.parse(queued.queued_at!);
    expect(noWorkerAvailable(queued, at + QUEUE_PATIENCE_MS - 1000)).toBe(false);
    expect(noWorkerAvailable(queued, at + QUEUE_PATIENCE_MS + 1000)).toBe(true);
    expect(noWorkerAvailable(run(), at + QUEUE_PATIENCE_MS * 2)).toBe(false);
  });
});

describe("merging live updates", () => {
  it("sorts runs newest first", () => {
    const sorted = sortRuns([run({ id: "a", attempt: 1 }), run({ id: "c", attempt: 3 }), run({ id: "b", attempt: 2 })]);
    expect(sorted.map((r) => r.id)).toEqual(["c", "b", "a"]);
  });

  it("upserts a new run at the top and replaces a known one", () => {
    const runs = [run({ id: "a", attempt: 1, status: "failed" })];
    const withRetry = upsertRun(runs, run({ id: "b", attempt: 2, status: "queued" }));
    expect(withRetry.map((r) => r.id)).toEqual(["b", "a"]);
    const claimed = upsertRun(withRetry, run({ id: "b", attempt: 2, status: "claimed" }));
    expect(claimed[0]?.status).toBe("claimed");
    expect(claimed).toHaveLength(2);
  });

  it("never moves a run backwards, whichever stream is late", () => {
    expect(laterStatus("running", "claimed")).toBe("running");
    expect(laterStatus("succeeded", "running")).toBe("succeeded");
    expect(laterStatus("queued", "cancelled")).toBe("cancelled");
    const done = [run({ status: "succeeded", finished_at: "2026-09-25T10:09:00Z" })];
    expect(upsertRun(done, run({ status: "running" }))[0]?.status).toBe("succeeded");
    expect(mergeRuns(done, [run({ status: "running" })])[0]?.status).toBe("succeeded");
  });

  it("keeps live counters that ran ahead of a fresh read", () => {
    const live = [run({ input_tokens: 900, output_tokens: 90, cost_usd: 0.4 })];
    const beat = "2026-09-25T10:09:59Z";
    const fresh = [run({ input_tokens: 500, output_tokens: 50, cost_usd: 0.2, last_heartbeat: beat })];
    const merged = mergeRuns(live, fresh)[0]!;
    expect(merged).toMatchObject({ input_tokens: 900, output_tokens: 90, cost_usd: 0.4, last_heartbeat: beat });
  });

  it("never moves a heartbeat back, so a late update can't fake a lost one", () => {
    const live = [run({ last_heartbeat: "2026-09-25T10:09:55Z" })];
    const late = run({ last_heartbeat: "2026-09-25T10:09:20Z" });
    expect(upsertRun(live, late)[0]?.last_heartbeat).toBe("2026-09-25T10:09:55Z");
    expect(mergeRuns(live, [late])[0]?.last_heartbeat).toBe("2026-09-25T10:09:55Z");
    expect(laterTime(null, "2026-09-25T10:00:00Z")).toBe("2026-09-25T10:00:00Z");
    expect(laterTime("2026-09-25T10:00:00Z", "junk")).toBe("2026-09-25T10:00:00Z");
    expect(laterTime(null, null)).toBeNull();
  });

  it("folds a board card's run summary into a known run", () => {
    const runs = [run({ cost_usd: 0.1 })];
    const merged = mergeRunSummary(runs, {
      id: "r1",
      attempt: 1,
      status: "running",
      agent_id: "agent-3fa9c1",
      started_at: "2026-09-25T10:01:00Z",
      last_heartbeat: "2026-09-25T10:10:00Z",
      cost_usd: 0.3,
    });
    expect(merged?.[0]).toMatchObject({ last_heartbeat: "2026-09-25T10:10:00Z", cost_usd: 0.3 });
  });

  it("asks for a re-read when the summary names a run it doesn't know", () => {
    expect(
      mergeRunSummary([run()], {
        id: "new",
        attempt: 2,
        status: "queued",
        agent_id: null,
        started_at: null,
        last_heartbeat: null,
        cost_usd: 0,
      }),
    ).toBeNull();
  });

  it("applies cumulative usage, never lowering a counter", () => {
    const r = run({ input_tokens: 100, output_tokens: 10, cost_usd: 0.05 });
    expect(applyUsage(r, { input_tokens: 200, output_tokens: 20, cost_usd: 0.1 })).toMatchObject({
      input_tokens: 200,
      output_tokens: 20,
      cost_usd: 0.1,
    });
    expect(applyUsage(r, { input_tokens: 50, output_tokens: 5, cost_usd: 0.01 })).toBe(r);
  });

  it("keeps artifacts and test results that a stream payload leaves out", () => {
    const artifacts = [
      { id: "a1", kind: "test_report", label: "npm test", url: "/api/artifacts/a1", meta: null },
    ];
    const tests = { command: "npm test", exit_code: 1, passed: false, duration_s: 12.3 };
    const errors = [{ step: "setup", label: null, message: "npm ci exited 1" }];
    const known = [run({ status: "succeeded", artifacts, tests, review_errors: errors })];
    // run.state / run.updated payloads carry no artifacts (only the ticket read does).
    const streamed = upsertRun(known, run({ status: "succeeded", cost_usd: 0.5 }))[0]!;
    expect(streamed).toMatchObject({ artifacts, tests, review_errors: errors, cost_usd: 0.5 });
    // A fresh ticket read replaces them.
    const fresh = mergeRuns(known, [run({ status: "succeeded", artifacts: [], tests: null })])[0]!;
    expect(fresh.artifacts).toEqual([]);
    expect(fresh.tests).toBeNull();
  });

  it("doesn't let a stale ticket read wipe the results a finished run already has", () => {
    const artifacts = [
      { id: "a1", kind: "test_report", label: "npm test", url: "/api/artifacts/a1", meta: null },
    ];
    const tests = { command: "npm test", exit_code: 0, passed: true, duration_s: 3 };
    const known = [run({ status: "succeeded", artifacts, tests, review_errors: [] })];
    // A read that left before the run ended comes back with the running copy.
    const stale = run({ status: "running", artifacts: [], tests: null, review_errors: [] });
    const merged = mergeRuns(known, [stale])[0]!;
    expect(merged).toMatchObject({ status: "succeeded", artifacts, tests });
    // A finished run learned from the stream (no artifacts yet) still takes the read's list.
    const streamedOnly = [run({ status: "succeeded", tests })];
    const read = run({ status: "succeeded", artifacts, tests });
    expect(mergeRuns(streamedOnly, [read])[0]!.artifacts).toEqual(artifacts);
  });

  it("merges a card over the detail without dropping body or runs", () => {
    const detail: TicketDetail = { ...card(), body: "Steps", runs: [run()] };
    const merged = mergeCard(detail, card({ column: "in_review", pr_number: 9, pr_url: "u" }));
    expect(merged).toMatchObject({ column: "in_review", pr_number: 9, body: "Steps" });
    expect(merged.runs).toHaveLength(1);
  });
});

describe("runFields", () => {
  it("shows status, attempt, agent, elapsed, heartbeat, spent and tokens for a live run", () => {
    const fields = runFields(
      card(),
      run({ attempt: 2, trigger: "retry", input_tokens: 12345, output_tokens: 678, cost_usd: 0.42 }),
      NOW,
    );
    expect(fields.map((f) => f.label)).toEqual([
      "Status",
      "Attempt",
      "Agent",
      "Elapsed",
      "Heartbeat",
      "Spent",
      "Tokens",
    ]);
    expect(field(fields, "Status")?.value).toBe("Running");
    expect(fields.find((f) => f.label === "Attempt")).toMatchObject({ value: "2", detail: "Retry" });
    expect(field(fields, "Elapsed")?.value).toBe("9m 00s");
    expect(field(fields, "Heartbeat")?.value).toBe("Live");
    expect(field(fields, "Spent")?.value).toBe("$0.42");
    expect(fields.find((f) => f.label === "Tokens")).toMatchObject({
      value: "13,023",
      detail: "12,345 in · 678 out",
    });
  });

  it("flags a lost heartbeat", () => {
    const fields = runFields(card(), run({ last_heartbeat: "2026-09-25T10:08:00Z" }), NOW);
    expect(fields.find((f) => f.label === "Heartbeat")).toMatchObject({
      value: "Lost 2m 00s ago",
      tone: "alert",
    });
  });

  it("shows duration and the reason for a finished run", () => {
    const fields = runFields(
      card({ column: "failed" }),
      run({
        status: "failed",
        finished_at: "2026-09-25T10:04:30Z",
        exit_reason: "heartbeat_lost",
        cost_usd: "1.5" as unknown as number,
      }),
      NOW,
    );
    expect(field(fields, "Duration")?.value).toBe("3m 30s");
    expect(field(fields, "Reason")?.value).toBe("Heartbeat lost");
    expect(field(fields, "Spent")?.value).toBe("$1.50");
    expect(field(fields, "Heartbeat")).toBeUndefined();
    expect(field(fields, "Elapsed")).toBeUndefined();
  });

  it("doesn't repeat a reason that only restates the status", () => {
    const fields = runFields(
      card({ column: "failed" }),
      run({ status: "cancelled", finished_at: "2026-09-25T10:04:30Z", exit_reason: "cancelled" }),
      NOW,
    );
    expect(field(fields, "Status")?.value).toBe("Cancelled");
    expect(field(fields, "Reason")).toBeUndefined();
  });

  it("shows how long a queued run has waited, without empty Agent or $0.00 slots", () => {
    const fields = runFields(
      card(),
      run({ status: "queued", agent_id: null, started_at: null, last_heartbeat: null }),
      NOW,
    );
    expect(field(fields, "Agent")).toBeUndefined();
    expect(field(fields, "Queued")?.value).toBe("10m 00s");
    expect(field(fields, "Spent")).toBeUndefined();
    expect(field(fields, "Tokens")).toBeUndefined();
  });

  it("shows the test result, flagging a failure with its exit code", () => {
    const failed = runFields(
      card({ column: "in_review" }),
      run({
        status: "succeeded",
        finished_at: "2026-09-25T10:04:30Z",
        tests: { command: "npm test", exit_code: 1, passed: false, duration_s: 12.3 },
      }),
      NOW,
    );
    expect(failed.find((f) => f.label === "Tests")).toMatchObject({
      value: "Failed",
      tone: "alert",
      detail: "exit 1 · 12s",
    });
    const passed = runFields(
      card({ column: "in_review" }),
      run({ status: "succeeded", tests: { command: "pytest", exit_code: 0, passed: true, duration_s: 4.24 } }),
      NOW,
    );
    expect(passed.find((f) => f.label === "Tests")).toMatchObject({ value: "Passed", detail: "4.2s" });
    expect(field(runFields(card(), run({ tests: null }), NOW), "Tests")).toBeUndefined();
  });

  it("falls back to the board's fields for a ticket that never ran", () => {
    const fields = runFields(card({ column: "todo" }), null, NOW);
    expect(fields.map((f) => f.label)).toContain("Filed");
  });
});

describe("runReadout", () => {
  it("is the live elapsed time while running", () => {
    expect(runReadout(run(), NOW)).toEqual({ state: "live", elapsed: "9m 00s" });
  });

  it("goes stalled when the heartbeat is stale", () => {
    expect(runReadout(run({ last_heartbeat: "2026-09-25T10:09:00Z" }), NOW)).toEqual({
      state: "stalled",
      quietFor: "1m 00s",
    });
  });

  it("waits for a worker while queued", () => {
    expect(runReadout(run({ status: "queued" }), NOW)).toEqual({ state: "queued", noWorker: false });
  });

  it("is absent for finished runs and tickets without runs", () => {
    expect(runReadout(run({ status: "succeeded" }), NOW)).toBeNull();
    expect(runReadout(null, NOW)).toBeNull();
  });
});

describe("words", () => {
  it("words exit reasons, de-snaking unknown ones", () => {
    expect(exitReasonWord("no_changes")).toBe("No changes to commit");
    expect(exitReasonWord("docker_oom_killed")).toBe("Docker oom killed");
  });

  it("labels attempts for the selector", () => {
    expect(attemptLabel(run({ attempt: 3, status: "timed_out" }))).toBe("Attempt 3 · Timed out");
  });

  it("reads Decimal strings and junk as numbers", () => {
    expect(toNumber("0.25")).toBe(0.25);
    expect(toNumber(undefined)).toBe(0);
    expect(toNumber("abc")).toBe(0);
  });
});
