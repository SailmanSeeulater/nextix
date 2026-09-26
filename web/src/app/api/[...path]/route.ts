/**
 * Same-origin proxy to the nexTix API. Adds the bearer token server-side and
 * streams the response body through untouched, so SSE works end to end.
 * The session check happens in src/proxy.ts before this runs.
 */
import type { NextRequest } from "next/server";
import { apiBaseUrl, authHeaders } from "@/lib/api";

export const dynamic = "force-dynamic";

// Never reachable from the browser: GitHub and sandboxes call the API directly.
const BLOCKED_PREFIXES = ["github/", "internal/"];
const FORWARD_REQUEST_HEADERS = ["accept", "content-type", "last-event-id"];
// nosniff keeps the browser from reading a stored test report or screenshot (untrusted
// run output served from /api/artifacts/*) as anything but its declared type.
const FORWARD_RESPONSE_HEADERS = ["content-type", "cache-control", "x-content-type-options"];

async function forward(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const path = (await params).path.join("/");
  if (BLOCKED_PREFIXES.some((p) => path.startsWith(p))) {
    return Response.json({ detail: "not found" }, { status: 404 });
  }

  const url = new URL(`/api/${path}`, apiBaseUrl());
  url.search = request.nextUrl.search;

  const headers = new Headers(authHeaders());
  for (const name of FORWARD_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  const hasBody = !["GET", "HEAD"].includes(request.method);
  let upstream: Response;
  try {
    upstream = await fetch(url, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      signal: request.signal,
      cache: "no-store",
    });
  } catch {
    return Response.json({ detail: "API unreachable" }, { status: 502 });
  }

  const responseHeaders = new Headers();
  for (const name of FORWARD_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  if (upstream.headers.get("content-type")?.startsWith("text/event-stream")) {
    responseHeaders.set("Cache-Control", "no-cache, no-transform");
    responseHeaders.set("X-Accel-Buffering", "no");
  }
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export { forward as GET, forward as POST, forward as PATCH, forward as DELETE };
