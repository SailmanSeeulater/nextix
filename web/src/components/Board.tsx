"use client";

import { useEffect, useMemo, useState } from "react";
import {
  COLUMNS,
  applyEvent,
  formatCost,
  formatElapsed,
  groupByColumn,
  indexCards,
  isHeartbeatStale,
  repoOptions,
  statusLabel,
  type CardIndex,
} from "@/lib/board";
import type { Column, TicketCard } from "@/lib/types";

type Connection = "connecting" | "live" | "offline";

export function Board({ initial }: { initial: TicketCard[] }) {
  const [cards, setCards] = useState<CardIndex>(() => indexCards(initial));
  const [repo, setRepo] = useState<string>("");
  const [connection, setConnection] = useState<Connection>("connecting");
  const now = useNow(1000);

  useEffect(() => {
    const source = new EventSource("/api/stream");

    // `ready` fires on every (re)connect once the server is subscribed. Reload the
    // full board then, so anything published while we were disconnected is caught.
    source.addEventListener("ready", async () => {
      setConnection("live");
      try {
        const res = await fetch("/api/tickets", { cache: "no-store" });
        if (res.ok) setCards(indexCards((await res.json()) as TicketCard[]));
      } catch {
        // keep current state; the next reconnect retries
      }
    });
    source.addEventListener("ticket.updated", (e) => {
      const data = JSON.parse((e as MessageEvent<string>).data) as TicketCard;
      setCards((c) => applyEvent(c, { type: "ticket.updated", data }));
    });
    source.addEventListener("ticket.removed", (e) => {
      const data = JSON.parse((e as MessageEvent<string>).data) as { id: string };
      setCards((c) => applyEvent(c, { type: "ticket.removed", data }));
    });
    source.onerror = () => setConnection("offline"); // EventSource reconnects on its own
    return () => source.close();
  }, []);

  const repos = useMemo(() => repoOptions(cards), [cards]);
  const groups = useMemo(() => groupByColumn(cards, repo || null), [cards, repo]);

  return (
    <div>
      <div className="mb-4 flex items-center gap-3 text-sm">
        <label className="flex items-center gap-2">
          <span className="text-neutral-500">Repo</span>
          <select
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            className="rounded-md border border-neutral-300 bg-transparent px-2 py-1 dark:border-neutral-700"
          >
            <option value="">All repos</option>
            {repos.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <ConnectionBadge state={connection} />
      </div>

      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {COLUMNS.map(({ key, title }) => (
          <div
            key={key}
            data-column={key}
            className="flex min-h-40 flex-col rounded-lg border border-neutral-200 bg-neutral-100/60 p-3 dark:border-neutral-800 dark:bg-neutral-900/60"
          >
            <h2 className="mb-2 flex items-center justify-between text-sm font-medium text-neutral-600 dark:text-neutral-300">
              {title}
              <span className="text-xs text-neutral-400">{groups[key].length}</span>
            </h2>
            <div className="flex flex-col gap-2">
              {groups[key].map((card) => (
                <Card key={card.id} card={card} now={now} />
              ))}
              {groups[key].length === 0 ? (
                <p className="text-xs text-neutral-400">No tickets</p>
              ) : null}
            </div>
          </div>
        ))}
      </section>
    </div>
  );
}

const BADGE: Record<Column, string> = {
  todo: "bg-neutral-200 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300",
  doing: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
  needs_input: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  failed: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  in_review: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300",
  done: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
};

function Card({ card, now }: { card: TicketCard; now: number }) {
  const run = card.latest_run;
  const labels = card.labels.filter((l) => l !== "nextix");
  return (
    <article className="rounded-md border border-neutral-200 bg-white p-3 text-sm shadow-sm dark:border-neutral-800 dark:bg-neutral-950">
      <div className="mb-1 flex items-center justify-between gap-2 text-xs text-neutral-500">
        <span className="truncate">{card.repo}</span>
        <a href={card.issue_url} target="_blank" rel="noreferrer" className="hover:underline">
          #{card.issue_number}
        </a>
      </div>
      <a
        href={card.issue_url}
        target="_blank"
        rel="noreferrer"
        className="mb-2 block font-medium leading-snug hover:underline"
      >
        {card.title}
      </a>
      {labels.length > 0 ? (
        <div className="mb-2 flex flex-wrap gap-1">
          {labels.map((l) => (
            <span
              key={l}
              className="rounded bg-neutral-100 px-1.5 py-0.5 text-[11px] text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300"
            >
              {l}
            </span>
          ))}
        </div>
      ) : null}
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className={`rounded px-1.5 py-0.5 ${BADGE[card.column]}`}>{statusLabel(card)}</span>
        {card.pr_url ? (
          <a href={card.pr_url} target="_blank" rel="noreferrer" className="text-neutral-500 hover:underline">
            PR #{card.pr_number}
          </a>
        ) : null}
      </div>
      {card.column === "doing" && run ? (
        <div className="mt-2 flex items-center gap-2 text-xs text-neutral-500">
          <LiveDot stale={isHeartbeatStale(run, now)} />
          <span>{run.agent_id ?? "unassigned"}</span>
          {run.started_at ? <span>· {formatElapsed(run.started_at, now)}</span> : null}
          <span className="ml-auto">{formatCost(run.cost_usd)}</span>
        </div>
      ) : run && run.cost_usd > 0 ? (
        <div className="mt-2 text-right text-xs text-neutral-400">{formatCost(run.cost_usd)}</div>
      ) : null}
    </article>
  );
}

function LiveDot({ stale }: { stale: boolean }) {
  return (
    <span className="relative flex h-2 w-2" title={stale ? "no heartbeat in 30s" : "live"}>
      {!stale ? (
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
      ) : null}
      <span
        className={`relative inline-flex h-2 w-2 rounded-full ${stale ? "bg-neutral-400" : "bg-emerald-500"}`}
      />
    </span>
  );
}

function ConnectionBadge({ state }: { state: Connection }) {
  const style =
    state === "live" ? "text-emerald-600" : state === "offline" ? "text-red-600" : "text-neutral-500";
  const text = state === "live" ? "● live" : state === "offline" ? "● reconnecting…" : "○ connecting…";
  return <span className={`text-xs ${style}`}>{text}</span>;
}

function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
