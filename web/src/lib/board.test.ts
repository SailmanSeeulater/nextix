import { describe, expect, it } from "vitest";
import {
  applyEvent,
  changesLayout,
  formatElapsed,
  formatSeconds,
  groupByColumn,
  indexCards,
  formatAgo,
  headerField,
  HEARTBEAT_RECHECK_MS,
  HEARTBEAT_STALE_MS,
  heartbeatNeedsRecheck,
  isHeartbeatStale,
  isStalled,
  liveness,
  mergeHeartbeats,
  passFields,
  repoLabels,
  repoOptions,
} from "./board";
import type { RunSummary, TicketCard } from "./types";

function card(overrides: Partial<TicketCard> = {}): TicketCard {
  return {
    id: "t1",
    repo: "acme/widgets",
    issue_number: 1,
    title: "A ticket",
    labels: ["nextix"],
    issue_state: "open",
    issue_url: "https://github.com/acme/widgets/issues/1",
    pr_number: null,
    pr_state: null,
    pr_url: null,
    column: "todo",
    latest_run: null,
    updated_at: "2026-09-25T10:00:00Z",
    ...overrides,
  };
}

function run(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    id: "r1",
    attempt: 1,
    status: "running",
    agent_id: "agent-ab12",
    started_at: "2026-09-25T10:00:00Z",
    last_heartbeat: "2026-09-25T10:00:00Z",
    cost_usd: 0.12,
    ...overrides,
  };
}

describe("applyEvent", () => {
  it("adds and replaces cards on ticket.updated", () => {
    const start = indexCards([card()]);
    const moved = applyEvent(start, {
      type: "ticket.updated",
      data: card({ column: "in_review" }),
    });
    expect(moved.t1?.column).toBe("in_review");
    expect(start.t1?.column).toBe("todo"); // input untouched

    const added = applyEvent(moved, { type: "ticket.updated", data: card({ id: "t2" }) });
    expect(Object.keys(added).sort()).toEqual(["t1", "t2"]);
  });

  it("removes cards on ticket.removed and ignores unknown ids", () => {
    const start = indexCards([card()]);
    expect(applyEvent(start, { type: "ticket.removed", data: { id: "t1" } })).toEqual({});
    expect(applyEvent(start, { type: "ticket.removed", data: { id: "zz" } })).toBe(start);
  });
});

describe("groupByColumn", () => {
  const index = indexCards([
    card({ id: "a", column: "todo", updated_at: "2026-09-25T09:00:00Z" }),
    card({ id: "b", column: "todo", updated_at: "2026-09-25T11:00:00Z" }),
    card({ id: "c", column: "done", repo: "acme/gadgets" }),
  ]);

  it("puts every card in its column, newest first, with all columns present", () => {
    const groups = groupByColumn(index);
    expect(groups.todo.map((c) => c.id)).toEqual(["b", "a"]);
    expect(groups.done.map((c) => c.id)).toEqual(["c"]);
    expect(groups.doing).toEqual([]);
  });

  it("filters by repo", () => {
    const groups = groupByColumn(index, "acme/gadgets");
    expect(groups.todo).toEqual([]);
    expect(groups.done).toHaveLength(1);
  });

  it("lists repos for the filter", () => {
    expect(repoOptions(index)).toEqual(["acme/gadgets", "acme/widgets"]);
  });
});

describe("liveness helpers", () => {
  const now = Date.parse("2026-09-25T10:01:05Z");

  it("flags stale heartbeats after 30s", () => {
    expect(isHeartbeatStale(run({ last_heartbeat: "2026-09-25T10:00:50Z" }), now)).toBe(false);
    expect(isHeartbeatStale(run({ last_heartbeat: "2026-09-25T10:00:30Z" }), now)).toBe(true);
    expect(isHeartbeatStale(run({ last_heartbeat: null }), now)).toBe(true);
  });

  it("formats elapsed time", () => {
    expect(formatElapsed("2026-09-25T10:01:00Z", now)).toBe("5s");
    expect(formatElapsed("2026-09-25T10:00:00Z", now)).toBe("1m 05s");
    expect(formatElapsed("2026-09-25T08:00:00Z", now)).toBe("2h 1m");
  });

  it("formats measured durations the same way, with tenths under ten seconds", () => {
    expect(formatSeconds(4.24)).toBe("4.2s");
    expect(formatSeconds(0)).toBe("0s");
    expect(formatSeconds(9.96)).toBe("10s");
    expect(formatSeconds(12.3)).toBe("12s");
    expect(formatSeconds(65)).toBe("1m 05s");
    expect(formatSeconds(7260)).toBe("2h 1m");
    expect(formatSeconds(Number.NaN)).toBe("–");
  });

  it("re-reads a live run's heartbeat before it would look lost", () => {
    // 15s old: fresh enough. 25s old: re-read now, before the 30s alarm.
    expect(heartbeatNeedsRecheck(run({ last_heartbeat: "2026-09-25T10:00:50Z" }), now)).toBe(false);
    expect(heartbeatNeedsRecheck(run({ last_heartbeat: "2026-09-25T10:00:40Z" }), now)).toBe(true);
    expect(heartbeatNeedsRecheck(run({ status: "claimed", last_heartbeat: null }), now)).toBe(true);
    expect(HEARTBEAT_RECHECK_MS).toBeLessThan(HEARTBEAT_STALE_MS);
  });

  it("doesn't re-read runs that send no heartbeat", () => {
    const old = "2026-09-25T09:00:00Z";
    expect(heartbeatNeedsRecheck(null, now)).toBe(false);
    expect(heartbeatNeedsRecheck(run({ status: "queued", last_heartbeat: null }), now)).toBe(false);
    expect(heartbeatNeedsRecheck(run({ status: "failed", last_heartbeat: old }), now)).toBe(false);
  });
});

describe("mergeHeartbeats", () => {
  const doing = (overrides: Partial<RunSummary> = {}) =>
    card({ column: "doing", latest_run: run(overrides) });

  it("takes a newer heartbeat for the same run", () => {
    const index = indexCards([doing({ last_heartbeat: "2026-09-25T10:00:00Z" })]);
    const next = mergeHeartbeats(index, [doing({ last_heartbeat: "2026-09-25T10:00:20Z" })]);
    expect(next.t1?.latest_run?.last_heartbeat).toBe("2026-09-25T10:00:20Z");
    expect(index.t1?.latest_run?.last_heartbeat).toBe("2026-09-25T10:00:00Z"); // untouched
  });

  it("takes nothing else from the read, so a slow read can't undo a live move", () => {
    const index = indexCards([card({ column: "in_review", latest_run: run({ status: "succeeded" }) })]);
    const stale = doing({ status: "running", last_heartbeat: "2026-09-25T10:00:20Z" });
    const next = mergeHeartbeats(index, [stale]);
    expect(next.t1?.column).toBe("in_review");
    expect(next.t1?.latest_run?.status).toBe("succeeded");
  });

  it("returns the same index when no heartbeat is newer, or the run changed", () => {
    const index = indexCards([doing({ last_heartbeat: "2026-09-25T10:00:20Z" })]);
    expect(mergeHeartbeats(index, [doing({ last_heartbeat: "2026-09-25T10:00:10Z" })])).toBe(index);
    expect(mergeHeartbeats(index, [doing({ id: "r2", last_heartbeat: "2026-09-25T10:01:00Z" })])).toBe(
      index,
    );
    expect(mergeHeartbeats(index, [card({ id: "other" })])).toBe(index);
  });
});

describe("passFields", () => {
  const now = Date.parse("2026-09-25T10:05:00Z");
  const labels = (c: TicketCard) => passFields(c, now).map((f) => `${f.label}: ${f.value}`);

  it("shows liveness, elapsed and cost for Doing", () => {
    const fresh = run({ last_heartbeat: "2026-09-25T10:04:50Z" });
    expect(labels(card({ column: "doing", latest_run: fresh }))).toEqual([
      "Agent: agent-ab12",
      "Heartbeat: Live",
      "Spent: $0.12",
    ]);
  });

  it("flags a lost heartbeat as an alert", () => {
    const lost = run({ last_heartbeat: "2026-09-25T10:03:00Z" });
    const hb = passFields(card({ column: "doing", latest_run: lost }), now).find(
      (f) => f.label === "Heartbeat",
    );
    expect(hb).toEqual({ label: "Heartbeat", value: "Lost 2m 00s ago", tone: "alert" });
  });

  it("shows a queued run as waiting for a worker, without a heartbeat", () => {
    const queued = run({ status: "queued", agent_id: null, started_at: null, last_heartbeat: null });
    expect(labels(card({ column: "doing", latest_run: queued }))).toEqual([
      "Agent: Waiting for a worker",
      "Spent: $0.12",
    ]);
  });

  it("describes the other states with facts the API carries", () => {
    expect(labels(card({ labels: ["nextix", "ui"], created_via: "web" }))).toEqual([
      "Filed: 5m",
      "Via: Board",
      "Labels: ui",
    ]);
    expect(labels(card({ column: "failed", latest_run: run({ status: "timed_out", attempt: 2 }) }))).toEqual([
      "Run: Timed out",
      "Attempt: 2",
      "Spent: $0.12",
    ]);
    expect(labels(card({ column: "done", pr_number: 7, pr_state: "merged" }))[0]).toBe("Outcome: Merged #7");
    expect(labels(card({ column: "done" }))[0]).toBe("Outcome: Closed");
    expect(labels(card({ column: "in_review", pr_number: 7 }))[0]).toBe("Pull request: #7");
  });

  it("keeps the issue number in the header of every pass", () => {
    expect(headerField(card({ column: "doing", latest_run: run() }))).toBe("#1");
    expect(headerField(card())).toBe("#1");
  });

  it("reads liveness for Doing passes only", () => {
    const live = run({ last_heartbeat: "2026-09-25T10:04:50Z" });
    expect(liveness(card({ column: "doing", latest_run: live }), now)).toEqual({
      state: "live",
      elapsed: "5m 00s",
    });
    const quiet = run({ last_heartbeat: "2026-09-25T10:04:15Z" });
    expect(liveness(card({ column: "doing", latest_run: quiet }), now)).toEqual({
      state: "stalled",
      quietFor: "45s",
    });
    expect(liveness(card({ column: "doing", latest_run: run({ status: "queued" }) }), now)).toEqual({
      state: "queued",
    });
    expect(liveness(card(), now)).toBeNull();
  });

  it("puts stalled passes at the front of the Doing stack", () => {
    const live = card({
      id: "live",
      column: "doing",
      updated_at: "2026-09-25T10:04:00Z",
      latest_run: run({ last_heartbeat: "2026-09-25T10:04:55Z" }),
    });
    const stalled = card({
      id: "stalled",
      column: "doing",
      updated_at: "2026-09-25T09:00:00Z",
      latest_run: run({ last_heartbeat: "2026-09-25T10:00:00Z" }),
    });
    expect(isStalled(stalled, now)).toBe(true);
    expect(groupByColumn(indexCards([live, stalled]), null, now).doing.map((c) => c.id)).toEqual([
      "stalled",
      "live",
    ]);
    expect(groupByColumn(indexCards([live, stalled])).doing.map((c) => c.id)).toEqual([
      "live",
      "stalled",
    ]);
  });

  it("formats coarse ages", () => {
    expect(formatAgo("2026-09-25T10:04:30Z", now)).toBe("just now");
    expect(formatAgo("2026-09-25T08:05:00Z", now)).toBe("2h");
    expect(formatAgo("2026-09-22T10:05:00Z", now)).toBe("3d");
  });
});

describe("changesLayout", () => {
  const index = indexCards([card()]);

  it("is false for in-place updates such as heartbeats", () => {
    expect(changesLayout(index, [{ type: "ticket.updated", data: card({ title: "renamed" }) }])).toBe(false);
  });

  it("is true when a pass is added, removed, or changes stack", () => {
    expect(changesLayout(index, [{ type: "ticket.updated", data: card({ id: "t2" }) }])).toBe(true);
    expect(changesLayout(index, [{ type: "ticket.removed", data: { id: "t1" } }])).toBe(true);
    expect(changesLayout(index, [{ type: "ticket.updated", data: card({ column: "doing" }) }])).toBe(true);
    expect(changesLayout(index, [{ type: "ticket.removed", data: { id: "nope" } }])).toBe(false);
  });
});

describe("repoLabels", () => {
  it("uses the short name unless two owners share it", () => {
    expect(repoLabels(["acme/web", "acme/api", "other/web", "acme/api"])).toEqual({
      "acme/web": "acme/web",
      "other/web": "other/web",
      "acme/api": "api",
    });
  });
});
