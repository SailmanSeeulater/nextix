"use client";

import {
  ArrowDown,
  Bot,
  ChevronRight,
  FilePen,
  FilePlus,
  FileText,
  FolderSearch,
  Globe,
  ListChecks,
  LoaderCircle,
  type LucideIcon,
  RefreshCw,
  SquareTerminal,
  TextSearch,
  TriangleAlert,
  Wrench,
} from "lucide-react";
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { runWord } from "@/lib/board";
import { loadRunHistory } from "@/lib/client-api";
import { isActiveStatus } from "@/lib/runs";
import {
  buildTranscript,
  formatToolInput,
  hasAgentOutput,
  lastEventId,
  latestUsage,
  mergeEvents,
  parseRunEvent,
  type ToolItem,
  type TranscriptItem,
  type Usage,
} from "@/lib/transcript";
import type { RunDetail, RunEvent } from "@/lib/types";

/** Event names the run stream uses for stored events (SSE event name = kind). */
const STREAM_KINDS = ["state", "log", "message", "tool_use", "tool_result", "usage", "error"];

/** How close to the bottom (px) still counts as "at the bottom" for auto-scroll. */
const BOTTOM_SLACK = 64;

type History = "loading" | "loaded" | "partial" | "failed";
type Connection = "connecting" | "live" | "reconnecting";

function isAtBottom(): boolean {
  const doc = document.documentElement;
  return window.innerHeight + window.scrollY >= doc.scrollHeight - BOTTOM_SLACK;
}

function prefersReducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function parseRun(data: string): RunDetail | null {
  try {
    const value = JSON.parse(data) as Partial<RunDetail> | null;
    return value && typeof value.id === "string" && typeof value.status === "string"
      ? (value as RunDetail)
      : null;
  } catch {
    return null;
  }
}

/**
 * The run's transcript: stored history first, then the live stream (which replays
 * stored events, sends `ready`, then live ones). Events merge by id, so a replay or
 * a reconnect never duplicates a line.
 */
export const Transcript = memo(function Transcript({
  runId,
  status,
  noWorker,
  onRunUpdated,
  onUsage,
  onStreamTrouble,
}: {
  runId: string;
  status: string;
  /** A queued run nobody has claimed for a long while. */
  noWorker: boolean;
  onRunUpdated: (run: RunDetail) => void;
  onUsage: (runId: string, usage: Usage) => void;
  /** The stream dropped; the parent re-reads the ticket in case the run ended meanwhile. */
  onStreamTrouble: () => void;
}) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [history, setHistory] = useState<History>("loading");
  const [connection, setConnection] = useState<Connection>("connecting");
  const [reloads, setReloads] = useState(0);
  const [atBottom, setAtBottom] = useState(true);
  const active = isActiveStatus(status);

  const eventsRef = useRef(events);
  const callbacks = useRef({ onRunUpdated, onUsage, onStreamTrouble });
  useEffect(() => {
    eventsRef.current = events;
    callbacks.current = { onRunUpdated, onUsage, onStreamTrouble };
  });

  // History, then (for an active run) the live stream. Re-runs when the run stops
  // being active: the stream closes and one last history read catches the tail.
  useEffect(() => {
    let cancelled = false;
    let source: EventSource | null = null;
    let pending: RunEvent[] = [];
    let timer: ReturnType<typeof setTimeout> | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    const flush = () => {
      timer = null;
      const batch = pending;
      pending = [];
      if (batch.length) setEvents((current) => mergeEvents(current, batch));
    };
    const enqueue = (event: RunEvent) => {
      pending.push(event);
      timer ??= setTimeout(flush, 50);
    };

    void (async () => {
      const result = await loadRunHistory(runId, lastEventId(eventsRef.current));
      if (cancelled) return;
      setEvents((current) => mergeEvents(current, result.events));
      setHistory((previous) =>
        result.complete
          ? "loaded"
          : result.events.length || (previous !== "loading" && previous !== "failed")
            ? "partial"
            : "failed",
      );
      if (!active) return;

      source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/stream`);
      const onEvent = (e: Event) => {
        const event = parseRunEvent((e as MessageEvent<string>).data);
        if (event) enqueue(event);
      };
      for (const kind of STREAM_KINDS) source.addEventListener(kind, onEvent);
      source.addEventListener("ready", () => setConnection("live"));
      source.addEventListener("run.updated", (e) => {
        const run = parseRun((e as MessageEvent<string>).data);
        // A terminal status flips `active`, which closes this stream (cleanup below).
        if (run && run.id === runId) callbacks.current.onRunUpdated(run);
      });
      // EventSource reconnects by itself and the replay is deduped. When the server
      // refuses outright (the source closes), start over in a few seconds.
      source.onerror = () => {
        setConnection("reconnecting");
        callbacks.current.onStreamTrouble();
        if (source?.readyState === EventSource.CLOSED && retry === null) {
          retry = setTimeout(() => setReloads((n) => n + 1), 5000);
        }
      };
    })();

    return () => {
      cancelled = true;
      source?.close();
      if (retry) clearTimeout(retry);
      if (timer) {
        clearTimeout(timer);
        flush();
      }
    };
  }, [runId, active, reloads]);

  const items = useMemo(() => buildTranscript(events), [events]);
  const usage = useMemo(() => latestUsage(events), [events]);

  useEffect(() => {
    if (usage) callbacks.current.onUsage(runId, usage);
  }, [runId, usage]);

  // Follow the bottom while the reader is there; stop when they scroll up.
  const followRef = useRef(false);
  const settledRef = useRef(false);
  const jumpingRef = useRef(false);

  useEffect(() => {
    let frame = 0;
    const measure = () => {
      frame = 0;
      const bottom = isAtBottom();
      if (bottom) jumpingRef.current = false;
      // Mid-jump scroll positions aren't the reader scrolling away.
      if (!jumpingRef.current) followRef.current = bottom;
      setAtBottom(bottom);
    };
    const onScroll = () => {
      frame ||= requestAnimationFrame(measure);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  useLayoutEffect(() => {
    if (history === "loading") return;
    if (!settledRef.current) {
      // First paint with history: follow only if it all fits (or the reader is already
      // at the bottom); never yank them past the header on load.
      settledRef.current = true;
      followRef.current = isAtBottom();
    } else if (followRef.current) {
      window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "instant" });
    }
    const frame = requestAnimationFrame(() => setAtBottom(isAtBottom()));
    return () => cancelAnimationFrame(frame);
  }, [events, history]);

  function jumpToLatest() {
    followRef.current = true;
    jumpingRef.current = true;
    window.scrollTo({
      top: document.documentElement.scrollHeight,
      behavior: prefersReducedMotion() ? "instant" : "smooth",
    });
    // If the reader interrupts the smooth scroll, let their position win again.
    setTimeout(() => {
      jumpingRef.current = false;
    }, 900);
  }

  function reload() {
    setHistory("loading");
    setReloads((n) => n + 1);
  }

  const loadedSomething = history !== "loading";
  const hasOutput = hasAgentOutput(items);

  return (
    <section className="transcript" aria-labelledby="transcript-title">
      <div className="transcript-bar">
        <h2 className="transcript-title" id="transcript-title">
          Transcript
        </h2>
        {active ? <StreamSignal connection={connection} /> : null}
      </div>

      {(history === "partial" || (history === "failed" && hasOutput)) && (
        <p className="transcript-note" data-tone="error" role="status">
          Part of this transcript couldn&apos;t be loaded.{" "}
          <button type="button" className="link-button" onClick={reload}>
            Try again
          </button>
        </p>
      )}

      {items.length > 0 ? (
        <ol className="transcript-list">
          {items.map((item) => (
            <Item key={item.id} item={item} active={active} />
          ))}
        </ol>
      ) : null}
      {hasOutput ? null : (
        <EmptyState history={history} status={status} noWorker={noWorker} onRetry={reload} />
      )}

      {loadedSomething && !atBottom && items.length > 0 ? (
        <button type="button" className="jump-latest" onClick={jumpToLatest}>
          <ArrowDown size={15} strokeWidth={2.5} aria-hidden />
          {active ? "Jump to latest" : "Jump to the end"}
        </button>
      ) : null}
    </section>
  );
});

function StreamSignal({ connection }: { connection: Connection }) {
  if (connection === "live") {
    return (
      <span className="signal transcript-signal" role="status">
        <span className="live-dot" data-state="live" aria-hidden />
        <span>Live</span>
      </span>
    );
  }
  return (
    <span className="signal transcript-signal" data-state={connection} role="status">
      <RefreshCw size={14} strokeWidth={2.5} className="spin" aria-hidden />
      <span>{connection === "reconnecting" ? "Reconnecting…" : "Connecting…"}</span>
    </span>
  );
}

function EmptyState({
  history,
  status,
  noWorker,
  onRetry,
}: {
  history: History;
  status: string;
  noWorker: boolean;
  onRetry: () => void;
}) {
  if (history === "loading") {
    return (
      <p className="transcript-empty">
        <LoaderCircle size={16} strokeWidth={2.5} className="spin" aria-hidden />
        Loading the transcript…
      </p>
    );
  }
  if (history === "failed") {
    return (
      <p className="transcript-empty" data-tone="error">
        <TriangleAlert size={16} strokeWidth={2.5} aria-hidden />
        Couldn&apos;t load this transcript.
        <button type="button" className="link-button" onClick={onRetry}>
          Try again
        </button>
      </p>
    );
  }
  if (status === "queued") {
    return noWorker ? (
      <p className="transcript-empty">
        <TriangleAlert size={16} strokeWidth={2.5} aria-hidden />
        No worker has picked this up for over ten minutes. Check that the nexTix worker is
        running.
      </p>
    ) : (
      <p className="transcript-empty">
        <LoaderCircle size={16} strokeWidth={2.5} className="spin" aria-hidden />
        Waiting for a worker…
      </p>
    );
  }
  if (isActiveStatus(status)) {
    return (
      <p className="transcript-empty">
        <LoaderCircle size={16} strokeWidth={2.5} className="spin" aria-hidden />
        Starting the agent…
      </p>
    );
  }
  return <p className="transcript-empty">The agent didn&apos;t record anything in this run.</p>;
}

/**
 * Items are rebuilt from the events on every batch, but an item only changes when its
 * tool result arrives, so rows re-render only then (or when the run stops).
 */
const Item = memo(
  function Item({ item, active }: { item: TranscriptItem; active: boolean }) {
    return <ItemBody item={item} active={active} />;
  },
  (a, b) =>
    a.active === b.active &&
    a.item.id === b.item.id &&
    a.item.type === b.item.type &&
    (a.item.type !== "tool" || b.item.type !== "tool" || a.item.result?.id === b.item.result?.id),
);

function ItemBody({ item, active }: { item: TranscriptItem; active: boolean }) {
  switch (item.type) {
    case "message":
      return (
        <li className="t-message">
          {item.paragraphs.map((p, i) => (
            <p key={i}>{p}</p>
          ))}
        </li>
      );
    case "tool":
      return <ToolRow item={item} active={active} />;
    case "log":
      return (
        <li className="t-log">
          <ChevronRight size={13} strokeWidth={2.5} aria-hidden />
          <span>{item.text}</span>
        </li>
      );
    case "error":
      return (
        <li className="t-error">
          <TriangleAlert size={16} strokeWidth={2.5} aria-hidden />
          <p>{item.text}</p>
        </li>
      );
    case "state":
      return (
        <li className="t-state">
          <span>{runWord(item.status)}</span>
          {item.ts ? (
            <time className="tabular" dateTime={item.ts}>
              {formatClock(item.ts)}
            </time>
          ) : null}
        </li>
      );
  }
}

function formatClock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

const TOOL_ICONS: Record<string, LucideIcon> = {
  Bash: SquareTerminal,
  Read: FileText,
  Write: FilePlus,
  Edit: FilePen,
  MultiEdit: FilePen,
  NotebookEdit: FilePen,
  Glob: FolderSearch,
  Grep: TextSearch,
  WebFetch: Globe,
  WebSearch: Globe,
  TodoWrite: ListChecks,
  Task: Bot,
  Agent: Bot,
};

/** A tool call and its result in one collapsible row. The body renders only when open. */
function ToolRow({ item, active }: { item: ToolItem; active: boolean }) {
  const [open, setOpen] = useState(false);
  const Icon = TOOL_ICONS[item.name] ?? Wrench;
  const result = item.result;
  const pending = result === null && active;
  const input = formatToolInput(item.input);

  return (
    <li className="t-tool" data-error={result?.isError || undefined}>
      <details open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
        <summary>
          <ChevronRight size={14} strokeWidth={2.5} className="t-chevron" aria-hidden />
          <Icon size={15} strokeWidth={2.25} className="t-tool-icon" aria-hidden />
          <span className="t-tool-name">{item.name}</span>
          {item.summary ? <span className="t-tool-summary">{item.summary}</span> : null}
          <span className="t-tool-status">
            {pending ? (
              <>
                <LoaderCircle size={13} strokeWidth={2.5} className="spin" aria-hidden />
                <span className="sr-only">Running</span>
              </>
            ) : result?.isError ? (
              <>
                <TriangleAlert size={13} strokeWidth={2.5} aria-hidden />
                Error
              </>
            ) : null}
          </span>
        </summary>
        {open ? (
          <div className="t-tool-body">
            {input ? (
              <>
                <p className="t-raw-label">Input</p>
                <pre className="t-raw">{input}</pre>
              </>
            ) : null}
            {result ? (
              <>
                <p className="t-raw-label">{result.isError ? "Error" : "Output"}</p>
                <pre className="t-raw" data-error={result.isError || undefined}>
                  {result.content || "(no output)"}
                </pre>
              </>
            ) : (
              <p className="t-raw-empty">
                {active ? "Waiting for the result…" : "No result was recorded."}
              </p>
            )}
          </div>
        ) : null}
      </details>
    </li>
  );
}
