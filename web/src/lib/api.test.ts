import { describe, expect, it } from "vitest";
import { fetchHealth, fetchTicketDetail, isTicketId } from "./api";

function fakeFetch(status: number, body: unknown): typeof fetch {
  return (async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    })) as unknown as typeof fetch;
}

describe("fetchHealth", () => {
  it("returns the parsed body on 200", async () => {
    const body = { status: "ok", version: "0.1.0", checks: { db: "ok", redis: "ok" } };
    expect(await fetchHealth(fakeFetch(200, body))).toEqual(body);
  });

  it("returns the body on 503 so the UI can show which check failed", async () => {
    const body = { status: "error", version: "0.1.0", checks: { db: "ok", redis: "error" } };
    expect(await fetchHealth(fakeFetch(503, body))).toEqual(body);
  });

  it("returns null when the API is unreachable", async () => {
    const failing = (async () => {
      throw new Error("ECONNREFUSED");
    }) as unknown as typeof fetch;
    expect(await fetchHealth(failing)).toBeNull();
  });
});

describe("fetchTicketDetail", () => {
  const ID = "0b8f3c1e-5d2a-4e7b-9c61-2f4a8d9e0b13";

  it("returns the ticket", async () => {
    const ticket = { id: ID, title: "x", runs: [] };
    expect(await fetchTicketDetail(ID, fakeFetch(200, ticket))).toEqual({ ticket });
  });

  it("tells a missing ticket apart from an API it couldn't ask", async () => {
    expect(await fetchTicketDetail(ID, fakeFetch(404, { detail: "not found" }))).toEqual({
      error: "not_found",
    });
    expect(await fetchTicketDetail(ID, fakeFetch(500, {}))).toEqual({ error: "unavailable" });
    const failing = (async () => {
      throw new Error("ECONNREFUSED");
    }) as unknown as typeof fetch;
    expect(await fetchTicketDetail(ID, failing)).toEqual({ error: "unavailable" });
  });

  it("doesn't ask the API about ids that can't be tickets", async () => {
    let asked = false;
    const spy = (async () => {
      asked = true;
      return new Response("{}");
    }) as unknown as typeof fetch;
    expect(await fetchTicketDetail("../repos", spy)).toEqual({ error: "not_found" });
    expect(asked).toBe(false);
    expect(isTicketId(ID)).toBe(true);
    expect(isTicketId("t1")).toBe(false);
  });
});
