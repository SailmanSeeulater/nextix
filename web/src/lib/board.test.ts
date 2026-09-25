import { describe, expect, it } from "vitest";
import {
  applyEvent,
  formatElapsed,
  groupByColumn,
  indexCards,
  isHeartbeatStale,
  repoOptions,
  statusLabel,
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
});

describe("statusLabel", () => {
  it("describes each column", () => {
    expect(statusLabel(card())).toBe("ready");
    expect(statusLabel(card({ column: "doing", latest_run: run() }))).toBe("running");
    expect(statusLabel(card({ column: "in_review", pr_number: 7 }))).toBe("PR #7 open");
    expect(statusLabel(card({ column: "done", pr_number: 7, pr_state: "merged" }))).toBe(
      "PR #7 merged",
    );
    expect(statusLabel(card({ column: "done" }))).toBe("closed");
    expect(
      statusLabel(card({ column: "failed", latest_run: run({ status: "timed_out" }) })),
    ).toBe("timed out");
  });
});
