/**
 * Pure logic for the /costs page (docs/phase6.md): which window is shown, how the API's
 * report is read, how the per-day chart scales, and how days, money and tokens are
 * written. No DOM here, so all of it is tested.
 */
import { toNumber } from "./runs";
import type { CostDay, CostRepo, CostReport, CostTicket, CostTotals } from "./types";

/** The windows the page offers, in days. */
export const COST_WINDOWS = [7, 30, 90] as const;
export type CostWindow = (typeof COST_WINDOWS)[number];
export const DEFAULT_WINDOW: CostWindow = 30;

/** `?days=` → one of the offered windows; anything else falls back to 30 days. */
export function parseDays(raw: string | string[] | null | undefined): CostWindow {
  const value = Array.isArray(raw) ? raw[0] : raw;
  const n = Number(value);
  return (COST_WINDOWS as readonly number[]).includes(n) ? (n as CostWindow) : DEFAULT_WINDOW;
}

/** The page URL for a window; the default window keeps the address bare. */
export function costsHref(days: CostWindow): string {
  return days === DEFAULT_WINDOW ? "/costs" : `/costs?days=${days}`;
}

// ------------------------------------------------------------------ reading the report

function totals(raw: unknown): CostTotals {
  const r = (raw ?? {}) as Record<string, unknown>;
  return {
    cost_usd: toNumber(r.cost_usd),
    input_tokens: toNumber(r.input_tokens),
    output_tokens: toNumber(r.output_tokens),
    runs: toNumber(r.runs),
  };
}

function list(raw: unknown): Record<string, unknown>[] {
  return Array.isArray(raw)
    ? raw.filter((x): x is Record<string, unknown> => typeof x === "object" && x !== null)
    : [];
}

const DAY = /^\d{4}-\d{2}-\d{2}$/;

/**
 * The API's report, with money and counts made numbers (a Decimal may arrive as a
 * string) and unreadable rows dropped. Null when the body isn't a report at all.
 */
export function readCostReport(raw: unknown): CostReport | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.total !== "object" || r.total === null || !Array.isArray(r.by_day)) return null;
  const by_day: CostDay[] = list(r.by_day)
    .filter((d) => typeof d.date === "string" && DAY.test(d.date))
    .map((d) => ({ date: d.date as string, ...totals(d) }));
  const by_repo: CostRepo[] = list(r.by_repo)
    .filter((d) => typeof d.repo === "string")
    .map((d) => ({ repo: d.repo as string, ...totals(d) }));
  const by_ticket: CostTicket[] = list(r.by_ticket)
    .filter((d) => typeof d.ticket_id === "string")
    .map((d) => ({
      ticket_id: d.ticket_id as string,
      repo: typeof d.repo === "string" ? d.repo : "",
      issue_number: toNumber(d.issue_number),
      title: typeof d.title === "string" && d.title ? d.title : "Untitled ticket",
      ...totals(d),
    }));
  return {
    days: toNumber(r.days),
    estimated: r.estimated === true,
    total: totals(r.total),
    by_day,
    by_repo,
    by_ticket,
  };
}

// ------------------------------------------------------------------ writing values

const USD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** "$1,204.50"; a cost that rounds to nothing but isn't zero reads "<$0.01". */
export function formatUsd(usd: number): string {
  if (usd > 0 && usd < 0.005) return "<$0.01";
  return USD.format(usd);
}

/** A chart axis value: "$0", "$5", "$2.50", "$0.025", "$1.5k". */
export function formatAxisUsd(usd: number): string {
  if (usd >= 1000) return `$${trimZeros((usd / 1000).toFixed(1))}k`;
  if (Number.isInteger(usd)) return `$${usd}`;
  if (usd >= 0.1) return `$${usd.toFixed(2)}`;
  // Axis tops are 1, 2, 2.5 or 5 × 10ⁿ, so two significant digits say it exactly.
  return `$${Number(usd.toPrecision(2))}`;
}

function trimZeros(s: string): string {
  return s.includes(".") ? s.replace(/\.?0+$/, "") : s;
}

/** Token counts for tables: "812", "12.4k", "3.1M". The exact count goes in a title. */
export function formatTokens(n: number): string {
  const v = Math.round(n);
  if (v < 1000) return String(v);
  if (v < 999_500) return `${trimZeros((v / 1000).toFixed(v < 10_000 ? 1 : 0))}k`;
  return `${trimZeros((v / 1_000_000).toFixed(1))}M`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-09-03" → "Sep 3" (or "Sep 3, 2026"). The API's days are UTC dates. */
export function formatDay(iso: string, withYear = false): string {
  const [y, m, d] = iso.split("-").map(Number) as [number, number, number];
  const month = MONTHS[m - 1];
  if (!month || !d) return iso;
  return withYear ? `${month} ${d}, ${y}` : `${month} ${d}`;
}

/** "Aug 28 – Sep 26"; with years when the span crosses one. Empty for no days. */
export function dayRange(days: readonly { date: string }[]): string {
  const first = days[0]?.date;
  const last = days[days.length - 1]?.date;
  if (!first || !last) return "";
  if (first === last) return formatDay(first, true);
  const years = first.slice(0, 4) !== last.slice(0, 4);
  return `${formatDay(first, years)} – ${formatDay(last, years)}`;
}

// ------------------------------------------------------------------ the chart

/** The smallest of 1, 2, 2.5, 5 × 10ⁿ at or above `value`; 0 stays 0. */
export function niceCeiling(value: number): number {
  if (!(value > 0) || !Number.isFinite(value)) return 0;
  const power = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 2.5, 5, 10]) {
    // Rounded so float noise (0.1 × 3) can't push a value past its own step.
    const candidate = Number((step * power).toPrecision(12));
    if (candidate >= value) return candidate;
  }
  return 10 * power;
}

/** A day with any cost stays visible, however small next to the biggest day. */
export const MIN_BAR = 0.02;

export interface ChartScale {
  /** The top of the axis in dollars; 0 when every day cost nothing. */
  max: number;
  /** Each day's bar as a share of the chart's height, 0 to 1. */
  heights: number[];
}

export function chartScale(values: readonly number[]): ChartScale {
  const top = Math.max(0, ...values.map((v) => (Number.isFinite(v) ? v : 0)));
  const max = niceCeiling(top);
  const heights = values.map((v) => {
    if (!(v > 0) || max === 0) return 0;
    return Math.min(1, Math.max(MIN_BAR, v / max));
  });
  return { max, heights };
}

/** Which days get a date under the chart: the first, the last, and one between. */
export function axisLabelIndexes(count: number): number[] {
  if (count <= 0) return [];
  if (count === 1) return [0];
  if (count < 5) return [0, count - 1];
  return [0, Math.floor((count - 1) / 2), count - 1];
}

/** One sentence that says what the chart shows, for its accessible description. */
export function chartSummary(days: readonly CostDay[]): string {
  if (days.length === 0) return "No days to show.";
  const active = days.filter((d) => d.runs > 0).length;
  const peak = days.reduce((a, b) => (b.cost_usd > a.cost_usd ? b : a), days[0]!);
  const span = `${active} of ${days.length} days had runs.`;
  if (!(peak.cost_usd > 0)) return `No cost was recorded on any day. ${span}`;
  return `Highest: ${formatUsd(peak.cost_usd)} on ${formatDay(peak.date)}. ${span}`;
}

/** "1 run" / "12 runs". */
export function runsWord(n: number): string {
  return `${Math.round(n).toLocaleString("en-US")} ${n === 1 ? "run" : "runs"}`;
}
