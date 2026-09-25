"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { SESSION_COOKIE, SESSION_MAX_AGE_S, safeEqual, sessionValue } from "@/lib/session";

function safeNext(value: FormDataEntryValue | null): string {
  const next = typeof value === "string" ? value : "/";
  // Only same-site relative paths; blocks open redirects like //evil.com.
  return next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

export async function login(formData: FormData): Promise<void> {
  const expected = process.env.NEXTIX_API_TOKEN;
  const submitted = formData.get("token");
  const next = safeNext(formData.get("next"));
  if (!expected || typeof submitted !== "string" || !safeEqual(submitted, expected)) {
    redirect(`/login?error=1&next=${encodeURIComponent(next)}`);
  }
  (await cookies()).set(SESSION_COOKIE, await sessionValue(expected), {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NEXTIX_PUBLIC_URL?.startsWith("https://") ?? false,
    path: "/",
    maxAge: SESSION_MAX_AGE_S,
  });
  redirect(next);
}

export async function logout(): Promise<void> {
  (await cookies()).delete(SESSION_COOKIE);
  redirect("/login");
}
