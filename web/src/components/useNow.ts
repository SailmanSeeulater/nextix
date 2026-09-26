"use client";

import { useEffect, useState } from "react";

/**
 * A clock that ticks every `intervalMs`. It starts at `initial` (the server's render
 * time) so the first client render matches the server HTML exactly.
 */
export function useNow(initial: number, intervalMs: number): number {
  const [now, setNow] = useState(initial);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
