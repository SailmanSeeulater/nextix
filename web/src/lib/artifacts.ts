/**
 * Pure helpers for a run's review artifacts (docs/phase4.md): screenshots grouped by
 * route for the Before / After tab, the test report for the Checks tab, and the review
 * errors each tab shows in place.
 */
import { formatCount } from "./runs";
import type { Artifact, ReviewError, RunDetail } from "./types";

/** The parts of a run these helpers read (a whole RunDetail will do). */
export type RunReview = Partial<Pick<RunDetail, "artifacts" | "review_errors">>;

/** Artifacts are served same-origin through the proxy; anything else is not rendered. */
export function isArtifactUrl(url: unknown): url is string {
  return typeof url === "string" && /^\/api\/artifacts\/[A-Za-z0-9_-]+$/.test(url);
}

function metaNumber(meta: Artifact["meta"], key: string): number | null {
  const value = meta?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function metaBoolean(meta: Artifact["meta"], key: string): boolean | null {
  const value = meta?.[key];
  return typeof value === "boolean" ? value : null;
}

export interface Shot {
  id: string;
  url: string;
  width: number | null;
  height: number | null;
}

export interface DiffShot extends Shot {
  diffPixels: number | null;
  diffPct: number | null;
}

export interface ScreenshotGroup {
  /** The route ("/settings"). */
  label: string;
  before: Shot | null;
  after: Shot | null;
  diff: DiffShot | null;
  /** Manifest errors for this route (a capture that failed, a rejected file). */
  errors: ReviewError[];
}

const SCREENSHOT_KINDS = new Set(["screenshot_before", "screenshot_after", "screenshot_diff"]);

function toShot(a: Artifact): Shot {
  return {
    id: a.id,
    url: a.url,
    width: metaNumber(a.meta, "width"),
    height: metaNumber(a.meta, "height"),
  };
}

/** Screenshot labels are routes, which start with "/" (docs/phase4.md). */
function isRouteLabel(label: string | null, routes: ReadonlySet<string>): label is string {
  return label !== null && (routes.has(label) || label.startsWith("/"));
}

/** True when an error is about the app or its screenshots rather than setup or tests. */
export function isScreenshotError(error: ReviewError, routes: ReadonlySet<string>): boolean {
  return (
    isRouteLabel(error.label, routes) ||
    /^(app|screenshot|before|after|diff|capture)/i.test(error.step)
  );
}

/**
 * One group per route, in the order the runner captured them. A route that only has
 * errors (both captures failed) still gets a group, so the failure shows in place.
 */
export function screenshotGroups(run: RunReview | null): ScreenshotGroup[] {
  if (!run) return [];
  const groups = new Map<string, ScreenshotGroup>();
  const group = (label: string) => {
    let g = groups.get(label);
    if (!g) {
      g = { label, before: null, after: null, diff: null, errors: [] };
      groups.set(label, g);
    }
    return g;
  };
  for (const a of run.artifacts ?? []) {
    if (!SCREENSHOT_KINDS.has(a.kind) || !isArtifactUrl(a.url)) continue;
    const g = group(a.label ?? "/");
    if (a.kind === "screenshot_before") g.before ??= toShot(a);
    else if (a.kind === "screenshot_after") g.after ??= toShot(a);
    else
      g.diff ??= {
        ...toShot(a),
        diffPixels: metaNumber(a.meta, "diff_pixels"),
        diffPct: metaNumber(a.meta, "diff_pct"),
      };
  }
  const routes = new Set(groups.keys());
  // A route whose captures all failed has no artifacts, only errors: it still gets a group.
  for (const error of run.review_errors ?? []) {
    if (isRouteLabel(error.label, routes)) group(error.label).errors.push(error);
  }
  return [...groups.values()];
}

/** Screenshot errors that name no route ("app did not answer on :3000"). */
export function generalScreenshotErrors(run: RunReview | null): ReviewError[] {
  if (!run) return [];
  const routes = new Set(screenshotGroups(run).map((g) => g.label));
  return (run.review_errors ?? []).filter(
    (e) => isScreenshotError(e, routes) && !isRouteLabel(e.label, routes),
  );
}

/** Review errors that belong with the tests (setup, the test command, anything else). */
export function otherReviewErrors(run: RunReview | null): ReviewError[] {
  if (!run) return [];
  const routes = new Set(screenshotGroups(run).map((g) => g.label));
  return (run.review_errors ?? []).filter((e) => !isScreenshotError(e, routes));
}

/** The Before / After tab shows when the run has screenshots, or screenshot errors to explain. */
export function hasScreenshots(run: RunReview | null): boolean {
  return screenshotGroups(run).length > 0 || generalScreenshotErrors(run).length > 0;
}

/**
 * Whether a route's diff found any changed pixel. The pixel count decides when it is
 * known: the runner rounds `diff_pct` to two decimals, so a handful of changed pixels on
 * a large page arrives as 0 while `diff_pixels` is still above zero.
 */
export function hasVisibleChange(diff: Pick<DiffShot, "diffPct" | "diffPixels">): boolean {
  if (diff.diffPixels !== null) return diff.diffPixels > 0;
  return diff.diffPct === null || diff.diffPct > 0;
}

function pixelWord(n: number): string {
  return `${formatCount(n)} ${n === 1 ? "pixel" : "pixels"}`;
}

/** "1.21% of pixels changed"; "No visible change" only when nothing changed. */
export function formatDiffPct(pct: number | null, pixels: number | null = null): string {
  if (!hasVisibleChange({ diffPct: pct, diffPixels: pixels })) return "No visible change";
  if (pct === null) {
    return pixels === null ? "Changes highlighted" : `${pixelWord(pixels)} changed`;
  }
  if (pct < 0.01) {
    return pixels === null
      ? "Less than 0.01% of pixels changed"
      : `${pixelWord(pixels)} changed (under 0.01%)`;
  }
  const value = Number(pct.toFixed(2)).toString();
  return `${value}% of pixels changed`;
}

/** "1280 × 800" when both sides are known. */
export function formatSize(shot: Shot | null): string | null {
  if (!shot || shot.width === null || shot.height === null) return null;
  return `${shot.width} × ${shot.height}`;
}

export interface TestReport {
  /** The stored output, fetched as text; null when the runner didn't save one. */
  url: string | null;
  /** The output was longer than the runner keeps (the last 200 KB). */
  truncated: boolean;
}

export function testReport(run: RunReview | null): TestReport | null {
  const a = run?.artifacts?.find((x) => x.kind === "test_report" && isArtifactUrl(x.url));
  if (!a) return null;
  return { url: a.url, truncated: metaBoolean(a.meta, "truncated") ?? false };
}

export interface Tail {
  text: string;
  /** Lines in the whole output. */
  totalLines: number;
  /** Lines left out of `text` (0 when it is all there). */
  hiddenLines: number;
}

/**
 * The end of a test log: the last `maxLines` lines, also capped at `maxChars` so one
 * enormous line can't flood the page. The end is where a failure is reported.
 */
export function tailText(text: string, maxLines = 60, maxChars = 12_000): Tail {
  const body = text.endsWith("\n") ? text.slice(0, -1) : text;
  if (body === "") return { text: "", totalLines: 0, hiddenLines: 0 };
  const lines = body.split("\n");
  let kept = lines.slice(-maxLines);
  let joined = kept.join("\n");
  if (joined.length > maxChars) {
    const cut = joined.length - maxChars;
    let clipped = joined.slice(cut);
    // Start on a whole line when there is one; otherwise show the clipped line.
    const firstBreak = clipped.indexOf("\n");
    if (joined[cut - 1] !== "\n" && firstBreak >= 0 && firstBreak < clipped.length - 1) {
      clipped = clipped.slice(firstBreak + 1);
    }
    joined = clipped;
    kept = joined.split("\n");
  }
  return { text: joined, totalLines: lines.length, hiddenLines: lines.length - kept.length };
}

/** "app_after" → "App after"; the manifest's step names are short snake_case words. */
export function stepWord(step: string): string {
  const words = step.replace(/[_-]+/g, " ").trim();
  return words ? words[0]!.toUpperCase() + words.slice(1) : "Review";
}
