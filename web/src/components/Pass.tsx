"use client";

import type { DraggableSyntheticListeners } from "@dnd-kit/core";
import {
  ArrowRight,
  ArrowUpRight,
  Clock,
  FlaskConical,
  LoaderCircle,
  RotateCcw,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import type { CSSProperties } from "react";
import { COLUMNS, headerField, liveness, passFields } from "@/lib/board";
import { ACTION_WORDS, type PassAction } from "@/lib/drag";
import type { TicketCard } from "@/lib/types";
import { COLUMN_ICONS } from "./columnIcons";

const COLUMN_TITLES = Object.fromEntries(COLUMNS.map((c) => [c.key, c.title]));

export function Pass({
  card,
  open,
  now,
  fresh,
  transitionName,
  repoLabel,
  onToggle,
  action = null,
  pending = false,
  onAction,
  drag,
  ghost = false,
}: {
  card: TicketCard;
  /** Short repo name for the strip; the full name stays available to assistive tech. */
  repoLabel: string;
  open: boolean;
  now: number;
  fresh: boolean;
  transitionName: string;
  onToggle: () => void;
  /** Retry or Run again, offered on the open pass (the keyboard way to do what a drag does). */
  action?: PassAction | null;
  /** An action was sent and the board hasn't moved the pass yet. */
  pending?: boolean;
  onAction?: () => void;
  /** Set when the pass can be dragged onto Todo. */
  drag?: {
    setNodeRef: (el: HTMLElement | null) => void;
    listeners: DraggableSyntheticListeners;
    dragging: boolean;
  };
  /** The copy that follows the pointer during a drag: hidden from assistive tech, no ids. */
  ghost?: boolean;
}) {
  const Icon = COLUMN_ICONS[card.column];
  const live = liveness(card, now);
  const stalled = live?.state === "stalled";
  const bodyId = `pass-body-${card.id}`;
  const style = {
    // A stalled pass goes hollow: a Doing outline with nothing inside, the agent gone quiet.
    "--pass-bg": stalled ? "var(--surface)" : `var(--pass-${card.column})`,
    "--pass-ink": stalled ? "var(--ink)" : `var(--pass-${card.column}-ink)`,
    viewTransitionName: transitionName,
  } as CSSProperties;

  return (
    <article
      ref={drag?.setNodeRef}
      {...drag?.listeners}
      className="pass"
      data-column={card.column}
      data-open={open}
      data-stale={stalled || undefined}
      data-fresh={fresh || undefined}
      data-draggable={drag ? true : undefined}
      data-dragging={drag?.dragging || undefined}
      data-ghost={ghost || undefined}
      aria-hidden={ghost || undefined}
      style={style}
    >
      <button
        type="button"
        className="pass-toggle"
        aria-expanded={open}
        aria-controls={ghost ? undefined : bodyId}
        tabIndex={ghost ? -1 : undefined}
        onClick={onToggle}
      >
        <span className="pass-strip">
          <Icon size={13} strokeWidth={2.5} aria-hidden />
          <span className="pass-repo" title={card.repo}>
            <span aria-hidden>{repoLabel}</span>
            <span className="sr-only">{card.repo}</span>
          </span>
          <span className="pass-header-field">{headerField(card)}</span>
        </span>
        <span className="pass-title">{card.title}</span>
        <span className="sr-only">{`, ${COLUMN_TITLES[card.column]}`}</span>
        {live ? <Readout live={live} /> : null}
        {card.tests_failing ? (
          <span className="pass-flag">
            <FlaskConical size={13} strokeWidth={2.5} aria-hidden />
            <span className="sr-only">, </span>
            Tests failing
          </span>
        ) : null}
        {pending ? (
          <span className="pass-flag">
            <LoaderCircle size={13} strokeWidth={2.5} className="spin" aria-hidden />
            <span className="sr-only">, </span>
            Queuing…
          </span>
        ) : null}
      </button>

      <div className="pass-body" id={ghost ? undefined : bodyId} inert={!open || ghost}>
        <div>
          <div className="notch-cut" aria-hidden>
            <span />
          </div>
          <dl className="pass-fields">
            {passFields(card, now).map((f) => (
              <div className="pass-field" key={f.label} data-tone={f.tone}>
                <dt>{f.label}</dt>
                <dd>
                  {f.tone === "alert" ? <TriangleAlert size={13} strokeWidth={2.5} aria-hidden /> : null}
                  {f.value}
                </dd>
              </div>
            ))}
          </dl>
          <div className="pass-links">
            <Link href={`/tickets/${card.id}`} prefetch={false}>
              Details
              <ArrowRight size={14} strokeWidth={2.5} aria-hidden />
            </Link>
            <a href={card.issue_url} target="_blank" rel="noreferrer">
              Issue #{card.issue_number}
              <ArrowUpRight size={14} strokeWidth={2.5} aria-hidden />
            </a>
            {card.pr_url ? (
              <a href={card.pr_url} target="_blank" rel="noreferrer">
                Pull request #{card.pr_number}
                <ArrowUpRight size={14} strokeWidth={2.5} aria-hidden />
              </a>
            ) : null}
            {action && onAction ? (
              <button
                type="button"
                className="pass-action"
                onClick={onAction}
                disabled={pending}
                aria-busy={pending || undefined}
              >
                {pending ? (
                  <LoaderCircle size={14} strokeWidth={2.5} className="spin" aria-hidden />
                ) : (
                  <RotateCcw size={14} strokeWidth={2.5} aria-hidden />
                )}
                {pending ? "Queuing…" : ACTION_WORDS[action]}
              </button>
            ) : null}
          </div>
        </div>
      </div>
    </article>
  );
}

/** The Doing pass's primary field: large enough to read from across the stack. */
function Readout({ live }: { live: NonNullable<ReturnType<typeof liveness>> }) {
  if (live.state === "stalled") {
    return (
      <span className="pass-readout" data-state="stalled">
        <TriangleAlert size={16} strokeWidth={2.5} aria-hidden />
        <span className="pass-readout-main">No heartbeat</span>
        <span className="pass-readout-sub tabular">
          {live.quietFor ? `quiet for ${live.quietFor}` : "none received yet"}
        </span>
      </span>
    );
  }
  if (live.state === "queued") {
    return (
      <span className="pass-readout" data-state="queued">
        <Clock size={15} strokeWidth={2.5} aria-hidden />
        <span className="pass-readout-sub">Waiting for a worker</span>
      </span>
    );
  }
  return (
    <span className="pass-readout" data-state="live">
      <span className="live-dot" data-state="live" aria-hidden />
      <span className="pass-readout-main tabular">{live.elapsed}</span>
      <span className="pass-readout-sub">running</span>
    </span>
  );
}
