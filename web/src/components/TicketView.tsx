"use client";

import {
  ArrowLeft,
  ArrowUpRight,
  ChevronRight,
  Clock,
  LoaderCircle,
  RotateCcw,
  Square,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { COLUMNS, HEARTBEAT_RECHECK_INTERVAL_MS, heartbeatNeedsRecheck } from "@/lib/board";
import { ApiError, cancelRun, loadTicketDetail, retryTicket } from "@/lib/client-api";
import {
  activeRun,
  applyUsage,
  attemptLabel,
  canRetry,
  mergeCard,
  mergeRunSummary,
  mergeRuns,
  noWorkerAvailable,
  runFields,
  runReadout,
  sortRuns,
  upsertRun,
  type RunReadout,
} from "@/lib/runs";
import { toParagraphs, type Usage } from "@/lib/transcript";
import type { Column, RunDetail, RunStateEvent, TicketCard, TicketDetail } from "@/lib/types";
import { COLUMN_ICONS } from "./columnIcons";
import { ThemePicker } from "./ThemePicker";
import { Transcript } from "./Transcript";
import { useNow } from "./useNow";

const COLUMN_TITLES = Object.fromEntries(COLUMNS.map((c) => [c.key, c.title])) as Record<
  Column,
  string
>;

function parseData<T>(e: Event): T | null {
  try {
    return JSON.parse((e as MessageEvent<string>).data) as T;
  } catch {
    return null;
  }
}

function describe(err: unknown): string {
  return err instanceof ApiError ? err.message : "Something went wrong. Try again.";
}

/**
 * One ticket: a large pass in its state's color, the run actions, and the selected
 * run's transcript. Live changes arrive on the board stream (ticket.updated, run.state)
 * and the run stream (run.updated, usage).
 */
export function TicketView({ initial, renderedAt }: { initial: TicketDetail; renderedAt: number }) {
  const ticketId = initial.id;
  const [detail, setDetail] = useState<TicketDetail>(() => ({
    ...initial,
    runs: sortRuns(initial.runs),
  }));
  /** null follows the newest run, so a retry switches to it. */
  const [chosenRunId, setChosenRunId] = useState<string | null>(null);
  const [busy, setBusy] = useState<"retry" | "cancel" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const [stoppingRunId, setStoppingRunId] = useState<string | null>(null);
  const [removed, setRemoved] = useState(false);
  const now = useNow(renderedAt, 1000);

  const detailRef = useRef(detail);
  useEffect(() => {
    detailRef.current = detail;
  }, [detail]);

  // Re-read the ticket, coalescing bursts: after an action, on (re)connect, when a
  // run ends (for the new column and PR link), or when a run we don't know appears.
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const refresh = useCallback(() => {
    if (refreshTimer.current) return;
    refreshTimer.current = setTimeout(async () => {
      refreshTimer.current = null;
      const fresh = await loadTicketDetail(ticketId);
      if (fresh) setDetail((d) => ({ ...fresh, runs: mergeRuns(d.runs, fresh.runs) }));
    }, 300);
  }, [ticketId]);

  useEffect(() => {
    const timer = refreshTimer;
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  useEffect(() => {
    const source = new EventSource("/api/stream");
    // `ready` fires on every (re)connect: re-read so nothing missed while away is lost.
    source.addEventListener("ready", refresh);
    source.addEventListener("ticket.updated", (e) => {
      const card = parseData<TicketCard>(e);
      if (!card || card.id !== ticketId) return;
      const summary = card.latest_run;
      if (summary && mergeRunSummary(detailRef.current.runs, summary) === null) refresh();
      setDetail((d) => {
        const next = mergeCard(d, card);
        const runs = summary ? mergeRunSummary(d.runs, summary) : null;
        return runs ? { ...next, runs } : next;
      });
    });
    source.addEventListener("ticket.removed", (e) => {
      if (parseData<{ id: string }>(e)?.id === ticketId) setRemoved(true);
    });
    source.addEventListener("run.state", (e) => {
      const data = parseData<RunStateEvent>(e);
      if (!data?.run || data.ticket_id !== ticketId) return;
      setDetail((d) => ({ ...d, runs: upsertRun(d.runs, data.run) }));
    });
    return () => source.close();
  }, [ticketId, refresh]);

  const onRunUpdated = useCallback(
    (run: RunDetail) => {
      const prev = detailRef.current.runs.find((r) => r.id === run.id);
      setDetail((d) => ({ ...d, runs: upsertRun(d.runs, run) }));
      if (!prev || prev.status !== run.status) refresh();
    },
    [refresh],
  );

  const onUsage = useCallback((runId: string, usage: Usage) => {
    setDetail((d) => {
      let changed = false;
      const runs = d.runs.map((r) => {
        if (r.id !== runId) return r;
        const next = applyUsage(r, usage);
        changed ||= next !== r;
        return next;
      });
      return changed ? { ...d, runs } : d;
    });
  }, []);

  const runs = detail.runs;
  const selected = (chosenRunId && runs.find((r) => r.id === chosenRunId)) || runs[0] || null;
  const active = activeRun(runs);

  // Heartbeats aren't pushed (only status and usage changes are), so re-read the ticket
  // before a live run's header would say its heartbeat was lost.
  const lastRecheck = useRef(0);
  useEffect(() => {
    if (!heartbeatNeedsRecheck(active, now)) return;
    if (now - lastRecheck.current < HEARTBEAT_RECHECK_INTERVAL_MS) return;
    lastRecheck.current = now;
    refresh();
  }, [active, now, refresh]);
  const retryable = !removed && canRetry(detail.column, runs);
  const readout = runReadout(selected, now);
  const stalled = readout?.state === "stalled";
  const Icon = COLUMN_ICONS[detail.column];

  async function retry() {
    setBusy("retry");
    setError(null);
    try {
      const run = await retryTicket(ticketId);
      setDetail((d) => ({ ...d, runs: upsertRun(d.runs, run) }));
      setChosenRunId(null);
    } catch (err) {
      setError(describe(err));
    } finally {
      setBusy(null);
      refresh();
    }
  }

  async function cancel(run: RunDetail) {
    setBusy("cancel");
    setError(null);
    try {
      const updated = await cancelRun(run.id);
      if (updated) setDetail((d) => ({ ...d, runs: upsertRun(d.runs, updated) }));
      setStoppingRunId(run.id);
      setConfirmingCancel(false);
    } catch (err) {
      setError(describe(err));
    } finally {
      setBusy(null);
      refresh();
    }
  }

  const style = {
    // A stalled run goes hollow, as on the board: the agent has gone quiet.
    "--pass-bg": stalled ? "var(--surface)" : `var(--pass-${detail.column})`,
    "--pass-ink": stalled ? "var(--ink)" : `var(--pass-${detail.column}-ink)`,
  } as CSSProperties;

  return (
    <div className="detail">
      <header className="detail-top">
        <Link href="/" className="back-link">
          <ArrowLeft size={16} strokeWidth={2.5} aria-hidden />
          Board
        </Link>
        <ThemePicker />
      </header>

      <article
        className="pass detail-pass"
        data-column={detail.column}
        data-stale={stalled || undefined}
        style={style}
        aria-labelledby="ticket-title"
      >
        <div className="detail-pass-head">
          <p className="pass-strip">
            <Icon size={14} strokeWidth={2.5} aria-hidden />
            <span className="detail-state">{COLUMN_TITLES[detail.column]}</span>
            <span className="pass-repo" title={detail.repo}>
              {detail.repo}
            </span>
            <span className="pass-header-field">#{detail.issue_number}</span>
          </p>
          <h1 className="detail-title" id="ticket-title">
            {detail.title}
          </h1>
          {readout ? <HeaderReadout readout={readout} /> : null}
        </div>

        <div className="notch-cut" aria-hidden>
          <span />
        </div>

        <dl className="pass-fields">
          {runFields(detail, selected, now).map((f) => (
            <div className="pass-field" key={f.label} data-tone={f.tone}>
              <dt>{f.label}</dt>
              <dd>
                {f.tone === "alert" ? (
                  <TriangleAlert size={13} strokeWidth={2.5} aria-hidden />
                ) : null}
                {f.value}
              </dd>
              {f.detail ? <dd className="pass-field-detail">{f.detail}</dd> : null}
            </div>
          ))}
        </dl>

        {selected?.question ? (
          <div className="detail-pass-question">
            <p className="detail-pass-question-label">The agent asked</p>
            <p className="detail-pass-question-text">{selected.question}</p>
          </div>
        ) : null}
        {detail.column === "needs_input" ? (
          <p className="detail-pass-note">
            {selected?.question
              ? "Answer on the GitHub issue, then retry."
              : "The agent asked a question on the GitHub issue. Answer it there, then retry."}
          </p>
        ) : null}

        <div className="pass-links">
          <a href={detail.issue_url} target="_blank" rel="noreferrer">
            Issue #{detail.issue_number}
            <ArrowUpRight size={14} strokeWidth={2.5} aria-hidden />
          </a>
          {detail.pr_url ? (
            <a href={detail.pr_url} target="_blank" rel="noreferrer">
              Pull request #{detail.pr_number}
              <ArrowUpRight size={14} strokeWidth={2.5} aria-hidden />
            </a>
          ) : null}
        </div>
      </article>

      <div className="detail-actions">
        {runs.length > 1 && selected ? (
          <label className="attempt-picker">
            <span className="sr-only">Run attempt</span>
            <select
              className="select"
              value={selected.id}
              onChange={(e) =>
                setChosenRunId(e.target.value === runs[0]?.id ? null : e.target.value)
              }
            >
              {runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {attemptLabel(r)}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        <div className="detail-buttons">
          {active ? (
            <CancelControl
              run={active}
              stopping={stoppingRunId === active.id}
              confirming={confirmingCancel}
              busy={busy === "cancel"}
              onAsk={() => {
                setError(null);
                setConfirmingCancel(true);
              }}
              onDismiss={() => setConfirmingCancel(false)}
              onConfirm={() => void cancel(active)}
            />
          ) : null}
          {retryable ? (
            <button
              type="button"
              className="button"
              onClick={() => void retry()}
              disabled={busy !== null}
              aria-busy={busy === "retry" || undefined}
            >
              {busy === "retry" ? (
                <LoaderCircle size={16} className="spin" aria-hidden />
              ) : (
                <RotateCcw size={16} strokeWidth={2.5} aria-hidden />
              )}
              {busy === "retry" ? "Queuing…" : runs.length ? "Retry" : "Start run"}
            </button>
          ) : null}
          {detail.pr_url ? (
            <a
              className="button"
              data-variant={retryable ? "secondary" : undefined}
              href={detail.pr_url}
              target="_blank"
              rel="noreferrer"
            >
              Open PR on GitHub
              <ArrowUpRight size={16} strokeWidth={2.5} aria-hidden />
            </a>
          ) : null}
        </div>
      </div>

      <div aria-live="polite">
        {error ? (
          <p className="detail-note" data-tone="error">
            {error}
          </p>
        ) : null}
        {removed ? (
          <p className="detail-note">This ticket was removed from the board.</p>
        ) : null}
      </div>

      {detail.body ? <IssueBody body={detail.body} /> : null}

      {selected ? (
        <Transcript
          key={selected.id}
          runId={selected.id}
          status={selected.status}
          noWorker={noWorkerAvailable(selected, now)}
          onRunUpdated={onRunUpdated}
          onUsage={onUsage}
          onStreamTrouble={refresh}
        />
      ) : (
        <section className="transcript" aria-labelledby="transcript-title">
          <div className="transcript-bar">
            <h2 className="transcript-title" id="transcript-title">
              Transcript
            </h2>
          </div>
          <p className="transcript-empty">
            No agent has worked on this ticket yet.
            {retryable ? " Start a run to hand it to one." : ""}
          </p>
        </section>
      )}
    </div>
  );
}

/** The header's primary field while a run is active, as on a Doing pass. */
function HeaderReadout({ readout }: { readout: RunReadout }) {
  if (readout.state === "stalled") {
    return (
      <p className="pass-readout" data-state="stalled">
        <TriangleAlert size={16} strokeWidth={2.5} aria-hidden />
        <span className="pass-readout-main">No heartbeat</span>
        <span className="pass-readout-sub tabular">
          {readout.quietFor ? `quiet for ${readout.quietFor}` : "none received yet"}
        </span>
      </p>
    );
  }
  if (readout.state === "queued") {
    return (
      <p className="pass-readout" data-state="queued">
        {readout.noWorker ? (
          <TriangleAlert size={15} strokeWidth={2.5} aria-hidden />
        ) : (
          <Clock size={15} strokeWidth={2.5} aria-hidden />
        )}
        <span className="pass-readout-sub">
          {readout.noWorker ? "No worker available" : "Waiting for a worker"}
        </span>
      </p>
    );
  }
  return (
    <p className="pass-readout" data-state="live">
      <span className="live-dot" data-state="live" aria-hidden />
      <span className="pass-readout-main tabular">{readout.elapsed}</span>
      <span className="pass-readout-sub">running</span>
    </p>
  );
}

function CancelControl({
  run,
  stopping,
  confirming,
  busy,
  onAsk,
  onConfirm,
  onDismiss,
}: {
  run: RunDetail;
  stopping: boolean;
  confirming: boolean;
  busy: boolean;
  onAsk: () => void;
  onConfirm: () => void;
  onDismiss: () => void;
}) {
  if (stopping) {
    return (
      <button type="button" className="button" data-variant="secondary" disabled aria-busy>
        <LoaderCircle size={16} className="spin" aria-hidden />
        Stopping…
      </button>
    );
  }
  if (confirming) {
    return (
      <span className="cancel-confirm" role="group" aria-label="Confirm cancel">
        <span className="cancel-confirm-text">
          Stop {run.agent_id ?? "this run"}? You can retry afterwards.
        </span>
        <button
          type="button"
          className="button"
          data-variant="secondary"
          onClick={onConfirm}
          disabled={busy}
          aria-busy={busy || undefined}
        >
          {busy ? (
            <LoaderCircle size={16} className="spin" aria-hidden />
          ) : (
            <Square size={14} strokeWidth={2.5} aria-hidden />
          )}
          Stop run
        </button>
        <button type="button" className="link-button" onClick={onDismiss} disabled={busy}>
          Keep running
        </button>
      </span>
    );
  }
  return (
    <button type="button" className="button" data-variant="secondary" onClick={onAsk}>
      <Square size={14} strokeWidth={2.5} aria-hidden />
      Cancel run
    </button>
  );
}

/** The GitHub issue's text, folded away: the transcript is the page's subject. */
function IssueBody({ body }: { body: string }) {
  const paragraphs = toParagraphs(body);
  if (paragraphs.length === 0) return null;
  return (
    <details className="issue-body">
      <summary>
        <ChevronRight size={14} strokeWidth={2.5} className="t-chevron" aria-hidden />
        Issue description
      </summary>
      <div className="issue-body-text">
        {paragraphs.map((p, i) => (
          <p key={i}>{p}</p>
        ))}
      </div>
    </details>
  );
}
