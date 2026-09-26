import { describe, expect, it } from "vitest";
import {
  buildTranscript,
  formatToolInput,
  hasAgentOutput,
  lastEventId,
  latestUsage,
  mergeEvents,
  oneLine,
  parseRunEvent,
  summarizeToolInput,
  toParagraphs,
  toRunEvent,
  type ToolItem,
} from "./transcript";
import type { RunEvent } from "./types";

let nextId = 1;
function ev(kind: string, payload: Record<string, unknown> = {}, id = nextId++): RunEvent {
  return { id, ts: "2026-09-25T10:00:00Z", kind, payload };
}

const ids = (events: RunEvent[]) => events.map((e) => e.id);

describe("mergeEvents", () => {
  const a = ev("log", { text: "a" }, 1);
  const b = ev("log", { text: "b" }, 2);
  const c = ev("log", { text: "c" }, 3);

  it("appends events that come after everything we have", () => {
    expect(ids(mergeEvents([a], [b, c]))).toEqual([1, 2, 3]);
  });

  it("drops ids it already has, so a stream replay never duplicates lines", () => {
    const current = [a, b, c];
    expect(ids(mergeEvents(current, [a, b, c]))).toEqual([1, 2, 3]);
  });

  it("returns the same array when nothing is new, so React can skip the render", () => {
    const current = [a, b];
    expect(mergeEvents(current, [a, b])).toBe(current);
    expect(mergeEvents(current, [])).toBe(current);
  });

  it("dedupes within one batch and sorts out-of-order arrivals by id", () => {
    expect(ids(mergeEvents([b], [c, a, c, b]))).toEqual([1, 2, 3]);
  });

  it("handles a reconnect: history, then a full replay, then one live event", () => {
    const history = mergeEvents([], [a, b]);
    const replayed = mergeEvents(history, [a, b]);
    expect(replayed).toBe(history);
    expect(ids(mergeEvents(replayed, [a, b, c]))).toEqual([1, 2, 3]);
  });

  it("reports the last id for the next history page", () => {
    expect(lastEventId([])).toBeNull();
    expect(lastEventId([a, b])).toBe(2);
  });
});

describe("parseRunEvent", () => {
  it("parses a RunEvent from SSE data", () => {
    const e = parseRunEvent('{"id":7,"ts":"t","kind":"log","payload":{"text":"hi"}}');
    expect(e).toEqual({ id: 7, ts: "t", kind: "log", payload: { text: "hi" } });
  });

  it("rejects malformed data instead of throwing", () => {
    expect(parseRunEvent("not json")).toBeNull();
    expect(parseRunEvent('{"kind":"log"}')).toBeNull();
    expect(parseRunEvent('{"id":"1","kind":"log"}')).toBeNull();
    expect(parseRunEvent("null")).toBeNull();
  });

  it("gives a missing payload an empty object", () => {
    expect(toRunEvent({ id: 1, kind: "log" })?.payload).toEqual({});
  });
});

describe("buildTranscript", () => {
  it("pairs each tool_result with its tool_use by tool_use_id", () => {
    const items = buildTranscript([
      ev("tool_use", { id: "tu_1", name: "Bash", input: { command: "npm test" } }, 1),
      ev("tool_use", { id: "tu_2", name: "Read", input: { file_path: "src/a.ts" } }, 2),
      ev("tool_result", { tool_use_id: "tu_2", content: "file body", is_error: false }, 3),
      ev("tool_result", { tool_use_id: "tu_1", content: "1 failed", is_error: true }, 4),
    ]);
    expect(items).toHaveLength(2);
    const [bash, read] = items as [ToolItem, ToolItem];
    expect(bash).toMatchObject({ name: "Bash", summary: "npm test" });
    expect(bash.result).toEqual({ id: 4, content: "1 failed", isError: true });
    expect(read.result).toEqual({ id: 3, content: "file body", isError: false });
  });

  it("leaves a call without a result pending", () => {
    const [item] = buildTranscript([ev("tool_use", { id: "x", name: "Grep", input: {} }, 1)]);
    expect((item as ToolItem).result).toBeNull();
  });

  it("shows a result whose call never arrived on its own", () => {
    const [item] = buildTranscript([
      ev("tool_result", { tool_use_id: "ghost", content: "out", is_error: false }, 1),
    ]);
    expect(item).toMatchObject({ type: "tool", orphan: true, name: "Tool result" });
    expect((item as ToolItem).result?.content).toBe("out");
  });

  it("keeps the first result when a call gets two", () => {
    const items = buildTranscript([
      ev("tool_use", { id: "t", name: "Bash", input: { command: "ls" } }, 1),
      ev("tool_result", { tool_use_id: "t", content: "first" }, 2),
      ev("tool_result", { tool_use_id: "t", content: "second" }, 3),
    ]);
    expect(items).toHaveLength(1);
    expect((items[0] as ToolItem).result?.content).toBe("first");
  });

  it("renders messages, logs, errors and state changes in order, and leaves usage out", () => {
    const items = buildTranscript([
      ev("state", { status: "running" }, 1),
      ev("log", { text: "Cloning…" }, 2),
      ev("message", { text: "Looking at the tests.\n\nThen fixing them." }, 3),
      ev("usage", { input_tokens: 10, output_tokens: 5, cost_usd: 0.01 }, 4),
      ev("error", { text: "Out of turns" }, 5),
      ev("heartbeat", {}, 6),
      ev("something_new", { text: "?" }, 7),
    ]);
    expect(items.map((i) => i.type)).toEqual(["state", "log", "message", "error"]);
    expect(items[2]).toMatchObject({
      paragraphs: ["Looking at the tests.", "Then fixing them."],
    });
  });

  it("skips empty messages and log lines, and words an empty error", () => {
    const items = buildTranscript([
      ev("message", { text: "  \n\n " }, 1),
      ev("log", { text: "" }, 2),
      ev("error", {}, 3),
    ]);
    expect(items).toEqual([
      { type: "error", id: 3, ts: "2026-09-25T10:00:00Z", text: "The runner reported an error." },
    ]);
  });

  it("does not break on payloads of the wrong shape", () => {
    const items = buildTranscript([
      ev("tool_use", { id: 5, name: null, input: "raw" }, 1),
      ev("message", { text: 42 }, 2),
    ]);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ type: "tool", name: "Tool", summary: "raw", toolUseId: "" });
  });
});

describe("hasAgentOutput", () => {
  it("reads a run with only state dividers as empty (a queued run already has one)", () => {
    // The API stores {"from": null, "status": "queued"} when it queues every run.
    const queued = buildTranscript([ev("state", { from: null, status: "queued" }, 1)]);
    expect(queued).toHaveLength(1);
    expect(hasAgentOutput(queued)).toBe(false);
    expect(hasAgentOutput([])).toBe(false);
  });

  it("counts anything the runner or agent recorded", () => {
    for (const e of [
      ev("log", { text: "Cloning…" }, 2),
      ev("message", { text: "Looking." }, 2),
      ev("tool_use", { id: "t", name: "Read", input: {} }, 2),
      ev("error", { text: "boom" }, 2),
    ]) {
      const items = buildTranscript([ev("state", { status: "claimed" }, 1), e]);
      expect(hasAgentOutput(items), e.kind).toBe(true);
    }
  });
});

describe("latestUsage", () => {
  it("returns the newest cumulative usage", () => {
    expect(
      latestUsage([
        ev("usage", { input_tokens: 10, output_tokens: 2, cost_usd: 0.01 }, 1),
        ev("message", { text: "hi" }, 2),
        ev("usage", { input_tokens: 30, output_tokens: 9, cost_usd: "0.05" }, 3),
      ]),
    ).toEqual({ input_tokens: 30, output_tokens: 9, cost_usd: 0.05 });
  });

  it("returns null before the first usage event", () => {
    expect(latestUsage([ev("log", { text: "x" })])).toBeNull();
  });

  it("reads missing or junk numbers as zero", () => {
    expect(latestUsage([ev("usage", { input_tokens: "lots" }, 1)])).toEqual({
      input_tokens: 0,
      output_tokens: 0,
      cost_usd: 0,
    });
  });
});

describe("summarizeToolInput", () => {
  it("shows the Bash command", () => {
    expect(summarizeToolInput("Bash", { command: "npm run lint", description: "Lint" })).toBe(
      "npm run lint",
    );
  });

  it("marks a multi-line command as continued", () => {
    expect(summarizeToolInput("Bash", { command: "cd web\nnpm test" })).toBe("cd web …");
  });

  it("shows the file path for file tools", () => {
    for (const name of ["Read", "Write", "Edit", "MultiEdit"]) {
      expect(summarizeToolInput(name, { file_path: "/work/src/app.ts", content: "x" })).toBe(
        "/work/src/app.ts",
      );
    }
    expect(summarizeToolInput("NotebookEdit", { notebook_path: "a.ipynb" })).toBe("a.ipynb");
  });

  it("shows search patterns and where they look", () => {
    expect(summarizeToolInput("Glob", { pattern: "**/*.ts", path: "src" })).toBe("**/*.ts in src");
    expect(summarizeToolInput("Glob", { pattern: "*.md" })).toBe("*.md");
    expect(summarizeToolInput("Grep", { pattern: "TODO", path: "web" })).toBe("“TODO” in web");
  });

  it("covers web, agent and to-do tools", () => {
    expect(summarizeToolInput("WebFetch", { url: "https://example.com" })).toBe(
      "https://example.com",
    );
    expect(summarizeToolInput("WebSearch", { query: "vitest" })).toBe("vitest");
    expect(summarizeToolInput("Task", { description: "Find usages", prompt: "…" })).toBe(
      "Find usages",
    );
    expect(summarizeToolInput("TodoWrite", { todos: [{}, {}] })).toBe("2 to-dos");
    expect(summarizeToolInput("TodoWrite", { todos: [{}] })).toBe("1 to-do");
  });

  it("falls back to a known key, then the first string, then compact JSON", () => {
    expect(summarizeToolInput("mcp__x__do", { limit: 3, path: "docs" })).toBe("docs");
    expect(summarizeToolInput("mcp__x__do", { limit: 3, name: "thing" })).toBe("thing");
    expect(summarizeToolInput("mcp__x__do", { limit: 3 })).toBe('{"limit":3}');
    expect(summarizeToolInput("mcp__x__do", {})).toBe("");
    expect(summarizeToolInput("Bash", null)).toBe("");
  });

  it("cuts long lines with an ellipsis", () => {
    const summary = summarizeToolInput("Bash", { command: "x".repeat(400) });
    expect(summary.length).toBe(160);
    expect(summary.endsWith("…")).toBe(true);
  });
});

describe("oneLine", () => {
  it("collapses whitespace and skips leading blank lines", () => {
    expect(oneLine("\n\n  git   status  ")).toBe("git status");
  });
});

describe("formatToolInput", () => {
  it("pretty-prints the full input", () => {
    expect(formatToolInput({ command: "ls" })).toBe('{\n  "command": "ls"\n}');
    expect(formatToolInput("raw")).toBe("raw");
    expect(formatToolInput(null)).toBe("");
  });
});

describe("toParagraphs", () => {
  it("splits on blank lines and keeps single newlines inside a paragraph", () => {
    expect(toParagraphs("One\ntwo\n\n\nThree\n  \nFour  ")).toEqual(["One\ntwo", "Three", "Four"]);
  });

  it("keeps indentation at the start of a paragraph", () => {
    expect(toParagraphs("Steps:\n\n  1. run")).toEqual(["Steps:", "  1. run"]);
  });
});
