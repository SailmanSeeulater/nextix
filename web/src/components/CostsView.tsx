"use client";

import { ArrowLeft, ChartColumn, Info, LoaderCircle, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useId, useState } from "react";
import { ApiError, fetchCosts } from "@/lib/client-api";
import {
  COST_WINDOWS,
  axisLabelIndexes,
  chartScale,
  chartSummary,
  costsHref,
  dayRange,
  formatAxisUsd,
  formatDay,
  formatTokens,
  formatUsd,
  parseDays,
  runsWord,
  type CostWindow,
} from "@/lib/costs";
import { formatCount } from "@/lib/runs";
import type { CostDay, CostReport, CostTotals } from "@/lib/types";
import { ThemePicker } from "./ThemePicker";

type Result =
  | { days: CostWindow; report: CostReport }
  | { days: CostWindow; error: string };

/**
 * What agent runs cost over 7, 30 or 90 days: totals on a pass, a bar per day, and the
 * repos and tickets that cost most. The window lives in `?days=` so a link keeps it.
 */
export function CostsView() {
  const days = parseDays(useSearchParams().get("days"));
  const [result, setResult] = useState<Result | null>(null);
  const [reloads, setReloads] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    fetchCosts(days, undefined, controller.signal).then(
      (report) => setResult({ days, report }),
      (err: unknown) => {
        if (controller.signal.aborted) return;
        setResult({
          days,
          error: err instanceof ApiError ? err.message : "Couldn't load the costs.",
        });
      },
    );
    return () => controller.abort();
  }, [days, reloads]);

  function choose(next: CostWindow) {
    if (next === days) return;
    try {
      window.history.replaceState(null, "", costsHref(next));
    } catch {
      // A sandboxed frame may refuse history changes; nothing else depends on it.
    }
  }

  function retry() {
    setResult(null);
    setReloads((n) => n + 1);
  }

  const current = result && result.days === days ? result : null;
  // While another window loads, the last report stays up (dimmed) instead of a blank page.
  const shown = result && "report" in result ? result.report : null;
  const loading = current === null;

  return (
    <div className="detail costs">
      <header className="detail-top">
        <Link href="/" className="back-link">
          <ArrowLeft size={16} strokeWidth={2.5} aria-hidden />
          Board
        </Link>
        <ThemePicker />
      </header>

      <div className="costs-head">
        <div>
          <h1 className="costs-title">Costs</h1>
          <p className="costs-sub">What agent runs cost, by day, repository and ticket.</p>
        </div>
        <div className="segmented" role="group" aria-label="Time span">
          {COST_WINDOWS.map((w) => (
            <button key={w} type="button" aria-pressed={w === days} onClick={() => choose(w)}>
              <span className="tabular">{w}</span> days
            </button>
          ))}
        </div>
      </div>

      {shown?.estimated ? (
        <div className="panel-note costs-estimate">
          <Info size={16} strokeWidth={2.5} aria-hidden />
          <p>
            <span className="panel-note-title">Estimates, not charges. </span>
            On a Claude plan nothing is billed per run. These are Claude Code&apos;s estimates of
            what the runs would cost at API prices.
          </p>
        </div>
      ) : null}

      <div aria-live="polite" aria-busy={loading || undefined}>
        {current && "error" in current ? (
          <p className="panel-empty" data-tone="error" role="alert">
            <TriangleAlert size={16} strokeWidth={2.5} aria-hidden />
            <span>
              <strong>Couldn&apos;t load the costs.</strong> {current.error}
            </span>
            <button type="button" className="link-button" onClick={retry}>
              Try again
            </button>
          </p>
        ) : shown ? (
          <div className="costs-body" data-stale={loading || undefined}>
            {loading ? (
              <p className="costs-updating" role="status">
                <LoaderCircle size={14} strokeWidth={2.5} className="spin" aria-hidden />
                Updating to the last {days} days…
              </p>
            ) : null}
            <CostsReport report={shown} />
          </div>
        ) : (
          <p className="panel-empty" role="status">
            <LoaderCircle size={16} strokeWidth={2.5} className="spin" aria-hidden />
            Adding up the last {days} days of runs…
          </p>
        )}
      </div>
    </div>
  );
}

function CostsReport({ report }: { report: CostReport }) {
  const range = dayRange(report.by_day);
  if (report.total.runs === 0) {
    return (
      <>
        <p className="costs-range tabular">{range}</p>
        <p className="panel-empty">
          <ChartColumn size={16} strokeWidth={2.5} aria-hidden />
          No runs in the last {report.days} days. Costs show here once agents work on tickets.
        </p>
      </>
    );
  }
  return (
    <>
      <TotalsPass total={report.total} range={range} days={report.days} estimated={report.estimated} />

      <section className="costs-section" aria-labelledby="costs-per-day">
        <h2 className="costs-heading" id="costs-per-day">
          Per day
        </h2>
        <CostChart days={report.by_day} range={range} />
      </section>

      <section className="costs-section" aria-labelledby="costs-by-repo">
        <h2 className="costs-heading" id="costs-by-repo">
          By repository
        </h2>
        <table className="cost-table">
          <thead>
            <tr>
              <th scope="col">Repository</th>
              <NumberHeads />
            </tr>
          </thead>
          <tbody>
            {report.by_repo.map((r) => (
              <tr key={r.repo}>
                <th scope="row" className="cost-name">
                  <span className="cost-title">{r.repo}</span>
                </th>
                <NumberCells row={r} />
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="costs-section" aria-labelledby="costs-top-tickets">
        <h2 className="costs-heading" id="costs-top-tickets">
          Top tickets
        </h2>
        <table className="cost-table">
          <thead>
            <tr>
              <th scope="col">Ticket</th>
              <NumberHeads />
            </tr>
          </thead>
          <tbody>
            {report.by_ticket.map((t) => (
              <tr key={t.ticket_id}>
                <th scope="row" className="cost-name">
                  <Link
                    href={`/tickets/${encodeURIComponent(t.ticket_id)}`}
                    className="cost-title"
                    prefetch={false}
                  >
                    {t.title}
                  </Link>
                  <span className="cost-where">
                    {t.repo} <span className="tabular">#{t.issue_number}</span>
                  </span>
                </th>
                <NumberCells row={t} />
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

function NumberHeads() {
  return (
    <>
      <th scope="col" className="cost-num">
        Cost
      </th>
      <th scope="col" className="cost-num">
        Runs
      </th>
      <th scope="col" className="cost-num cost-tokens">
        Input
      </th>
      <th scope="col" className="cost-num cost-tokens">
        Output
      </th>
    </>
  );
}

function NumberCells({ row }: { row: CostTotals }) {
  return (
    <>
      <td className="cost-num cost-money">{formatUsd(row.cost_usd)}</td>
      <td className="cost-num">{formatCount(row.runs)}</td>
      <td className="cost-num cost-tokens" title={`${formatCount(row.input_tokens)} tokens`}>
        {formatTokens(row.input_tokens)}
      </td>
      <td className="cost-num cost-tokens" title={`${formatCount(row.output_tokens)} tokens`}>
        {formatTokens(row.output_tokens)}
      </td>
    </>
  );
}

/** The window's totals as a Wallet pass: the cost is the primary field, counts below. */
function TotalsPass({
  total,
  range,
  days,
  estimated,
}: {
  total: CostTotals;
  range: string;
  days: number;
  estimated: boolean;
}) {
  const perRun = total.runs > 0 ? total.cost_usd / total.runs : 0;
  return (
    <article className="pass costs-pass" aria-label={`Totals for the last ${days} days`}>
      <div className="costs-pass-head">
        <p className="pass-strip">
          <ChartColumn size={13} strokeWidth={2.5} aria-hidden />
          <span className="pass-repo tabular">{range}</span>
          <span className="pass-header-field tabular">{days} days</span>
        </p>
        <p className="costs-pass-label">{estimated ? "Estimated cost" : "Spent"}</p>
        <p className="costs-readout tabular">{formatUsd(total.cost_usd)}</p>
      </div>
      <div className="notch-cut" aria-hidden>
        <span />
      </div>
      <dl className="pass-fields costs-fields">
        <div className="pass-field">
          <dt>Runs</dt>
          <dd>{formatCount(total.runs)}</dd>
        </div>
        <div className="pass-field">
          <dt>Per run</dt>
          <dd>{formatUsd(perRun)}</dd>
        </div>
        <div className="pass-field">
          <dt>Input tokens</dt>
          <dd>{formatCount(total.input_tokens)}</dd>
        </div>
        <div className="pass-field">
          <dt>Output tokens</dt>
          <dd>{formatCount(total.output_tokens)}</dd>
        </div>
      </dl>
    </article>
  );
}

/** Bars per day in inline SVG, with the same numbers in a table for screen readers. */
function CostChart({ days, range }: { days: CostDay[]; range: string }) {
  const id = useId();
  const { max, heights } = chartScale(days.map((d) => d.cost_usd));
  const n = days.length;
  const labels = axisLabelIndexes(n);
  // Each day gets a 10-unit slot; the bar fills 70% of it (less when there are many days).
  const slot = 10;
  const gap = n > 45 ? 2 : 3;
  return (
    <figure className="cost-chart">
      <div className="cost-chart-plot">
        <div className="cost-chart-axis tabular" aria-hidden>
          {max > 0 ? (
            <>
              <span>{formatAxisUsd(max)}</span>
              <span>{formatAxisUsd(max / 2)}</span>
            </>
          ) : null}
          <span>$0</span>
        </div>
        <svg
          className="cost-chart-svg"
          viewBox={`0 0 ${Math.max(1, n) * slot} 100`}
          preserveAspectRatio="none"
          role="img"
          aria-labelledby={`${id}-title ${id}-desc`}
        >
          <title id={`${id}-title`}>{`Cost per day, ${range}`}</title>
          <desc id={`${id}-desc`}>{chartSummary(days)}</desc>
          {max > 0 ? (
            <>
              <line className="cost-grid" x1="0" x2={n * slot} y1="0.5" y2="0.5" />
              <line className="cost-grid" x1="0" x2={n * slot} y1="50" y2="50" />
            </>
          ) : null}
          {days.map((d, i) => {
            const h = (heights[i] ?? 0) * 100;
            if (h <= 0) return null;
            return (
              <rect
                key={d.date}
                className="cost-bar"
                x={i * slot + gap / 2}
                y={100 - h}
                width={slot - gap}
                height={h}
              >
                <title>{`${formatDay(d.date)}: ${formatUsd(d.cost_usd)}, ${runsWord(d.runs)}`}</title>
              </rect>
            );
          })}
          <line className="cost-baseline" x1="0" x2={n * slot} y1="99.5" y2="99.5" />
        </svg>
        <div className="cost-chart-dates tabular" aria-hidden>
          {labels.map((i, k) => {
            // The first and last dates sit flush with the plot's edges; the middle one centers.
            const edge = k === 0 ? "start" : k === labels.length - 1 ? "end" : undefined;
            return (
              <span
                key={i}
                data-edge={edge}
                style={edge ? undefined : { left: `${((i + 0.5) / n) * 100}%` }}
              >
                {formatDay(days[i]!.date)}
              </span>
            );
          })}
        </div>
      </div>
      {max === 0 ? (
        <figcaption className="cost-chart-caption">No cost was recorded on any day.</figcaption>
      ) : null}
      <table className="sr-only">
        <caption>Cost per day, {range}</caption>
        <thead>
          <tr>
            <th scope="col">Day</th>
            <th scope="col">Cost</th>
            <th scope="col">Runs</th>
          </tr>
        </thead>
        <tbody>
          {days.map((d) => (
            <tr key={d.date}>
              <th scope="row">{formatDay(d.date, true)}</th>
              <td>{formatUsd(d.cost_usd)}</td>
              <td>{formatCount(d.runs)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
