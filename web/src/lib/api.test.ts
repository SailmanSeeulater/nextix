import { describe, expect, it } from "vitest";
import { fetchHealth } from "./api";

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
