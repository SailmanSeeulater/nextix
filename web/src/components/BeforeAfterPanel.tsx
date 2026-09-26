"use client";

import { ChevronsLeftRight, ImageOff } from "lucide-react";
import {
  memo,
  useId,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
} from "react";
import {
  formatDiffPct,
  formatSize,
  generalScreenshotErrors,
  hasVisibleChange,
  screenshotGroups,
  type ScreenshotGroup,
  type Shot,
} from "@/lib/artifacts";
import type { Artifact, ReviewError } from "@/lib/types";
import { ReviewErrors } from "./ReviewErrors";

type Mode = "side" | "slider" | "changes";

const MODES: ReadonlyArray<{ id: Mode; title: string }> = [
  { id: "side", title: "Side by side" },
  { id: "slider", title: "Slider" },
  { id: "changes", title: "Changes" },
];

/**
 * Before / After: one section per captured route. Before is the default branch, after is
 * the agent's branch; the diff image marks every pixel that changed. One control picks
 * how every route is compared, as GitHub's image diff does.
 */
export const BeforeAfterPanel = memo(function BeforeAfterPanel({
  artifacts,
  reviewErrors,
}: {
  artifacts: Artifact[] | undefined;
  reviewErrors: ReviewError[] | null | undefined;
}) {
  const run = useMemo(
    () => ({ artifacts, review_errors: reviewErrors }),
    [artifacts, reviewErrors],
  );
  const groups = useMemo(() => screenshotGroups(run), [run]);
  const general = useMemo(() => generalScreenshotErrors(run), [run]);
  const [mode, setMode] = useState<Mode>("side");
  const changed = groups.filter((g) => g.diff && hasVisibleChange(g.diff));

  return (
    <div className="shots">
      {groups.length > 0 ? (
        <div className="panel-bar">
          <p className="panel-bar-summary tabular">
            <span>
              {groups.length} {groups.length === 1 ? "route" : "routes"}
            </span>
            {groups.some((g) => g.diff) ? (
              <span className="panel-bar-meta">
                {changed.length === 0
                  ? "no visible change"
                  : `${changed.length} changed`}
              </span>
            ) : null}
          </p>
          <div className="panel-bar-tools">
            <div className="segmented" role="group" aria-label="Compare screenshots">
              {MODES.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  aria-pressed={mode === m.id}
                  onClick={() => setMode(m.id)}
                >
                  {m.title}
                </button>
              ))}
            </div>
          </div>
        </div>
      ) : null}

      <ReviewErrors errors={general} />

      {groups.length === 0 ? (
        <p className="panel-empty">
          <ImageOff size={16} strokeWidth={2.5} aria-hidden />
          No screenshots were captured for this run.
        </p>
      ) : (
        groups.map((g) => <RouteShots key={g.label} group={g} mode={mode} />)
      )}
    </div>
  );
});

function RouteShots({ group, mode }: { group: ScreenshotGroup; mode: Mode }) {
  const titleId = useId();
  const size = formatSize(group.after ?? group.before);
  const diff = group.diff;

  return (
    <section className="shots-route" aria-labelledby={titleId}>
      <header className="shots-route-head">
        <h3 className="shots-route-title" id={titleId}>
          {group.label}
        </h3>
        {size ? <span className="shots-route-size tabular">{size}</span> : null}
        {diff ? (
          <span className="shots-route-change tabular">
            {formatDiffPct(diff.diffPct, diff.diffPixels)}
          </span>
        ) : null}
      </header>

      <ReviewErrors errors={group.errors} />

      {group.before || group.after || group.diff ? (
        mode === "side" ? (
          <div className="shots-pair">
            <ShotFigure shot={group.before} route={group.label} side="before" />
            <ShotFigure shot={group.after} route={group.label} side="after" />
          </div>
        ) : mode === "slider" ? (
          group.before && group.after ? (
            <CompareSlider before={group.before} after={group.after} route={group.label} />
          ) : (
            <p className="shots-missing">The slider needs both screenshots of this route.</p>
          )
        ) : diff ? (
          <ShotFigure shot={diff} route={group.label} side="diff" />
        ) : (
          <p className="shots-missing">No diff image was made for this route.</p>
        )
      ) : null}
    </section>
  );
}

const SIDE_TEXT = {
  before: { title: "Before", note: "default branch", missing: "No before screenshot" },
  after: { title: "After", note: "agent's branch", missing: "No after screenshot" },
  diff: { title: "Changed pixels", note: "highlighted", missing: "No diff image" },
} as const;

function aspect(shot: Shot | null): CSSProperties | undefined {
  return shot?.width && shot.height ? { aspectRatio: `${shot.width} / ${shot.height}` } : undefined;
}

function ShotFigure({
  shot,
  route,
  side,
}: {
  shot: Shot | null;
  route: string;
  side: "before" | "after" | "diff";
}) {
  const text = SIDE_TEXT[side];
  return (
    <figure className="shot">
      {shot ? (
        <a
          className="shot-frame"
          href={shot.url}
          target="_blank"
          rel="noreferrer"
          style={aspect(shot)}
          aria-label={`${text.title} screenshot of ${route}, full size (opens in a new tab)`}
        >
          <ShotImage shot={shot} alt="" />
        </a>
      ) : (
        <div className="shot-frame" data-missing style={{ aspectRatio: "16 / 10" }}>
          <ImageOff size={18} strokeWidth={2.25} aria-hidden />
          <span>{text.missing}</span>
        </div>
      )}
      <figcaption>
        <span className="shot-caption">{text.title}</span>
        <span className="shot-note">{text.note}</span>
      </figcaption>
    </figure>
  );
}

/** A screenshot from the artifact store; a failed load says so instead of a broken icon. */
function ShotImage({
  shot,
  alt,
  className,
  style,
}: {
  shot: Shot;
  alt: string;
  className?: string;
  style?: CSSProperties;
}) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <span className="shot-failed">
        <ImageOff size={18} strokeWidth={2.25} aria-hidden />
        Couldn&apos;t load this screenshot.
      </span>
    );
  }
  return (
    // Artifacts are auth-gated same-origin files; the image optimizer can't fetch them
    // with the viewer's session, so the plain img element is the right one here.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={shot.url}
      alt={alt}
      width={shot.width ?? undefined}
      height={shot.height ?? undefined}
      loading="lazy"
      decoding="async"
      draggable={false}
      className={className}
      style={style}
      onError={() => setFailed(true)}
    />
  );
}

const clamp = (n: number) => Math.min(100, Math.max(0, n));

/**
 * Before under after with a divider between them. The divider is a real range input
 * (arrow keys, Page Up/Down, Home/End, screen readers); pointer drags anywhere on the
 * image move it too, and vertical swipes still scroll the page.
 */
function CompareSlider({ before, after, route }: { before: Shot; after: Shot; route: string }) {
  const [pos, setPos] = useState(50);
  const frameRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const dragging = useRef(false);
  /** Focus came from a drag: no keyboard ring until a key is pressed. */
  const [pointerFocus, setPointerFocus] = useState(false);

  function fromPointer(clientX: number) {
    const rect = frameRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return;
    setPos(clamp(Math.round(((clientX - rect.left) / rect.width) * 1000) / 10));
  }

  function onPointerDown(e: PointerEvent<HTMLDivElement>) {
    if (e.button !== 0) return;
    dragging.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
    fromPointer(e.clientX);
    e.preventDefault();
    // The divider takes focus, so arrow keys fine-tune it right after a drag. Chrome
    // treats a range input's focus as :focus-visible even from a pointer, so the ring is
    // held back (data-pointer) until the keyboard is used.
    setPointerFocus(true);
    inputRef.current?.focus({ preventScroll: true });
  }

  function onPointerMove(e: PointerEvent<HTMLDivElement>) {
    if (dragging.current) fromPointer(e.clientX);
  }

  function endDrag() {
    dragging.current = false;
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    const big = e.shiftKey ? 10 : 1;
    const moves: Record<string, (p: number) => number> = {
      ArrowLeft: (p) => p - 2 * big,
      ArrowDown: (p) => p - 2 * big,
      ArrowRight: (p) => p + 2 * big,
      ArrowUp: (p) => p + 2 * big,
      PageDown: (p) => p - 10,
      PageUp: (p) => p + 10,
      Home: () => 0,
      End: () => 100,
    };
    setPointerFocus(false);
    const move = moves[e.key];
    if (!move) return;
    e.preventDefault();
    setPos((p) => clamp(Math.round(move(p) * 10) / 10));
  }

  const shown = Math.round(pos);
  return (
    <div className="compare">
      <div
        ref={frameRef}
        className="compare-frame"
        style={{ ...aspect(before), "--pos": `${pos}%` } as CSSProperties}
        data-pointer={pointerFocus || undefined}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        <ShotImage shot={before} alt={`Before: ${route}`} className="compare-before" />
        <ShotImage shot={after} alt={`After: ${route}`} className="compare-after" />
        <input
          ref={inputRef}
          className="compare-input"
          type="range"
          min={0}
          max={100}
          step={0.1}
          value={pos}
          onChange={(e) => setPos(clamp(Number(e.currentTarget.value)))}
          onKeyDown={onKeyDown}
          onBlur={() => setPointerFocus(false)}
          aria-label={`Divider between before and after, ${route}`}
          aria-valuetext={`${shown}% before, ${100 - shown}% after`}
        />
        <span className="compare-divider" aria-hidden>
          <span className="compare-handle">
            <ChevronsLeftRight size={16} strokeWidth={2.5} />
          </span>
        </span>
        <span className="compare-tag" data-side="before" aria-hidden>
          Before
        </span>
        <span className="compare-tag" data-side="after" aria-hidden>
          After
        </span>
      </div>
      <p className="compare-hint">Drag the divider, or use the arrow keys once it has focus.</p>
    </div>
  );
}
