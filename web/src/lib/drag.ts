/**
 * Board drag actions (docs/phase5.md, decision 7). GitHub drives where tickets go, so a
 * pass can only be dragged onto Todo, and only from two stacks: Failed (retry) and In
 * Review (cancel any active run, then run again on the same branch). Every other drop is
 * refused with a short explanation. The same two actions are buttons on an open pass, so
 * nobody has to drag.
 */
import { canRerun } from "./runs";
import type { Column, TicketCard } from "./types";

export type PassAction = "retry" | "rerun";

/** The action a pass offers, from its stack: Failed retries, In Review runs again. */
export function passAction(card: TicketCard): PassAction | null {
  if (card.column === "failed") return "retry";
  // The API refuses a rerun on a closed issue (409); an In Review issue is open, but a
  // stale card may not be.
  if (canRerun(card)) return "rerun";
  return null;
}

export const ACTION_WORDS: Record<PassAction, string> = {
  retry: "Retry",
  rerun: "Run again",
};

export type DropOutcome = PassAction | "refuse" | "none";

/**
 * What dropping a pass from `from` onto `to` does. `to` is null when it was dropped
 * outside every stack; dropping it back on its own stack is no move at all.
 */
export function dropOutcome(card: TicketCard, to: Column | null): DropOutcome {
  if (to === null || to === card.column) return "none";
  const action = passAction(card);
  if (to === "todo" && action) return action;
  return "refuse";
}

export const REFUSE_MESSAGE =
  "GitHub decides where tickets go; you can retry failed tickets or rerun ones in review.";

export const RERUN_QUESTION = "Cancel any running agent and start again on the same branch?";

/** A queued action shown on its pass ("Queuing…") until the board moves the pass. */
export interface PendingAction {
  action: PassAction;
  /** Where the pass was when the action started, and which run it showed. */
  column: Column;
  runId: string | null;
  since: number;
}

/** Stop waiting for the board this long after an action; the board is re-read then. */
export const PENDING_TIMEOUT_MS = 20_000;

export function startPending(card: TicketCard, action: PassAction, now: number): PendingAction {
  return {
    action,
    column: card.column,
    runId: card.latest_run?.id ?? null,
    since: now,
  };
}

/**
 * True once the live board reflects an action: the pass moved to another stack, shows a
 * different run, or left the board.
 */
export function boardCaughtUp(pending: PendingAction, card: TicketCard | undefined): boolean {
  if (!card) return true;
  if (card.column !== pending.column) return true;
  return (card.latest_run?.id ?? null) !== pending.runId;
}

/** True once the pass should stop saying "Queuing…": the board caught up, or the wait ran out. */
export function pendingSettled(
  pending: PendingAction,
  card: TicketCard | undefined,
  now: number,
): boolean {
  return boardCaughtUp(pending, card) || now - pending.since > PENDING_TIMEOUT_MS;
}
