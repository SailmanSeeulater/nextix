"use client";

import {
  ArrowUpRight,
  ChevronRight,
  FileMinus,
  FilePen,
  FilePlus,
  FileSymlink,
  GitPullRequest,
  type LucideIcon,
  LoaderCircle,
  TriangleAlert,
} from "lucide-react";
import {
  lazy,
  memo,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react";
import { formatCount } from "@/lib/runs";
import { ApiError, fetchTicketDiff } from "@/lib/client-api";
import {
  LARGE_FILE_LINES,
  estimatedHeight,
  fileStatusWord,
  initiallyCollapsed,
  isGenerated,
  prFilesUrl,
  splitDiff,
  type DiffFile,
  type FileStatus,
} from "@/lib/diff";
import type { TicketDiff } from "@/lib/types";

/** The viewer library loads only when a file is first opened. */
const DiffBody = lazy(() => import("./DiffBody"));

export type DiffViewType = "unified" | "split";

const VIEW_KEY = "nextix.diffView";
const WIDE_QUERY = "(min-width: 1024px)";

function subscribeWide(onChange: () => void): () => void {
  const query = window.matchMedia(WIDE_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

/** Split view needs the room; on narrower screens the diff is always unified. */
function useWide(): boolean {
  return useSyncExternalStore(
    subscribeWide,
    () => window.matchMedia(WIDE_QUERY).matches,
    () => false,
  );
}

function readViewPreference(): DiffViewType {
  try {
    return localStorage.getItem(VIEW_KEY) === "split" ? "split" : "unified";
  } catch {
    return "unified";
  }
}

const FILE_ICONS: Record<FileStatus, LucideIcon> = {
  added: FilePlus,
  deleted: FileMinus,
  modified: FilePen,
  renamed: FileSymlink,
  copied: FileSymlink,
};

/**
 * The PR's diff, from GitHub through the API. Loaded when the tab is first opened, and
 * again when the PR's head moves (a review-feedback run pushed new commits).
 */
export const DiffPanel = memo(function DiffPanel({
  ticketId,
  prNumber,
  prUrl,
  headSha,
}: {
  ticketId: string;
  prNumber: number | null;
  prUrl: string | null;
  headSha: string | null;
}) {
  const [reloads, setReloads] = useState(0);
  if (prNumber === null) return <NoPullRequest />;
  return (
    <DiffLoader
      key={`${prNumber}:${headSha ?? ""}:${reloads}`}
      ticketId={ticketId}
      prUrl={prUrl}
      onRetry={() => setReloads((n) => n + 1)}
    />
  );
});

type LoadState =
  | { status: "loading" }
  | { status: "no_pr" }
  | { status: "error"; message: string }
  | { status: "ok"; diff: TicketDiff };

function DiffLoader({
  ticketId,
  prUrl,
  onRetry,
}: {
  ticketId: string;
  prUrl: string | null;
  onRetry: () => void;
}) {
  const [state, setState] = useState<LoadState>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    fetchTicketDiff(ticketId, undefined, controller.signal).then(
      (diff) => setState(diff ? { status: "ok", diff } : { status: "no_pr" }),
      (err: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          status: "error",
          message: err instanceof ApiError ? err.message : "Couldn't load the diff.",
        });
      },
    );
    return () => controller.abort();
  }, [ticketId]);

  switch (state.status) {
    case "loading":
      return (
        <p className="panel-empty" role="status">
          <LoaderCircle size={16} strokeWidth={2.5} className="spin" aria-hidden />
          Loading the diff from GitHub…
        </p>
      );
    case "no_pr":
      return <NoPullRequest />;
    case "error":
      return (
        <p className="panel-empty" data-tone="error" role="alert">
          <TriangleAlert size={16} strokeWidth={2.5} aria-hidden />
          {state.message}
          <button type="button" className="link-button" onClick={onRetry}>
            Try again
          </button>
        </p>
      );
    case "ok":
      return <DiffFiles diff={state.diff} prUrl={prUrl} />;
  }
}

function NoPullRequest() {
  return (
    <p className="panel-empty">
      <GitPullRequest size={16} strokeWidth={2.5} aria-hidden />
      No pull request yet. The diff shows here once the agent opens one.
    </p>
  );
}

function DiffFiles({ diff, prUrl }: { diff: TicketDiff; prUrl: string | null }) {
  const parsed = useMemo(() => splitDiff(diff.diff, diff.truncated), [diff]);
  const [collapsed, setCollapsed] = useState(() => initiallyCollapsed(parsed.files));
  const [preferred, setPreferred] = useState<DiffViewType>(readViewPreference);
  const wide = useWide();
  const view: DiffViewType = wide ? preferred : "unified";
  const filesUrl = prFilesUrl(prUrl);

  const toggle = useCallback((key: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  function choose(next: DiffViewType) {
    setPreferred(next);
    try {
      localStorage.setItem(VIEW_KEY, next);
    } catch {
      // storage unavailable: the choice lasts this visit
    }
  }

  function jumpTo(file: DiffFile, index: number) {
    setCollapsed((prev) => {
      if (!prev.has(file.key)) return prev;
      const next = new Set(prev);
      next.delete(file.key);
      return next;
    });
    requestAnimationFrame(() => {
      const target = document.getElementById(`diff-file-${index}`);
      if (!target) return;
      settleScroll(target);
      target.querySelector<HTMLElement>(".diff-file-toggle")?.focus({ preventScroll: true });
    });
  }

  const openable = parsed.files.filter((f) => !f.binary && f.lines > 0);
  const allOpen = openable.every((f) => !collapsed.has(f.key));
  const count = parsed.files.length;

  return (
    <div className="diff">
      {diff.truncated ? (
        <div className="panel-note" role="note">
          <TriangleAlert size={16} strokeWidth={2.5} aria-hidden />
          <div>
            <p className="panel-note-title">The diff is too large to show here</p>
            <p>
              {count > 0
                ? `The first ${count === 1 ? "file is" : `${formatCount(count)} files are`} below. `
                : ""}
              {filesUrl ? (
                <a href={filesUrl} target="_blank" rel="noreferrer">
                  See every change in the pull request&apos;s Files tab
                  <ArrowUpRight size={14} strokeWidth={2.5} aria-hidden />
                </a>
              ) : (
                "GitHub's Files tab on the pull request has every change."
              )}
            </p>
          </div>
        </div>
      ) : null}

      {count === 0 && !diff.truncated ? (
        <p className="panel-empty">This pull request doesn&apos;t change any files.</p>
      ) : null}

      {count > 0 ? (
        <>
          <div className="panel-bar">
            <p className="panel-bar-summary tabular">
              <span>
                {formatCount(count)} {count === 1 ? "file" : "files"}
                {diff.truncated ? " shown" : " changed"}
              </span>
              <span className="diff-count" data-sign="add">
                +{formatCount(parsed.additions)}
              </span>
              <span className="diff-count" data-sign="del">
                −{formatCount(parsed.deletions)}
              </span>
            </p>
            <div className="panel-bar-tools">
              {wide ? (
                <div className="segmented" role="group" aria-label="Diff layout">
                  <button
                    type="button"
                    aria-pressed={view === "unified"}
                    onClick={() => choose("unified")}
                  >
                    Unified
                  </button>
                  <button
                    type="button"
                    aria-pressed={view === "split"}
                    onClick={() => choose("split")}
                  >
                    Split
                  </button>
                </div>
              ) : null}
              {openable.length > 0 ? (
                <button
                  type="button"
                  className="link-button"
                  onClick={() =>
                    setCollapsed(allOpen ? new Set(openable.map((f) => f.key)) : new Set())
                  }
                >
                  {allOpen ? "Collapse all" : "Expand all"}
                </button>
              ) : null}
            </div>
          </div>

          {count > 1 ? (
            <details className="diff-index">
              <summary>
                <ChevronRight size={14} strokeWidth={2.5} className="t-chevron" aria-hidden />
                Changed files
              </summary>
              <ol>
                {parsed.files.map((file, i) => (
                  <li key={file.key}>
                    <button type="button" className="diff-index-item" onClick={() => jumpTo(file, i)}>
                      <span className="diff-index-path">{file.path}</span>
                      <FileCounts file={file} />
                    </button>
                  </li>
                ))}
              </ol>
            </details>
          ) : null}

          <ol className="diff-files">
            {parsed.files.map((file, i) => (
              <DiffFileSection
                key={file.key}
                file={file}
                index={i}
                open={!collapsed.has(file.key)}
                view={view}
                onToggle={toggle}
              />
            ))}
          </ol>
        </>
      ) : null}
    </div>
  );
}

/**
 * Scroll a file to the top, then again while files it passed render at their real
 * height (off-screen files only reserve an estimate; see .diff-file-body).
 */
function settleScroll(el: HTMLElement, tries = 5) {
  el.scrollIntoView({ block: "start" });
  if (tries <= 1) return;
  requestAnimationFrame(() =>
    requestAnimationFrame(() => {
      const want = parseFloat(getComputedStyle(el).scrollMarginTop) || 0;
      if (Math.abs(el.getBoundingClientRect().top - want) > 2) settleScroll(el, tries - 1);
    }),
  );
}

function FileCounts({ file }: { file: DiffFile }) {
  if (file.additions === 0 && file.deletions === 0) return null;
  return (
    <span className="diff-file-counts tabular">
      <span className="diff-count" data-sign="add">
        +{formatCount(file.additions)}
      </span>
      <span className="diff-count" data-sign="del">
        −{formatCount(file.deletions)}
      </span>
    </span>
  );
}

/** Why a file starts collapsed, said once on its header. */
function collapseReason(file: DiffFile): string | null {
  if (isGenerated(file.path)) return "Generated";
  if (file.lines > LARGE_FILE_LINES) return "Large";
  return null;
}

const DiffFileSection = memo(function DiffFileSection({
  file,
  index,
  open,
  view,
  onToggle,
}: {
  file: DiffFile;
  index: number;
  open: boolean;
  view: DiffViewType;
  onToggle: (key: string) => void;
}) {
  const Icon = FILE_ICONS[file.status];
  const bodyId = `diff-file-body-${index}`;
  const openable = !file.binary && file.lines > 0;
  const reason = collapseReason(file);
  const heading = (
    <>
      <Icon size={15} strokeWidth={2.25} className="diff-file-icon" aria-hidden />
      <span className="diff-file-name">
        {file.previousPath ? (
          <>
            <span className="diff-file-previous">{file.previousPath}</span>
            <span aria-hidden> → </span>
            <span className="sr-only"> to </span>
          </>
        ) : null}
        <span className="diff-file-path">{file.path}</span>
      </span>
      <span className="sr-only">, {fileStatusWord(file.status)}</span>
      {reason && openable ? <span className="diff-file-tag">{reason}</span> : null}
      <FileCounts file={file} />
    </>
  );

  return (
    <li className="diff-file" id={`diff-file-${index}`} data-open={open && openable}>
      <h3 className="diff-file-head">
        {openable ? (
          <button
            type="button"
            className="diff-file-toggle"
            aria-expanded={open}
            aria-controls={bodyId}
            onClick={() => onToggle(file.key)}
          >
            <ChevronRight size={14} strokeWidth={2.5} className="t-chevron" aria-hidden />
            {heading}
          </button>
        ) : (
          <span className="diff-file-toggle" data-static>
            {heading}
          </span>
        )}
      </h3>
      {openable && open ? (
        <div
          className="diff-file-body"
          id={bodyId}
          style={{ containIntrinsicSize: `auto ${estimatedHeight(file)}px` }}
        >
          <Suspense
            fallback={
              <p className="diff-file-note">
                <LoaderCircle size={14} strokeWidth={2.5} className="spin" aria-hidden />
                Loading the viewer…
              </p>
            }
          >
            <DiffBody text={file.text} view={view} />
          </Suspense>
        </div>
      ) : null}
      {!openable ? (
        <p className="diff-file-note">
          {file.binary
            ? "Binary file, not shown."
            : file.status === "renamed"
              ? "Renamed without changes."
              : "No line changes (mode or metadata only)."}
        </p>
      ) : null}
    </li>
  );
});
