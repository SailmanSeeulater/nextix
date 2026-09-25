import { afterEach, describe, expect, it } from "vitest";
import { isValidSession, safeEqual, sessionValue } from "./session";

describe("session", () => {
  afterEach(() => {
    delete process.env.NEXTIX_API_TOKEN;
  });

  it("derives a stable value that is not the token", async () => {
    const v = await sessionValue("tok");
    expect(v).toMatch(/^[0-9a-f]{64}$/);
    expect(v).toBe(await sessionValue("tok"));
    expect(v).not.toContain("tok");
    expect(v).not.toBe(await sessionValue("other"));
  });

  it("validates only the cookie for the configured token", async () => {
    process.env.NEXTIX_API_TOKEN = "tok";
    expect(await isValidSession(await sessionValue("tok"))).toBe(true);
    expect(await isValidSession(await sessionValue("old"))).toBe(false);
    expect(await isValidSession(undefined)).toBe(false);
  });

  it("fails closed when no token is configured", async () => {
    expect(await isValidSession(await sessionValue("anything"))).toBe(false);
    expect(await isValidSession("")).toBe(false);
  });

  it("compares strings safely", () => {
    expect(safeEqual("abc", "abc")).toBe(true);
    expect(safeEqual("abc", "abd")).toBe(false);
    expect(safeEqual("abc", "abcd")).toBe(false);
  });
});
