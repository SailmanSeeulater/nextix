import { describe, expect, it } from "vitest";
import { ApiError, createTicket, describeError, parseLabels } from "./client-api";

function respond(status: number, body: unknown): typeof fetch {
  return (async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    })) as unknown as typeof fetch;
}

describe("describeError", () => {
  it("uses the API's own sentence", async () => {
    const res = new Response(JSON.stringify({ detail: "repo not connected" }), { status: 404 });
    expect(await describeError(res)).toBe("repo not connected");
  });

  it("names the field in a validation error", async () => {
    const res = new Response(
      JSON.stringify({ detail: [{ loc: ["body", "prompt"], msg: "too short" }] }),
      { status: 422 },
    );
    expect(await describeError(res)).toBe("Check the prompt: too short.");
  });

  it("explains an expired session and an unavailable API", async () => {
    expect(await describeError(new Response("", { status: 401 }))).toMatch(/Sign in again/);
    expect(await describeError(new Response("", { status: 503 }))).toMatch(/unavailable/);
  });
});

describe("createTicket", () => {
  it("posts as the web composer and returns the result", async () => {
    let sent: RequestInit | undefined;
    const fake = (async (_url: string, init?: RequestInit) => {
      sent = init;
      return new Response(JSON.stringify({ needs_input: false }), { status: 201 });
    }) as unknown as typeof fetch;
    await createTicket({ repo: "a/b", prompt: "add x", labels: ["ui"], triage: true }, fake);
    expect(JSON.parse(String(sent?.body))).toEqual({
      repo: "a/b",
      prompt: "add x",
      labels: ["ui"],
      triage: true,
      created_via: "web",
    });
  });

  it("throws a readable ApiError on failure", async () => {
    await expect(
      createTicket({ repo: "a/b", prompt: "x", labels: [], triage: true }, respond(502, { detail: "Triage failed" })),
    ).rejects.toEqual(new ApiError("Triage failed"));
  });

  it("explains an unreachable server", async () => {
    const down = (async () => {
      throw new TypeError("fetch failed");
    }) as unknown as typeof fetch;
    await expect(
      createTicket({ repo: "a/b", prompt: "x", labels: [], triage: true }, down),
    ).rejects.toThrow(/Can't reach nexTix/);
  });
});

describe("parseLabels", () => {
  it("splits, trims and de-duplicates", () => {
    expect(parseLabels(" ui, p1 ,,ui ")).toEqual(["ui", "p1"]);
  });
});
