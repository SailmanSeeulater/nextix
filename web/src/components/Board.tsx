"use client";

import {
  DndContext,
  DragOverlay,
  MouseSensor,
  TouchSensor,
  pointerWithin,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { ChartColumn, Info, RefreshCw, TriangleAlert, Unplug } from "lucide-react";
import Link from "next/link";
import { flushSync } from "react-dom";
import { logout } from "@/app/login/actions";
import {
  COLUMNS,
  HEARTBEAT_RECHECK_INTERVAL_MS,
  applyEvent,
  changesLayout,
  groupByColumn,
  heartbeatNeedsRecheck,
  indexCards,
  mergeHeartbeats,
  repoLabels,
  repoOptions,
  type CardIndex,
} from "@/lib/board";
import type { ClaudeAuth } from "@/lib/api";
import { ApiError, loadTickets, rerunTicket, retryTicket } from "@/lib/client-api";
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
  type PassAction,
  type PendingAction,
} from "@/lib/drag";
import type { BoardEvent, Column, CreateTicketResult, RepoOption, TicketCard } from "@/lib/types";
import { COLUMN_ICONS } from "./columnIcons";
import { Composer } from "./Composer";
import { ConfirmDialog } from "./ConfirmDialog";
import { Pass } from "./Pass";
import { ThemePicker } from "./ThemePicker";
import { useNow } from "./useNow";

type Connection = "connecting" | "live" | "offline";
type ApiState = "ok" | "error" | "unreachable";

const COLUMN_TITLES = Object.fromEntries(COLUMNS.map((c) => [c.key, c.title])) as Record<
  Column,
  string
>;

/** dnd-kit's default text describes a keyboard sensor this board doesn't use. */
const DRAG_INSTRUCTIONS = {
  draggable:
    "Failed and In Review passes can be dragged onto Todo. Without dragging, open the pass and use Retry or Run again.",
};

/** A refusal or an error from a drag action stays this long, then clears. */
const NOTICE_MS = 8000;

/** The Done stack shows this many passes before "Show all". */
const DONE_LIMIT = 6;
const MORPH_NAME = "new-pass";

/**
 * Run a board update inside a view transition so passes travel between stacks.
 * Falls back to a plain update when the browser can't, the tab is hidden, or the
 * user prefers reduced motion.
 */
function withTransition(update: () => void): Promise<void> {
  const canAnimate =
    typeof document !== "undefined" &&
    typeof document.startViewTransition === "function" &&
    document.visibilityState === "visible" &&
    !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!canAnimate) {
    update();
    return Promise.resolve();
  }
  const transition = document.startViewTransition(() => flushSync(update));
  // A newer transition skips this one; that's expected, not an error.
  transition.ready.catch(() => undefined);
  return transition.finished.catch(() => undefined);
}


export function Board({
  initial,
  repos,
  apiState,
  loadError,
  renderedAt,
  claudeAuth,
}: {
  initial: TicketCard[];
  /** null when the API couldn't be asked, as opposed to an empty list. */
  repos: RepoOption[] | null;
  apiState: ApiState;
  loadError: boolean;
  /** Server clock at render, so the first client render matches the HTML exactly. */
  renderedAt: number;
  /** Which Claude credential triage uses, or null when the API couldn't say. */
  claudeAuth: ClaudeAuth | null;
}) {
  const [cards, setCards] = useState<CardIndex>(() => indexCards(initial));
  const [repo, setRepo] = useState("");
  const [connection, setConnection] = useState<Connection>("connecting");
  const [openIds, setOpenIds] = useState<Partial<Record<Column, string>>>({});
  const [active, setActive] = useState<Column>("todo");
  const [showAllDone, setShowAllDone] = useState(false);
  const [fresh, setFresh] = useState<{ id: string; morph: boolean } | null>(null);
  const morphRef = useRef<HTMLDivElement>(null);
  const cardsRef = useRef(cards);
  const now = useNow(renderedAt, 1000);
  // Drag actions: what was sent and not yet reflected on the board, the pass in flight,
  // the rerun waiting for a yes, and a short note after a refusal or an error.
  const [pending, setPending] = useState<Record<string, PendingAction>>({});
  const [dragId, setDragId] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<TicketCard | null>(null);
  const [notice, setNotice] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** A pointer drag ends with a click on whatever is under it; that click isn't a toggle. */
  const justDragged = useRef(false);

  useEffect(() => {
    cardsRef.current = cards;
  }, [cards]);

  useEffect(() => {
    // Events that arrive together are applied together. Only changes that move a
    // pass between stacks animate; heartbeat and cost ticks update in place.
    let pending: BoardEvent[] = [];
    let timer: ReturnType<typeof setTimeout> | null = null;
    const flush = () => {
      const events = pending;
      pending = [];
      timer = null;
      const apply = () => setCards((c) => events.reduce(applyEvent, c));
      if (changesLayout(cardsRef.current, events)) void withTransition(apply);
      else apply();
    };
    const enqueue = (event: BoardEvent) => {
      pending.push(event);
      timer ??= setTimeout(flush, 60);
    };

    const source = new EventSource("/api/stream");
    // `ready` fires on every (re)connect once the server is subscribed: reload the
    // full board so nothing published while disconnected is missed.
    source.addEventListener("ready", async () => {
      setConnection("live");
      const all = await loadTickets();
      if (all) setCards(indexCards(all));
    });
    source.addEventListener("ticket.updated", (e) => {
      const data = JSON.parse((e as MessageEvent<string>).data) as TicketCard;
      enqueue({ type: "ticket.updated", data });
    });
    source.addEventListener("ticket.removed", (e) => {
      const data = JSON.parse((e as MessageEvent<string>).data) as { id: string };
      enqueue({ type: "ticket.removed", data });
    });
    source.onerror = () => setConnection("offline"); // EventSource retries by itself
    return () => {
      if (timer) clearTimeout(timer);
      source.close();
    };
  }, []);

  // Heartbeats aren't pushed (only status and usage changes are), so re-read a live
  // run's heartbeat before its pass would go hollow for an agent that is still alive.
  const lastRecheck = useRef(0);
  useEffect(() => {
    if (now - lastRecheck.current < HEARTBEAT_RECHECK_INTERVAL_MS) return;
    if (!Object.values(cards).some((c) => heartbeatNeedsRecheck(c.latest_run, now))) return;
    lastRecheck.current = now;
    void loadTickets().then((all) => {
      if (all) setCards((c) => mergeHeartbeats(c, all));
    });
  }, [cards, now]);

  const onCreated = useCallback((result: CreateTicketResult) => {
    const card = result.ticket;
    // The composer's text block is the old snapshot; the new pass is the new one.
    morphRef.current?.style.setProperty("view-transition-name", MORPH_NAME);
    void withTransition(() => {
      morphRef.current?.style.removeProperty("view-transition-name");
      setCards((c) => applyEvent(c, { type: "ticket.updated", data: card }));
      setOpenIds((o) => ({ ...o, [card.column]: card.id }));
      setActive(card.column);
      setFresh({ id: card.id, morph: true });
    }).then(() => {
      morphRef.current?.style.removeProperty("view-transition-name");
      setFresh((f) => (f && f.id === card.id ? { id: card.id, morph: false } : f));
    });
  }, []);

  const say = useCallback((text: string, tone: "info" | "error") => {
    if (noticeTimer.current) clearTimeout(noticeTimer.current);
    setNotice({ text, tone });
    noticeTimer.current = setTimeout(() => setNotice(null), NOTICE_MS);
  }, []);

  useEffect(() => {
    const timer = noticeTimer;
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  const runAction = useCallback(
    async (card: TicketCard, action: PassAction) => {
      setNotice(null);
      const started = startPending(card, action, Date.now());
      setPending((p) => ({ ...p, [card.id]: started }));
      try {
        await (action === "retry" ? retryTicket(card.id) : rerunTicket(card.id));
        // Live events move the pass; if none arrive in time, re-read the whole board.
        setTimeout(() => {
          if (boardCaughtUp(started, cardsRef.current[card.id])) return;
          void loadTickets().then((all) => {
            if (all) setCards(indexCards(all));
          });
        }, PENDING_TIMEOUT_MS + 500);
      } catch (err) {
        setPending((p) => {
          const next = { ...p };
          delete next[card.id];
          return next;
        });
        const why = err instanceof ApiError ? err.message : "Something went wrong. Try again.";
        say(`#${card.issue_number}: ${why}`, "error");
      }
    },
    [say],
  );

  const requestAction = useCallback(
    (card: TicketCard, action: PassAction) => {
      if (action === "rerun") setConfirming(card);
      else void runAction(card, action);
    },
    [runAction],
  );

  function isPending(id: string): boolean {
    const p = pending[id];
    return p !== undefined && !pendingSettled(p, cards[id], now);
  }

  const sensors = useSensors(
    // A few pixels of travel before a drag starts, so a click still opens the pass.
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    // Press and hold on touch, so a swipe still scrolls the page.
    useSensor(TouchSensor, { activationConstraint: { delay: 250, tolerance: 6 } }),
  );

  function onDragStart({ active }: DragStartEvent) {
    justDragged.current = true;
    setDragId(String(active.id));
  }

  function endDrag() {
    setDragId(null);
    // The click that follows mouseup lands after this handler; let it pass, then reset.
    setTimeout(() => {
      justDragged.current = false;
    }, 0);
  }

  function onDragEnd({ active, over }: DragEndEvent) {
    endDrag();
    const card = cards[String(active.id)];
    if (!card) return;
    const to = (over?.data.current?.column as Column | undefined) ?? null;
    const outcome = dropOutcome(card, to);
    if (outcome === "refuse") say(REFUSE_MESSAGE, "info");
    else if (outcome !== "none") requestAction(card, outcome);
  }

  const announcements = useMemo<Announcements>(() => {
    const titleOf = (id: string | number) => cardsRef.current[String(id)]?.title ?? "the ticket";
    const stackOf = (column: unknown) =>
      typeof column === "string" && column in COLUMN_TITLES
        ? COLUMN_TITLES[column as Column]
        : null;
    return {
      onDragStart: ({ active }) => {
        const card = cardsRef.current[String(active.id)];
        const what = card && passAction(card) === "retry" ? "retry it" : "run it again";
        return `Picked up ${titleOf(active.id)}. Drop it on Todo to ${what}.`;
      },
      onDragOver: ({ over }) => {
        const stack = stackOf(over?.data.current?.column);
        return stack ? `Over ${stack}.` : "Not over a stack.";
      },
      onDragEnd: ({ active, over }) => {
        const stack = stackOf(over?.data.current?.column);
        return stack ? `Dropped ${titleOf(active.id)} on ${stack}.` : "Put back.";
      },
      onDragCancel: () => "Put back.",
    };
  }, []);

  const dragged = dragId ? (cards[dragId] ?? null) : null;
  const dropReady = dragged !== null && passAction(dragged) !== null;

  const groups = useMemo(() => groupByColumn(cards, repo || null, now), [cards, repo, now]);
  const labels = useMemo(() => repoLabels(Object.values(cards).map((c) => c.repo)), [cards]);
  const filterRepos = useMemo(
    () => [...new Set([...(repos ?? []).map((r) => r.full_name), ...repoOptions(cards)])].sort(),
    [repos, cards],
  );

  function isOpen(column: Column, id: string, index: number): boolean {
    const chosen = openIds[column];
    return chosen === undefined ? index === 0 : chosen === id;
  }

  function toggle(column: Column, id: string, index: number) {
    if (justDragged.current) return;
    setOpenIds((o) => ({ ...o, [column]: isOpen(column, id, index) ? "" : id }));
  }

  return (
    <div className="mx-auto max-w-[1600px] px-4 pb-16 sm:px-6">
      <header className="topbar">
        <h1 className="wordmark">nexTix</h1>
        <div className="topbar-tools">
          <label className="sr-only" htmlFor="repo-filter">
            Show tickets from
          </label>
          <select
            id="repo-filter"
            className="select"
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
          >
            <option value="">All repositories</option>
            {filterRepos.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          <Link href="/costs" className="topbar-link" title="Costs">
            <ChartColumn size={15} strokeWidth={2.25} aria-hidden />
            <span className="topbar-link-word">Costs</span>
          </Link>
          <ThemePicker />
          <ConnectionSignal state={connection} apiState={apiState} />
          <form action={logout}>
            <button type="submit" className="link-button">
              Sign out
            </button>
          </form>
        </div>
      </header>

      <Composer
        repos={repos}
        preferredRepo={repo}
        morphRef={morphRef}
        claudeAuth={claudeAuth}
        onCreated={onCreated}
      />

      {loadError ? (
        <p className="composer-note board-error" data-tone="error">
          Couldn&apos;t load tickets from the API. The board fills in as soon as it reconnects.
        </p>
      ) : null}

      {/* Always in the page, so screen readers hear what lands in it; empty, it takes no room. */}
      <p className="board-notice" role="status" data-tone={notice?.tone}>
        {notice ? (
          <>
            {notice.tone === "error" ? (
              <TriangleAlert size={15} strokeWidth={2.5} aria-hidden />
            ) : (
              <Info size={15} strokeWidth={2.5} aria-hidden />
            )}
            <span>{notice.text}</span>
          </>
        ) : null}
      </p>

      <DndContext
        id="board"
        sensors={sensors}
        collisionDetection={pointerWithin}
        accessibility={{ announcements, screenReaderInstructions: DRAG_INSTRUCTIONS }}
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        onDragCancel={endDrag}
      >
        <nav className="segments" aria-label="Ticket states">
          {COLUMNS.map(({ key, title }) => (
            <Segment
              key={key}
              column={key}
              title={title}
              count={groups[key].length}
              active={active === key}
              drop={dropState(key, dropReady)}
              onSelect={() => setActive(key)}
            />
          ))}
        </nav>

        <div className="stacks">
          {COLUMNS.map(({ key, title, empty }) => {
            const Icon = COLUMN_ICONS[key];
            const all = groups[key];
            const shown = key === "done" && !showAllDone ? all.slice(0, DONE_LIMIT) : all;
            const headingId = `stack-${key}`;
            return (
              <Stack
                key={key}
                column={key}
                headingId={headingId}
                active={active === key}
                drop={dropState(key, dropReady)}
              >
                <h2 className="stack-head" id={headingId}>
                  <Icon size={16} strokeWidth={2.5} aria-hidden />
                  {title}
                  <span className="stack-count tabular">{all.length}</span>
                </h2>
                {all.length === 0 ? (
                  <p className="stack-empty">{empty}</p>
                ) : (
                  <ol className="stack-list">
                    {shown.map((card, i) => (
                      <li key={card.id}>
                        <BoardPass
                          card={card}
                          now={now}
                          open={isOpen(key, card.id, i)}
                          fresh={fresh?.id === card.id}
                          transitionName={
                            fresh?.id === card.id && fresh.morph ? MORPH_NAME : `pass-${card.id}`
                          }
                          repoLabel={labels[card.repo] ?? card.repo}
                          pending={isPending(card.id)}
                          onToggle={() => toggle(key, card.id, i)}
                          onAction={requestAction}
                        />
                      </li>
                    ))}
                  </ol>
                )}
                {key === "done" && all.length > DONE_LIMIT ? (
                  <button
                    type="button"
                    className="stack-more"
                    onClick={() => setShowAllDone((v) => !v)}
                  >
                    {showAllDone ? "Show fewer" : `Show all ${all.length}`}
                  </button>
                ) : null}
              </Stack>
            );
          })}
        </div>

        {/* The copy under the pointer; no drop animation, so a pass that the board then
            moves travels with the view transition instead. */}
        <DragOverlay dropAnimation={null}>
          {dragged ? (
            <Pass
              card={dragged}
              now={now}
              open={false}
              fresh={false}
              transitionName="none"
              repoLabel={labels[dragged.repo] ?? dragged.repo}
              onToggle={() => undefined}
              ghost
            />
          ) : null}
        </DragOverlay>
      </DndContext>

      <ConfirmDialog
        open={confirming !== null}
        context={
          confirming ? (
            <>
              <span className="tabular">#{confirming.issue_number}</span> {confirming.title}
            </>
          ) : null
        }
        question={RERUN_QUESTION}
        confirmWord={ACTION_WORDS.rerun}
        dismissWord="Not now"
        onDismiss={() => setConfirming(null)}
        onConfirm={() => {
          const card = confirming;
          setConfirming(null);
          if (card) void runAction(card, "rerun");
        }}
      />
    </div>
  );
}

type DropState = "ready" | "closed" | undefined;

/** During a drag of a pass that has an action, Todo invites the drop; other stacks don't. */
function dropState(column: Column, dropReady: boolean): DropState {
  if (!dropReady) return undefined;
  return column === "todo" ? "ready" : "closed";
}

/** A stack, and a drop target: only Todo accepts, the rest explain why not. */
function Stack({
  column,
  headingId,
  active,
  drop,
  children,
}: {
  column: Column;
  headingId: string;
  active: boolean;
  drop: DropState;
  children: ReactNode;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: `stack-${column}`, data: { column } });
  return (
    <section
      ref={setNodeRef}
      aria-labelledby={headingId}
      data-active={active}
      data-drop={drop}
      data-over={(drop && isOver) || undefined}
      style={{ "--stack-hue": `var(--pass-${column}-mark)` } as CSSProperties}
    >
      {children}
    </section>
  );
}

/** A mobile segment; also a drop target, since only one stack shows at a time there. */
function Segment({
  column,
  title,
  count,
  active,
  drop,
  onSelect,
}: {
  column: Column;
  title: string;
  count: number;
  active: boolean;
  drop: DropState;
  onSelect: () => void;
}) {
  const Icon = COLUMN_ICONS[column];
  const { setNodeRef, isOver } = useDroppable({ id: `segment-${column}`, data: { column } });
  return (
    <button
      ref={setNodeRef}
      type="button"
      className="segment"
      aria-pressed={active}
      data-drop={drop}
      data-over={(drop && isOver) || undefined}
      onClick={onSelect}
      style={
        {
          "--seg-hue": `var(--pass-${column})`,
          "--seg-ink": `var(--pass-${column}-ink)`,
          "--seg-mark": `var(--pass-${column}-mark)`,
        } as CSSProperties
      }
    >
      <Icon size={15} strokeWidth={2.5} aria-hidden />
      <span className="segment-word">{title}</span>
      <span className="tabular">{count}</span>
    </button>
  );
}

/** A pass on the board: draggable onto Todo when it has an action, which the open pass also offers. */
function BoardPass({
  card,
  pending,
  onAction,
  ...rest
}: {
  card: TicketCard;
  now: number;
  open: boolean;
  fresh: boolean;
  transitionName: string;
  repoLabel: string;
  pending: boolean;
  onToggle: () => void;
  onAction: (card: TicketCard, action: PassAction) => void;
}) {
  const action = passAction(card);
  const { setNodeRef, listeners, isDragging } = useDraggable({
    id: card.id,
    disabled: action === null || pending,
  });
  return (
    <Pass
      {...rest}
      card={card}
      action={action}
      pending={pending}
      onAction={action ? () => onAction(card, action) : undefined}
      drag={
        action && !pending ? { setNodeRef, listeners, dragging: isDragging } : undefined
      }
    />
  );
}

function ConnectionSignal({ state, apiState }: { state: Connection; apiState: ApiState }) {
  // Each condition has its own shape as well as a color and a word, so it reads on phones
  // where the word is hidden: dot = live, spinning arrows = trying to connect, unplugged =
  // API down. "Connecting" and "Reconnecting" share the arrows on purpose: both mean the
  // board is still trying, and the word (or tooltip) says which.
  if (apiState !== "ok" && state !== "live") {
    const text = apiState === "unreachable" ? "API unreachable" : "API degraded";
    return (
      <span className="signal" style={{ color: "var(--danger)" }} title={text}>
        <Unplug size={14} strokeWidth={2.5} aria-hidden />
        <span className="signal-text">{text}</span>
      </span>
    );
  }
  if (state === "live") {
    return (
      <span className="signal" style={{ color: "var(--ok)" }} role="status" title="Live">
        <span className="live-dot" data-state="live" aria-hidden />
        <span className="signal-text" style={{ color: "var(--ink-2)" }}>
          Live
        </span>
      </span>
    );
  }
  const text = state === "offline" ? "Reconnecting…" : "Connecting…";
  return (
    <span
      className="signal"
      style={{ color: state === "offline" ? "var(--danger)" : "var(--ink-3)" }}
      role="status"
      title={text}
    >
      <RefreshCw size={14} strokeWidth={2.5} className="spin" aria-hidden />
      <span className="signal-text" style={{ color: "var(--ink-2)" }}>
        {text}
      </span>
    </span>
  );
}
