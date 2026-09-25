/**
 * Web session for the single-user MVP.
 *
 * The cookie holds HMAC-SHA256(key = NEXTIX_API_TOKEN, "nextix-web-session"),
 * never the token itself. Rotating the token logs everyone out. This is the
 * seam to replace when GitHub OAuth is added.
 */

export const SESSION_COOKIE = "nextix_session";
export const SESSION_MAX_AGE_S = 60 * 60 * 24 * 30;

const encoder = new TextEncoder();

function toHex(buf: ArrayBuffer): string {
  return Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");
}

export async function sessionValue(token: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(token),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return toHex(await crypto.subtle.sign("HMAC", key, encoder.encode("nextix-web-session")));
}

/** Constant-time string comparison. */
export function safeEqual(a: string, b: string): boolean {
  const ab = encoder.encode(a);
  const bb = encoder.encode(b);
  let diff = ab.length ^ bb.length;
  for (let i = 0; i < Math.max(ab.length, bb.length); i++) {
    diff |= (ab[i] ?? 0) ^ (bb[i] ?? 0);
  }
  return diff === 0;
}

export async function isValidSession(cookie: string | undefined): Promise<boolean> {
  const token = process.env.NEXTIX_API_TOKEN;
  if (!token || !cookie) return false;
  return safeEqual(cookie, await sessionValue(token));
}
