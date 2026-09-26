"use client";

/**
 * One file's hunks in react-diff-view. Loaded lazily (DiffPanel imports it with
 * React.lazy), so the library's code only reaches the browser once a file is opened.
 * Colors come from globals.css (.diff-table), derived from the theme; the library's
 * own stylesheet isn't used.
 */
import { useMemo } from "react";
import {
  Decoration,
  Diff,
  Hunk,
  markEdits,
  parseDiff,
  tokenize,
  type FileData,
  type HunkTokens,
} from "react-diff-view";
import type { DiffViewType } from "./DiffPanel";

/** Word-level edit marks cost a diff per changed block; skip them on very long files. */
const MARK_EDITS_MAX_LINES = 800;

function parseOne(text: string): FileData | null {
  try {
    return parseDiff(text)[0] ?? null;
  } catch {
    return null;
  }
}

function editTokens(file: FileData): HunkTokens | null {
  const lines = file.hunks.reduce((n, h) => n + h.changes.length, 0);
  if (lines > MARK_EDITS_MAX_LINES) return null;
  try {
    return tokenize(file.hunks, { enhancers: [markEdits(file.hunks, { type: "block" })] });
  } catch {
    return null;
  }
}

export default function DiffBody({ text, view }: { text: string; view: DiffViewType }) {
  const file = useMemo(() => parseOne(text), [text]);
  const tokens = useMemo(() => (file ? editTokens(file) : null), [file]);

  // A section the parser can't read still shows, as the raw text GitHub sent.
  if (!file) return <pre className="t-raw diff-raw">{text}</pre>;
  if (file.hunks.length === 0) return <p className="diff-file-note">No lines to show.</p>;

  return (
    <Diff
      viewType={view}
      diffType={file.type}
      hunks={file.hunks}
      tokens={tokens}
      className="diff-table"
      gutterType="default"
    >
      {(hunks) =>
        hunks.flatMap((hunk, i) => [
          <Decoration key={`head-${i}`} className="diff-hunk-head">
            {hunk.content}
          </Decoration>,
          <Hunk key={`hunk-${i}`} hunk={hunk} />,
        ])
      }
    </Diff>
  );
}
