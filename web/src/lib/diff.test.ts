import { describe, expect, it } from "vitest";
import {
  LARGE_FILE_LINES,
  OPEN_ROWS_BUDGET,
  estimatedHeight,
  fileStatusWord,
  initiallyCollapsed,
  isGenerated,
  prFilesUrl,
  splitDiff,
  unquoteGitPath,
  type DiffFile,
} from "./diff";

const MODIFIED = [
  "diff --git a/src/app/page.tsx b/src/app/page.tsx",
  "index 3b18e51..a9c2f10 100644",
  "--- a/src/app/page.tsx",
  "+++ b/src/app/page.tsx",
  "@@ -1,5 +1,6 @@",
  ' import { Button } from "./button";',
  "-export default function Page() {",
  "+export default function Page({ coupon }: Props) {",
  "+  const disabled = !coupon;",
  "   return <Button />;",
  " }",
  "@@ -20,3 +21,2 @@ function Footer() {",
  "--- a SQL comment the change removes",
  " end",
  "-gone",
  "",
].join("\n");

const ADDED = [
  "diff --git a/docs/new file.md b/docs/new file.md",
  "new file mode 100644",
  "index 0000000..e69de29",
  "--- /dev/null",
  "+++ b/docs/new file.md",
  "@@ -0,0 +1,2 @@",
  "+# Title",
  "+Body",
  "",
].join("\n");

const DELETED = [
  "diff --git a/old.txt b/old.txt",
  "deleted file mode 100644",
  "index e69de29..0000000",
  "--- a/old.txt",
  "+++ /dev/null",
  "@@ -1 +0,0 @@",
  "-bye",
  "",
].join("\n");

const RENAMED = [
  "diff --git a/lib/a.ts b/lib/b.ts",
  "similarity index 100%",
  "rename from lib/a.ts",
  "rename to lib/b.ts",
  "",
].join("\n");

const BINARY = [
  "diff --git a/public/logo.png b/public/logo.png",
  "index 1111111..2222222 100644",
  "Binary files a/public/logo.png and b/public/logo.png differ",
  "",
].join("\n");

describe("splitDiff", () => {
  it("splits files and counts additions and deletions per file", () => {
    const { files, additions, deletions, droppedPartial } = splitDiff(MODIFIED + ADDED);
    expect(files.map((f) => f.path)).toEqual(["src/app/page.tsx", "docs/new file.md"]);
    expect(files[0]).toMatchObject({
      status: "modified",
      additions: 2,
      deletions: 3,
      lines: 9,
      hunks: 2,
    });
    expect(files[1]).toMatchObject({ status: "added", additions: 2, deletions: 0 });
    expect(additions).toBe(4);
    expect(deletions).toBe(3);
    expect(droppedPartial).toBe(false);
  });

  it("counts a removed line that starts with dashes as a deletion, not a header", () => {
    const file = splitDiff(MODIFIED).files[0]!;
    expect(file.deletions).toBe(3);
    expect(file.path).toBe("src/app/page.tsx");
  });

  it("keeps each file's own section for the viewer", () => {
    const { files } = splitDiff(MODIFIED + DELETED);
    expect(files[0]!.text).toBe(MODIFIED);
    expect(files[1]!.text).toBe(DELETED);
  });

  it("reads deletes, renames and binary files", () => {
    const { files } = splitDiff(DELETED + RENAMED + BINARY);
    expect(files[0]).toMatchObject({ path: "old.txt", status: "deleted", deletions: 1 });
    expect(files[1]).toMatchObject({
      path: "lib/b.ts",
      previousPath: "lib/a.ts",
      status: "renamed",
      lines: 0,
    });
    expect(files[2]).toMatchObject({ path: "public/logo.png", binary: true, lines: 0 });
  });

  it("drops the last file of a truncated diff, which may be cut mid-hunk", () => {
    const cut = MODIFIED + ADDED.slice(0, 60);
    const parsed = splitDiff(cut, true);
    expect(parsed.files.map((f) => f.path)).toEqual(["src/app/page.tsx"]);
    expect(parsed.droppedPartial).toBe(true);
  });

  it("handles an empty diff and a diff GitHub refused to send", () => {
    expect(splitDiff("").files).toEqual([]);
    expect(splitDiff("", true)).toMatchObject({ files: [], droppedPartial: false });
  });

  it("decodes quoted paths", () => {
    const quoted = [
      'diff --git "a/caf\\303\\251 menu.txt" "b/caf\\303\\251 menu.txt"',
      "index 1..2 100644",
      '--- "a/caf\\303\\251 menu.txt"',
      '+++ "b/caf\\303\\251 menu.txt"',
      "@@ -1 +1 @@",
      "-a",
      "+b",
      "",
    ].join("\n");
    expect(splitDiff(quoted).files[0]!.path).toBe("café menu.txt");
  });
});

describe("unquoteGitPath", () => {
  it("leaves plain paths alone and decodes escapes", () => {
    expect(unquoteGitPath("src/a.ts")).toBe("src/a.ts");
    expect(unquoteGitPath('"a\\tb\\"c\\\\d"')).toBe('a\tb"c\\d');
  });
});

function file(overrides: Partial<DiffFile>): DiffFile {
  return {
    key: overrides.path ?? "f",
    path: "src/f.ts",
    previousPath: null,
    status: "modified",
    binary: false,
    additions: 1,
    deletions: 1,
    lines: 10,
    hunks: 1,
    text: "",
    ...overrides,
  };
}

describe("initiallyCollapsed", () => {
  it("collapses generated and large files", () => {
    const files = [
      file({ key: "a", path: "src/a.ts" }),
      file({ key: "lock", path: "web/package-lock.json", lines: 40 }),
      file({ key: "big", path: "src/big.ts", lines: LARGE_FILE_LINES + 1 }),
    ];
    expect([...initiallyCollapsed(files)]).toEqual(["lock", "big"]);
  });

  it("collapses everything after the page holds its row budget", () => {
    const per = LARGE_FILE_LINES; // the largest file that still opens by default
    const fit = Math.floor(OPEN_ROWS_BUDGET / per);
    const keys = Array.from({ length: fit + 2 }, (_, i) => `f${i}`);
    const files = keys.map((k) => file({ key: k, path: `${k}.ts`, lines: per }));
    expect([...initiallyCollapsed(files)]).toEqual(keys.slice(fit));
  });

  it("still opens a small file that fits after a large one was skipped", () => {
    const files = [
      file({ key: "big", lines: LARGE_FILE_LINES + 1 }),
      file({ key: "small", lines: 5 }),
    ];
    expect([...initiallyCollapsed(files)]).toEqual(["big"]);
  });

  it("never lists files that have nothing to open", () => {
    const files = [file({ key: "bin", binary: true, lines: 0 }), file({ key: "mv", lines: 0 })];
    expect(initiallyCollapsed(files).size).toBe(0);
  });
});

describe("helpers", () => {
  it("recognises lockfiles and bundles", () => {
    expect(isGenerated("pnpm-lock.yaml")).toBe(true);
    expect(isGenerated("app/dist/app.min.js")).toBe(true);
    expect(isGenerated("src/lock.ts")).toBe(false);
  });

  it("links to the pull request's Files tab", () => {
    expect(prFilesUrl("https://github.com/a/b/pull/7")).toBe("https://github.com/a/b/pull/7/files");
    expect(prFilesUrl("https://github.com/a/b/pull/7/")).toBe("https://github.com/a/b/pull/7/files");
    expect(prFilesUrl(null)).toBeNull();
  });

  it("words file statuses", () => {
    expect(fileStatusWord("renamed")).toBe("Renamed");
  });

  it("estimates an opened file's height from its rows and hunks", () => {
    expect(estimatedHeight({ lines: 0, hunks: 0 })).toBe(0);
    expect(estimatedHeight({ lines: 100, hunks: 2 })).toBeGreaterThan(estimatedHeight({ lines: 100, hunks: 1 }));
    expect(estimatedHeight({ lines: 400, hunks: 1 })).toBeGreaterThan(7000);
  });
});
