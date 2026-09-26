/**
 * Pure helpers for the Diff tab. The PR diff (GitHub's unified format, up to 2 MB) is
 * split into one section per file with cheap string work; the viewer library parses a
 * file's section only when that file is opened, so a huge diff stays responsive.
 */

export type FileStatus = "added" | "deleted" | "modified" | "renamed" | "copied";

export interface DiffFile {
  /** Stable within one diff: position and path. */
  key: string;
  /** The path to show: the new path, or the old one for a deleted file. */
  path: string;
  /** Where a renamed or copied file came from. */
  previousPath: string | null;
  status: FileStatus;
  binary: boolean;
  additions: number;
  deletions: number;
  /** Rows the viewer would draw: every hunk line, context included. */
  lines: number;
  /** Hunks (each drawn with its own "@@" header row). */
  hunks: number;
  /** This file's section of the diff, its `diff --git` line included. */
  text: string;
}

export interface ParsedDiff {
  files: DiffFile[];
  additions: number;
  deletions: number;
  /** True when the last file was dropped because the diff was cut off inside it. */
  droppedPartial: boolean;
}

const FILE_START = "diff --git ";

/** Decode a git C-style quoted path ("caf\303\251.txt"): escapes and octal UTF-8 bytes. */
export function unquoteGitPath(raw: string): string {
  if (!(raw.length >= 2 && raw.startsWith('"') && raw.endsWith('"'))) return raw;
  const body = raw.slice(1, -1);
  const bytes: number[] = [];
  const encoder = new TextEncoder();
  const simple: Record<string, number> = { n: 10, t: 9, r: 13, '"': 34, "\\": 92, a: 7, b: 8, f: 12, v: 11 };
  for (let i = 0; i < body.length; i++) {
    const ch = body[i]!;
    if (ch !== "\\") {
      for (const b of encoder.encode(ch)) bytes.push(b);
      continue;
    }
    const next = body[i + 1] ?? "";
    const octal = /^[0-7]{3}/.exec(body.slice(i + 1, i + 4));
    if (octal) {
      bytes.push(parseInt(octal[0], 8));
      i += 3;
    } else if (next in simple) {
      bytes.push(simple[next]!);
      i += 1;
    } else {
      bytes.push(92);
    }
  }
  return new TextDecoder().decode(new Uint8Array(bytes));
}

/** Split a line's "a/x b/y" or quoted pair into two raw tokens. */
function splitPair(rest: string): [string, string] | null {
  if (rest.startsWith('"')) {
    const end = findQuoteEnd(rest, 0);
    if (end < 0) return null;
    const first = rest.slice(0, end + 1);
    const second = rest.slice(end + 2);
    return [first, second];
  }
  // Unquoted: the two paths are usually the same, so "a/P b/P" splits in the middle.
  if (rest.length % 2 === 1) {
    const half = (rest.length - 1) / 2;
    const a = rest.slice(0, half);
    const b = rest.slice(half + 1);
    if (rest[half] === " " && a.slice(2) === b.slice(2)) return [a, b];
  }
  const quoted = rest.indexOf(' "');
  if (quoted > 0) return [rest.slice(0, quoted), rest.slice(quoted + 1)];
  const at = rest.lastIndexOf(" b/");
  return at > 0 ? [rest.slice(0, at), rest.slice(at + 1)] : null;
}

function findQuoteEnd(s: string, start: number): number {
  for (let i = start + 1; i < s.length; i++) {
    if (s[i] === "\\") i++;
    else if (s[i] === '"') return i;
  }
  return -1;
}

/** "a/src/x.ts" → "src/x.ts"; "/dev/null" → null. Quotes are decoded. */
function stripPrefix(raw: string): string | null {
  const path = unquoteGitPath(raw.trim());
  if (path === "/dev/null") return null;
  return /^[ab]\//.test(path) ? path.slice(2) : path;
}

/** Read one file's section: its paths, status and line counts. */
function describeFile(text: string, index: number): DiffFile {
  const lines = text.split("\n");
  let oldPath: string | null = null;
  let newPath: string | null = null;
  let status: FileStatus = "modified";
  let binary = false;
  let additions = 0;
  let deletions = 0;
  let rows = 0;
  let hunks = 0;
  let inHunk = false;

  const pair = splitPair((lines[0] ?? "").slice(FILE_START.length));
  if (pair) {
    oldPath = stripPrefix(pair[0]);
    newPath = stripPrefix(pair[1]);
  }

  for (let i = 1; i < lines.length; i++) {
    const line = lines[i]!;
    if (line.startsWith("@@")) {
      inHunk = true;
      hunks++;
      continue;
    }
    if (inHunk) {
      // Inside a hunk every line is prefixed, so a removed "-- comment" reads "--- comment"
      // and must count as a deletion, not a header.
      const c = line[0];
      if (c === "+") additions++;
      else if (c === "-") deletions++;
      if (c === "+" || c === "-" || c === " ") rows++;
      continue;
    }
    if (line.startsWith("new file mode")) status = "added";
    else if (line.startsWith("deleted file mode")) status = "deleted";
    else if (line.startsWith("rename from ")) {
      status = "renamed";
      oldPath = unquoteGitPath(line.slice("rename from ".length));
    } else if (line.startsWith("rename to ")) {
      status = "renamed";
      newPath = unquoteGitPath(line.slice("rename to ".length));
    } else if (line.startsWith("copy from ")) {
      status = "copied";
      oldPath = unquoteGitPath(line.slice("copy from ".length));
    } else if (line.startsWith("copy to ")) {
      status = "copied";
      newPath = unquoteGitPath(line.slice("copy to ".length));
    } else if (line.startsWith("--- ")) {
      oldPath = stripPrefix(line.slice(4));
      if (oldPath === null) status = "added";
    } else if (line.startsWith("+++ ")) {
      newPath = stripPrefix(line.slice(4));
      if (newPath === null) status = "deleted";
    } else if (line.startsWith("Binary files ") || line.startsWith("GIT binary patch")) {
      binary = true;
    }
  }

  const path = (status === "deleted" ? oldPath : newPath) ?? oldPath ?? newPath ?? "(unknown file)";
  const moved = status === "renamed" || status === "copied";
  return {
    key: `${index}:${path}`,
    path,
    previousPath: moved && oldPath !== path ? oldPath : null,
    status,
    binary,
    additions,
    deletions,
    lines: rows,
    hunks,
    text,
  };
}

/** Roughly how tall an opened file is, so an off-screen file reserves its space. */
export function estimatedHeight(file: Pick<DiffFile, "lines" | "hunks">): number {
  return Math.round(file.lines * 19.4 + file.hunks * 26);
}

/**
 * Split a unified diff into files. When the API says it was truncated, the last file
 * may be cut off mid-hunk, so it is dropped rather than shown wrong.
 */
export function splitDiff(text: string, truncated = false): ParsedDiff {
  const starts: number[] = [];
  if (text.startsWith(FILE_START)) starts.push(0);
  for (let at = text.indexOf("\n" + FILE_START); at >= 0; at = text.indexOf("\n" + FILE_START, at + 1)) {
    starts.push(at + 1);
  }
  let files = starts.map((start, i) => describeFile(text.slice(start, starts[i + 1] ?? text.length), i));
  const droppedPartial = truncated && files.length > 0;
  if (droppedPartial) files = files.slice(0, -1);
  return {
    files,
    additions: files.reduce((n, f) => n + f.additions, 0),
    deletions: files.reduce((n, f) => n + f.deletions, 0),
    droppedPartial,
  };
}

/** A file this long starts collapsed; it opens on request. */
export const LARGE_FILE_LINES = 400;
/** Files open by default until this many rows are on the page; the rest start collapsed. */
export const OPEN_ROWS_BUDGET = 1500;

const GENERATED = [
  /(^|\/)package-lock\.json$/,
  /(^|\/)npm-shrinkwrap\.json$/,
  /(^|\/)yarn\.lock$/,
  /(^|\/)pnpm-lock\.yaml$/,
  /(^|\/)bun\.lockb?$/,
  /(^|\/)Cargo\.lock$/,
  /(^|\/)poetry\.lock$/,
  /(^|\/)uv\.lock$/,
  /(^|\/)Pipfile\.lock$/,
  /(^|\/)Gemfile\.lock$/,
  /(^|\/)composer\.lock$/,
  /(^|\/)go\.sum$/,
  /\.min\.(js|css)$/,
  /\.map$/,
  /\.snap$/,
];

/** Lockfiles, minified bundles, source maps and snapshots: rarely what a reviewer reads. */
export function isGenerated(path: string): boolean {
  return GENERATED.some((re) => re.test(path));
}

/**
 * Which files start collapsed: generated files, large files, and everything after the
 * page already holds OPEN_ROWS_BUDGET rows. Binary files have nothing to open.
 */
export function initiallyCollapsed(files: readonly DiffFile[]): Set<string> {
  const collapsed = new Set<string>();
  let rows = 0;
  for (const file of files) {
    if (file.binary || file.lines === 0) continue;
    if (isGenerated(file.path) || file.lines > LARGE_FILE_LINES || rows + file.lines > OPEN_ROWS_BUDGET) {
      collapsed.add(file.key);
      continue;
    }
    rows += file.lines;
  }
  return collapsed;
}

/** The PR's Files tab on GitHub, for a diff too large to show here. */
export function prFilesUrl(prUrl: string | null): string | null {
  if (!prUrl) return null;
  return `${prUrl.replace(/\/+$/, "")}/files`;
}

const STATUS_WORDS: Record<FileStatus, string> = {
  added: "Added",
  deleted: "Deleted",
  modified: "Modified",
  renamed: "Renamed",
  copied: "Copied",
};

export function fileStatusWord(status: FileStatus): string {
  return STATUS_WORDS[status];
}
