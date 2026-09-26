import { describe, expect, it } from "vitest";
import {
  ApiError,
  cancelRun,
  createTicket,
  describeError,
  fetchArtifactText,
  fetchCosts,
  fetchTicketDiff,
  loadRunHistory,
  loadTicketDetail,
  parseLabels,
  rerunTicket,
  retryTicket,
} from "./client-api";

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

describe("describeError fallbacks", () => {
  it("words a status for this call only when the API gave no sentence", async () => {
    const bare = new Response("", { status: 409 });
    expect(await describeError(bare, { 409: "Already running." })).toBe("Already running.");
    const worded = new Response(JSON.stringify({ detail: "run abc is active" }), { status: 409 });
    expect(await describeError(worded, { 409: "Already running." })).toBe("run abc is active");
  });
});

describe("retryTicket", () => {
  it("posts to the ticket's runs and returns the new run", async () => {
    let called: { url: string; method?: string } | undefined;
    const fake = (async (url: string, init?: RequestInit) => {
      called = { url, method: init?.method };
      return new Response(JSON.stringify({ id: "r2", attempt: 2, status: "queued" }), { status: 201 });
    }) as unknown as typeof fetch;
    const run = await retryTicket("t1", fake);
    expect(called).toEqual({ url: "/api/tickets/t1/runs", method: "POST" });
    expect(run).toMatchObject({ id: "r2", status: "queued" });
  });

  it("explains a 409 when a run is already active", async () => {
    await expect(retryTicket("t1", respond(409, {}))).rejects.toThrow(/already active/);
  });

  it("explains an unreachable server", async () => {
    const down = (async () => {
      throw new TypeError("fetch failed");
    }) as unknown as typeof fetch;
    await expect(retryTicket("t1", down)).rejects.toThrow(/Can't reach nexTix/);
  });
});

describe("cancelRun", () => {
  it("posts to the run's cancel endpoint and accepts 202", async () => {
    let url = "";
    const fake = (async (u: string) => {
      url = u;
      return new Response(null, { status: 202 });
    }) as unknown as typeof fetch;
    await expect(cancelRun("r1", fake)).resolves.toBeNull();
    expect(url).toBe("/api/runs/r1/cancel");
  });

  it("returns the updated run when the API sends it", async () => {
    const body = { id: "r1", status: "cancelled", exit_reason: "cancelled" };
    await expect(cancelRun("r1", respond(202, body))).resolves.toMatchObject(body);
  });

  it("explains a run that already finished", async () => {
    await expect(cancelRun("r1", respond(410, {}))).rejects.toThrow(/already finished/);
  });
});

describe("loadRunHistory", () => {
  function pages(bodies: Record<string, unknown>, seen: string[] = []): typeof fetch {
    return (async (url: string) => {
      seen.push(url);
      const after = new URL(url, "http://x").searchParams.get("after") ?? "start";
      const body = bodies[after];
      return body === undefined
        ? new Response("", { status: 500 })
        : new Response(JSON.stringify(body), { status: 200 });
    }) as unknown as typeof fetch;
  }
  const e = (id: number) => ({ id, ts: "t", kind: "log", payload: { text: String(id) } });

  it("follows next_after until the last page", async () => {
    const seen: string[] = [];
    const history = await loadRunHistory(
      "r1",
      null,
      pages(
        {
          start: { events: [e(1), e(2)], next_after: 2 },
          "2": { events: [e(3)], next_after: null },
        },
        seen,
      ),
    );
    expect(history.complete).toBe(true);
    expect(history.events.map((x) => x.id)).toEqual([1, 2, 3]);
    expect(seen[0]).toBe("/api/runs/r1/events?limit=500");
    expect(seen[1]).toBe("/api/runs/r1/events?limit=500&after=2");
  });

  it("starts after the last event it already has", async () => {
    const seen: string[] = [];
    await loadRunHistory("r1", 41, pages({ "41": { events: [], next_after: null } }, seen));
    expect(seen).toEqual(["/api/runs/r1/events?limit=500&after=41"]);
  });

  it("keeps what loaded when a later page fails", async () => {
    const history = await loadRunHistory(
      "r1",
      null,
      pages({ start: { events: [e(1)], next_after: 1 } }),
    );
    expect(history).toMatchObject({ complete: false, events: [e(1)] });
  });

  it("stops when the cursor doesn't advance", async () => {
    const history = await loadRunHistory(
      "r1",
      5,
      pages({ "5": { events: [], next_after: 5 } }),
    );
    expect(history.complete).toBe(true);
  });
});

describe("loadTicketDetail", () => {
  it("returns the ticket, or null when it can't", async () => {
    expect(await loadTicketDetail("t1", respond(200, { id: "t1", runs: [] }))).toMatchObject({
      id: "t1",
    });
    expect(await loadTicketDetail("t1", respond(404, { detail: "not found" }))).toBeNull();
  });
});

describe("fetchTicketDiff", () => {
  it("returns the diff from the ticket's diff endpoint", async () => {
    let url = "";
    const fake = (async (u: string) => {
      url = u;
      return new Response(
        JSON.stringify({ pr_number: 57, head_sha: "abc", diff: "diff --git a/x b/x\n", truncated: false }),
        { status: 200 },
      );
    }) as unknown as typeof fetch;
    expect(await fetchTicketDiff("t 1", fake)).toEqual({
      pr_number: 57,
      head_sha: "abc",
      diff: "diff --git a/x b/x\n",
      truncated: false,
    });
    expect(url).toBe("/api/tickets/t%201/diff");
  });

  it("means no pull request yet on 404", async () => {
    expect(await fetchTicketDiff("t1", respond(404, { detail: "this ticket has no pull request yet" }))).toBeNull();
  });

  it("throws a readable error when GitHub or the network fails", async () => {
    await expect(fetchTicketDiff("t1", respond(502, { detail: "GitHub didn't return the diff" }))).rejects.toThrow(
      "GitHub didn't return the diff",
    );
    await expect(fetchTicketDiff("t1", respond(502, {}))).rejects.toThrow(/Try again in a moment/);
    const offline = (async () => {
      throw new TypeError("fetch failed");
    }) as unknown as typeof fetch;
    await expect(fetchTicketDiff("t1", offline)).rejects.toBeInstanceOf(ApiError);
  });

  it("refuses a body that isn't a diff", async () => {
    await expect(fetchTicketDiff("t1", respond(200, { nope: true }))).rejects.toThrow(/shape/);
  });

  it("passes an abort through instead of calling it unreachable", async () => {
    const controller = new AbortController();
    controller.abort();
    const aborting = (async () => {
      throw new DOMException("aborted", "AbortError");
    }) as unknown as typeof fetch;
    await expect(fetchTicketDiff("t1", aborting, controller.signal)).rejects.toMatchObject({
      name: "AbortError",
    });
  });
});

describe("fetchArtifactText", () => {
  it("reads a stored artifact as text", async () => {
    const fake = (async () => new Response("PASS 12 tests\n", { status: 200 })) as unknown as typeof fetch;
    expect(await fetchArtifactText("/api/artifacts/a1", fake)).toBe("PASS 12 tests\n");
  });

  it("never fetches anything but an artifact path", async () => {
    let called = false;
    const fake = (async () => {
      called = true;
      return new Response("");
    }) as unknown as typeof fetch;
    await expect(fetchArtifactText("https://evil.example/x", fake)).rejects.toBeInstanceOf(ApiError);
    await expect(fetchArtifactText("/api/tickets", fake)).rejects.toBeInstanceOf(ApiError);
    expect(called).toBe(false);
  });

  it("explains a missing file", async () => {
    await expect(fetchArtifactText("/api/artifacts/a1", respond(404, {}))).rejects.toThrow(
      "This file is no longer stored.",
    );
  });
});

describe("parseLabels", () => {
  it("splits, trims and de-duplicates", () => {
    expect(parseLabels(" ui, p1 ,,ui ")).toEqual(["ui", "p1"]);
  });
});

describe("rerunTicket", () => {
  it("posts to the ticket's rerun endpoint and returns the new run", async () => {
    let called: { url: string; method?: string } | undefined;
    const fake = (async (url: string, init?: RequestInit) => {
      called = { url, method: init?.method };
      return new Response(JSON.stringify({ id: "r3", attempt: 3, status: "queued" }), {
        status: 201,
      });
    }) as unknown as typeof fetch;
    await expect(rerunTicket("t 1", fake)).resolves.toMatchObject({ id: "r3", status: "queued" });
    expect(called).toEqual({ url: "/api/tickets/t%201/rerun", method: "POST" });
  });

  it("prefers the API's sentence, and explains a closed issue or a queue that is down", async () => {
    await expect(
      rerunTicket("t1", respond(409, { detail: "the issue is closed; reopen it first" })),
    ).rejects.toThrow("the issue is closed; reopen it first");
    await expect(rerunTicket("t1", respond(409, {}))).rejects.toThrow(/Reopen it on GitHub/);
    await expect(rerunTicket("t1", respond(503, {}))).rejects.toThrow(/run queue/);
  });

  it("explains an unreachable server", async () => {
    const down = (async () => {
      throw new TypeError("fetch failed");
    }) as unknown as typeof fetch;
    await expect(rerunTicket("t1", down)).rejects.toThrow(/Can't reach nexTix/);
  });
});

describe("fetchCosts", () => {
  const body = {
    days: 7,
    estimated: true,
    total: { cost_usd: "2.5", input_tokens: 10, output_tokens: 5, runs: 2 },
    by_day: [{ date: "2026-09-26", cost_usd: 2.5, input_tokens: 10, output_tokens: 5, runs: 2 }],
    by_repo: [],
    by_ticket: [],
  };

  it("asks for the window and reads the report", async () => {
    let url = "";
    const fake = (async (u: string) => {
      url = u;
      return new Response(JSON.stringify(body), { status: 200 });
    }) as unknown as typeof fetch;
    const report = await fetchCosts(7, fake);
    expect(url).toBe("/api/costs?days=7");
    expect(report.total.cost_usd).toBe(2.5);
    expect(report.estimated).toBe(true);
  });

  it("explains errors and a body it can't read", async () => {
    await expect(fetchCosts(7, respond(503, {}))).rejects.toThrow(/unavailable/);
    await expect(fetchCosts(7, respond(200, { nope: true }))).rejects.toThrow(/shape/);
    const down = (async () => {
      throw new TypeError("fetch failed");
    }) as unknown as typeof fetch;
    await expect(fetchCosts(7, down)).rejects.toBeInstanceOf(ApiError);
  });

  it("lets an aborted request stay aborted", async () => {
    const controller = new AbortController();
    controller.abort();
    const fake = (async () => {
      throw new DOMException("aborted", "AbortError");
    }) as unknown as typeof fetch;
    await expect(fetchCosts(7, fake, controller.signal)).rejects.toThrow("aborted");
  });
});
