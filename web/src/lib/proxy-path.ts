/**
 * Which API paths the same-origin proxy (src/app/api/[...path]/route.ts) forwards, and
 * where. Next hands the route its catch-all segments already percent-decoded, so each is
 * checked both as given and decoded once more (a double-encoded `%252e%252e`), then
 * re-encoded for the upstream URL so a decoded `?`, `#` or `/` can't change its shape.
 */

// Never reachable from the browser: GitHub and sandboxes call the API directly.
const BLOCKED_PREFIXES = ["github/", "internal/"];

function unsafeSegment(segment: string): boolean {
  let decoded = segment;
  try {
    decoded = decodeURIComponent(segment);
  } catch {
    // A lone `%` isn't an encoding of anything; the segment is checked as it stands.
  }
  return [segment, decoded].some(
    (s) => s === "" || s === "." || s === ".." || /[\/\\]|%2f|%5c/i.test(s),
  );
}

function blocked(path: string): boolean {
  const lower = `${path.toLowerCase()}/`;
  return BLOCKED_PREFIXES.some((p) => lower.startsWith(p));
}

/**
 * The upstream URL for a proxied request, or null when the path must not be forwarded:
 * an empty, `.` or `..` segment, an encoded slash, or a blocked prefix, checked on the
 * segments and again on the normalized pathname of the URL actually fetched.
 */
export function upstreamUrl(segments: readonly string[], base: string): URL | null {
  if (segments.length === 0 || segments.some(unsafeSegment)) return null;
  if (blocked(segments.join("/"))) return null;
  const url = new URL(`/api/${segments.map(encodeURIComponent).join("/")}`, base);
  if (!url.pathname.startsWith("/api/")) return null;
  const rest = url.pathname.slice("/api/".length);
  if (rest === "" || blocked(rest)) return null;
  return url;
}
