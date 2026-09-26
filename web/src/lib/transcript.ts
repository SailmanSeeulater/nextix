/**
 * Pure transcript logic: merging events from history pages and the live stream
 * (deduped by event id), pairing each tool result with its tool call, and the
 * one-line summaries shown on collapsed tool rows.
 */
import type { RunEvent } from "./types";

export interface ToolResult {
  /** The tool_result event's id. */
  id: number;
  content: string;
  isError: boolean;
}

export interface ToolItem {
  type: "tool";
  /** The tool_use event's id (or the tool_result's, for an orphan). */
  id: number;
  ts: string;
  toolUseId: string;
  name: string;
  input: unknown;
  summary: string;
  result: ToolResult | null;
  /** A result whose tool call never arrived; shown on its own. */
  orphan: boolean;
}

export type TranscriptItem =
  | { type: "message"; id: number; ts: string; paragraphs: string[] }
  | ToolItem
  | { type: "log"; id: number; ts: string; text: string }
  | { type: "error"; id: number; ts: string; text: string }
  | { type: "state"; id: number; ts: string; status: string };

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function num(value: unknown): number {
  const n = typeof value === "string" ? Number(value) : value;
  return typeof n === "number" && Number.isFinite(n) ? n : 0;
}

/** Validate one event from the wire; null for anything that isn't a RunEvent. */
export function toRunEvent(value: unknown): RunEvent | null {
  if (!isRecord(value)) return null;
  const { id, ts, kind, payload } = value;
  if (typeof id !== "number" || !Number.isFinite(id) || typeof kind !== "string") return null;
  return {
    id,
    ts: typeof ts === "string" ? ts : "",
    kind,
    payload: isRecord(payload) ? payload : {},
  };
}

/** Parse an SSE `data:` line carrying a RunEvent; null when it isn't one. */
export function parseRunEvent(data: string): RunEvent | null {
  try {
    return toRunEvent(JSON.parse(data));
  } catch {
    return null;
  }
}

export function lastEventId(events: readonly RunEvent[]): number | null {
  return events.length ? (events[events.length - 1] as RunEvent).id : null;
}

/**
 * Add `incoming` to `current` (sorted by id), dropping ids already present, so a
 * stream replay or a reconnect never duplicates a line. Returns `current` itself
 * when nothing is new, so React can skip the render.
 */
export function mergeEvents(current: readonly RunEvent[], incoming: readonly RunEvent[]): RunEvent[] {
  // Fast path: live events arrive in order after everything we have.
  let tail = lastEventId(current) ?? -Infinity;
  const appended: RunEvent[] = [];
  let inOrder = true;
  for (const event of incoming) {
    if (event.id > tail) {
      appended.push(event);
      tail = event.id;
    } else {
      inOrder = false;
      break;
    }
  }
  if (inOrder) return appended.length ? [...current, ...appended] : (current as RunEvent[]);

  const byId = new Map<number, RunEvent>();
  for (const event of current) byId.set(event.id, event);
  let added = false;
  for (const event of incoming) {
    if (!byId.has(event.id)) {
      byId.set(event.id, event);
      added = true;
    }
  }
  if (!added) return current as RunEvent[];
  return [...byId.values()].sort((a, b) => a.id - b.id);
}

/** Assistant text as paragraphs: blank lines split them, single newlines stay inside. */
export function toParagraphs(value: string): string[] {
  return value
    .split(/\n[ \t]*\n+/)
    .map((p) => p.replace(/^\n+|\s+$/g, ""))
    .filter((p) => p.trim().length > 0);
}

/**
 * Build the rendered transcript: messages, tool calls (each paired with its result),
 * runner log lines, errors and state dividers. `usage` events are left out; they feed
 * the header readout instead (see latestUsage).
 */
export function buildTranscript(events: readonly RunEvent[]): TranscriptItem[] {
  const items: TranscriptItem[] = [];
  const tools = new Map<string, ToolItem>();
  for (const e of events) {
    const p = e.payload;
    switch (e.kind) {
      case "message": {
        const paragraphs = toParagraphs(text(p.text));
        if (paragraphs.length) items.push({ type: "message", id: e.id, ts: e.ts, paragraphs });
        break;
      }
      case "tool_use": {
        const name = text(p.name) || "Tool";
        const item: ToolItem = {
          type: "tool",
          id: e.id,
          ts: e.ts,
          toolUseId: text(p.id),
          name,
          input: p.input ?? null,
          summary: summarizeToolInput(name, p.input),
          result: null,
          orphan: false,
        };
        items.push(item);
        if (item.toolUseId && !tools.has(item.toolUseId)) tools.set(item.toolUseId, item);
        break;
      }
      case "tool_result": {
        const toolUseId = text(p.tool_use_id);
        const result: ToolResult = {
          id: e.id,
          content: text(p.content),
          isError: p.is_error === true,
        };
        const call = toolUseId ? tools.get(toolUseId) : undefined;
        if (call && call.result === null) {
          call.result = result;
        } else if (!call) {
          items.push({
            type: "tool",
            id: e.id,
            ts: e.ts,
            toolUseId,
            name: "Tool result",
            input: null,
            summary: "",
            result,
            orphan: true,
          });
        }
        break;
      }
      case "log": {
        const line = text(p.text).trim();
        if (line) items.push({ type: "log", id: e.id, ts: e.ts, text: line });
        break;
      }
      case "error":
        items.push({
          type: "error",
          id: e.id,
          ts: e.ts,
          text: text(p.text).trim() || "The runner reported an error.",
        });
        break;
      case "state": {
        const status = text(p.status);
        if (status) items.push({ type: "state", id: e.id, ts: e.ts, status });
        break;
      }
      default:
        // usage feeds the header; heartbeats aren't stored; unknown kinds are skipped.
        break;
    }
  }
  return items;
}

/**
 * True once the transcript holds more than state dividers. Every run records its own
 * state changes (a queued run already has "Queued"), so those alone still read as empty.
 */
export function hasAgentOutput(items: readonly TranscriptItem[]): boolean {
  return items.some((item) => item.type !== "state");
}

/** The newest cumulative usage in the transcript, or null before the first one. */
export function latestUsage(events: readonly RunEvent[]): Usage | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i] as RunEvent;
    if (e.kind !== "usage") continue;
    return {
      input_tokens: num(e.payload.input_tokens),
      output_tokens: num(e.payload.output_tokens),
      cost_usd: num(e.payload.cost_usd),
    };
  }
  return null;
}

const SUMMARY_MAX = 160;

/** One line, whitespace collapsed, cut at `max` characters with an ellipsis. */
export function oneLine(value: string, max = SUMMARY_MAX): string {
  const lines = value.split("\n").map((l) => l.trim()).filter(Boolean);
  let line = (lines[0] ?? "").replace(/\s+/g, " ");
  if (lines.length > 1) line += " …";
  return line.length > max ? `${line.slice(0, max - 1).trimEnd()}…` : line;
}

function inPath(base: string, path: string): string {
  return path ? `${base} in ${path}` : base;
}

/**
 * The one-line summary on a collapsed tool row: the Bash command, the file path,
 * the search pattern. Unknown tools fall back to their first string argument, then
 * to compact JSON.
 */
export function summarizeToolInput(name: string, input: unknown): string {
  if (!isRecord(input)) return typeof input === "string" ? oneLine(input) : "";
  const s = (key: string) => text(input[key]);
  switch (name) {
    case "Bash":
      return oneLine(s("command") || s("description"));
    case "Read":
    case "Write":
    case "Edit":
    case "MultiEdit":
      return oneLine(s("file_path"));
    case "NotebookEdit":
      return oneLine(s("notebook_path") || s("file_path"));
    case "Glob":
      return oneLine(inPath(s("pattern"), s("path")));
    case "Grep":
      return oneLine(inPath(s("pattern") ? `“${s("pattern")}”` : "", s("path") || s("glob")));
    case "WebFetch":
      return oneLine(s("url"));
    case "WebSearch":
      return oneLine(s("query"));
    case "Task":
    case "Agent":
      return oneLine(s("description") || s("prompt"));
    case "TodoWrite": {
      const todos = input.todos;
      if (Array.isArray(todos)) return todos.length === 1 ? "1 to-do" : `${todos.length} to-dos`;
      return "";
    }
    default: {
      for (const key of ["command", "file_path", "path", "pattern", "url", "query", "description"]) {
        if (s(key)) return oneLine(s(key));
      }
      const first = Object.values(input).find((v): v is string => typeof v === "string" && v.trim() !== "");
      if (first) return oneLine(first);
      const json = JSON.stringify(input);
      return json && json !== "{}" ? oneLine(json) : "";
    }
  }
}

/** The full tool input for the expanded row. */
export function formatToolInput(input: unknown): string {
  if (input === null || input === undefined) return "";
  if (typeof input === "string") return input;
  try {
    return JSON.stringify(input, null, 2);
  } catch {
    return String(input);
  }
}
