"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { RefreshCw, Unplug } from "lucide-react";
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
import { loadTickets } from "@/lib/client-api";
import type { BoardEvent, Column, CreateTicketResult, RepoOption, TicketCard } from "@/lib/types";
import { COLUMN_ICONS } from "./columnIcons";
import { Composer } from "./Composer";
import { Pass } from "./Pass";
import { ThemePicker } from "./ThemePicker";
import { useNow } from "./useNow";

type Connection = "connecting" | "live" | "offline";
type ApiState = "ok" | "error" | "unreachable";

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

      <nav className="segments" aria-label="Ticket states">
        {COLUMNS.map(({ key, title }) => {
          const Icon = COLUMN_ICONS[key];
          return (
            <button
              key={key}
              type="button"
              className="segment"
              aria-pressed={active === key}
              onClick={() => setActive(key)}
              style={
                {
                  "--seg-hue": `var(--pass-${key})`,
                  "--seg-ink": `var(--pass-${key}-ink)`,
                  "--seg-mark": `var(--pass-${key}-mark)`,
                } as CSSProperties
              }
            >
              <Icon size={15} strokeWidth={2.5} aria-hidden />
              <span className="segment-word">{title}</span>
              <span className="tabular">{groups[key].length}</span>
            </button>
          );
        })}
      </nav>

      <div className="stacks">
        {COLUMNS.map(({ key, title, empty }) => {
          const Icon = COLUMN_ICONS[key];
          const all = groups[key];
          const shown = key === "done" && !showAllDone ? all.slice(0, DONE_LIMIT) : all;
          const headingId = `stack-${key}`;
          return (
            <section
              key={key}
              aria-labelledby={headingId}
              data-active={active === key}
              style={{ "--stack-hue": `var(--pass-${key}-mark)` } as CSSProperties}
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
                      <Pass
                        card={card}
                        now={now}
                        open={isOpen(key, card.id, i)}
                        fresh={fresh?.id === card.id}
                        transitionName={
                          fresh?.id === card.id && fresh.morph ? MORPH_NAME : `pass-${card.id}`
                        }
                        repoLabel={labels[card.repo] ?? card.repo}
                        onToggle={() => toggle(key, card.id, i)}
                      />
                    </li>
                  ))}
                </ol>
              )}
              {key === "done" && all.length > DONE_LIMIT ? (
                <button type="button" className="stack-more" onClick={() => setShowAllDone((v) => !v)}>
                  {showAllDone ? "Show fewer" : `Show all ${all.length}`}
                </button>
              ) : null}
            </section>
          );
        })}
      </div>
    </div>
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
