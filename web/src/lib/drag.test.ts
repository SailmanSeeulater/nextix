import { describe, expect, it } from "vitest";
import {
  ACTION_WORDS,
  PENDING_TIMEOUT_MS,
  REFUSE_MESSAGE,
  RERUN_QUESTION,
  boardCaughtUp,
  dropOutcome,
  passAction,
  pendingSettled,
  startPending,
} from "./drag";
import type { Column, RunSummary, TicketCard } from "./types";

function summary(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    id: "r1",
    attempt: 1,
    status: "failed",
    agent_id: null,
    started_at: null,
    last_heartbeat: null,
    cost_usd: 0,
    ...overrides,
  };
}

function card(column: Column, overrides: Partial<TicketCard> = {}): TicketCard {
  return {
    id: "t1",
    repo: "acme/widgets",
    issue_number: 7,
    title: "Fix the thing",
    labels: [],
    issue_state: "open",
    issue_url: "https://github.com/acme/widgets/issues/7",
    pr_number: null,
    pr_state: null,
    pr_url: null,
    column,
    latest_run: summary(),
    updated_at: "2026-09-25T10:00:00Z",
    ...overrides,
  };
}

const ALL: Column[] = ["todo", "doing", "needs_input", "failed", "in_review", "done"];

describe("passAction", () => {
  it("retries Failed and runs In Review again", () => {
    expect(passAction(card("failed"))).toBe("retry");
    expect(passAction(card("in_review"))).toBe("rerun");
  });

  it("offers nothing for other stacks or a closed issue", () => {
    for (const column of ["todo", "doing", "needs_input", "done"] as Column[]) {
      expect(passAction(card(column))).toBeNull();
    }
    expect(passAction(card("in_review", { issue_state: "closed" }))).toBeNull();
  });

  it("words each action", () => {
    expect(ACTION_WORDS).toEqual({ retry: "Retry", rerun: "Run again" });
  });
});

describe("dropOutcome", () => {
  it("retries a Failed pass dropped on Todo", () => {
    expect(dropOutcome(card("failed"), "todo")).toBe("retry");
  });

  it("runs an In Review pass again when dropped on Todo", () => {
    expect(dropOutcome(card("in_review"), "todo")).toBe("rerun");
  });

  it("refuses every other move", () => {
    for (const from of ["failed", "in_review"] as Column[]) {
      for (const to of ALL.filter((c) => c !== "todo" && c !== from)) {
        expect(dropOutcome(card(from), to), `${from} → ${to}`).toBe("refuse");
      }
    }
    expect(dropOutcome(card("done"), "todo")).toBe("refuse");
    expect(dropOutcome(card("in_review", { issue_state: "closed" }), "todo")).toBe("refuse");
  });

  it("does nothing when dropped outside the stacks or back where it was", () => {
    expect(dropOutcome(card("failed"), null)).toBe("none");
    expect(dropOutcome(card("failed"), "failed")).toBe("none");
    expect(dropOutcome(card("in_review"), "in_review")).toBe("none");
  });

  it("explains refusals and asks before a rerun, in plain words", () => {
    expect(REFUSE_MESSAGE).toMatch(/GitHub decides/);
    expect(RERUN_QUESTION).toMatch(/same branch\?$/);
  });
});

describe("pending actions", () => {
  const T0 = 1_000_000;

  it("remembers where the pass was and which run it showed", () => {
    expect(startPending(card("failed"), "retry", T0)).toEqual({
      action: "retry",
      column: "failed",
      runId: "r1",
      since: T0,
    });
    expect(startPending(card("failed", { latest_run: null }), "retry", T0).runId).toBeNull();
  });

  it("waits while the board still shows the pass as it was", () => {
    const pending = startPending(card("failed"), "retry", T0);
    expect(pendingSettled(pending, card("failed"), T0 + 1000)).toBe(false);
  });

  it("settles when the pass moves, shows a new run, or leaves the board", () => {
    const pending = startPending(card("in_review"), "rerun", T0);
    expect(pendingSettled(pending, card("doing"), T0 + 1)).toBe(true);
    expect(pendingSettled(pending, card("in_review", { latest_run: summary({ id: "r2" }) }), T0 + 1)).toBe(
      true,
    );
    expect(pendingSettled(pending, undefined, T0 + 1)).toBe(true);
  });

  it("gives up waiting after the timeout", () => {
    const pending = startPending(card("failed"), "retry", T0);
    expect(pendingSettled(pending, card("failed"), T0 + PENDING_TIMEOUT_MS)).toBe(false);
    expect(pendingSettled(pending, card("failed"), T0 + PENDING_TIMEOUT_MS + 1)).toBe(true);
  });
});

describe("boardCaughtUp", () => {
  it("ignores the clock: only the board's own state counts", () => {
    const pending = startPending(card("failed"), "retry", 0);
    expect(boardCaughtUp(pending, card("failed"))).toBe(false);
    expect(boardCaughtUp(pending, card("doing"))).toBe(true);
    expect(boardCaughtUp(pending, undefined)).toBe(true);
  });
});
