import { describe, expect, it } from "vitest";
import { upstreamUrl } from "./proxy-path";

const BASE = "http://api:8010";
const href = (segments: string[]) => upstreamUrl(segments, BASE)?.href ?? null;

describe("upstreamUrl", () => {
  it("forwards ordinary API paths under /api", () => {
    expect(href(["tickets"])).toBe("http://api:8010/api/tickets");
    expect(href(["tickets", "abc", "runs"])).toBe("http://api:8010/api/tickets/abc/runs");
    expect(href(["artifacts", "r1", "shot.png"])).toBe("http://api:8010/api/artifacts/r1/shot.png");
  });

  it("re-encodes decoded segments so they stay one path segment", () => {
    expect(href(["tickets", "a b"])).toBe("http://api:8010/api/tickets/a%20b");
    expect(href(["tickets", "a?x=1#y"])).toBe("http://api:8010/api/tickets/a%3Fx%3D1%23y");
    expect(href(["tickets", "100%"])).toBe("http://api:8010/api/tickets/100%25");
  });

  it("refuses the blocked prefixes, in any case", () => {
    expect(href(["github", "webhook"])).toBeNull();
    expect(href(["github"])).toBeNull();
    expect(href(["internal", "runs", "r1", "heartbeat"])).toBeNull();
    expect(href(["GitHub", "webhook"])).toBeNull();
    expect(href(["githubby"])).toBe("http://api:8010/api/githubby");
  });

  it("refuses empty, dot and dot-dot segments, decoded or not", () => {
    expect(href([])).toBeNull();
    expect(href(["tickets", ""])).toBeNull();
    expect(href(["tickets", ".", "x"])).toBeNull();
    expect(href(["tickets", "..", "github", "webhook"])).toBeNull();
    expect(href(["tickets", "%2e%2e", "internal"])).toBeNull();
    expect(href(["tickets", "%2E"])).toBeNull();
  });

  it("refuses slashes inside a segment, encoded or not", () => {
    expect(href(["tickets", "a/b"])).toBeNull();
    expect(href(["tickets", "a%2fb"])).toBeNull();
    expect(href(["tickets", "a%2Fb"])).toBeNull();
    expect(href(["tickets", "a\\b"])).toBeNull();
    expect(href(["tickets", "a%5cb"])).toBeNull();
  });
});
