/**
 * The ticket page's tabs and their `?tab=` values, so the chosen tab survives a reload
 * and a shared link opens on it.
 */

export type DetailTab = "transcript" | "diff" | "before-after" | "checks";

export const DETAIL_TABS: ReadonlyArray<{ id: DetailTab; title: string }> = [
  { id: "transcript", title: "Transcript" },
  { id: "diff", title: "Diff" },
  { id: "before-after", title: "Before / After" },
  { id: "checks", title: "Checks" },
];

const IDS = new Set<string>(DETAIL_TABS.map((t) => t.id));

/** A `?tab=` value from the URL; anything unknown means the transcript. */
export function parseTab(value: string | string[] | null | undefined): DetailTab {
  const raw = Array.isArray(value) ? value[0] : value;
  return raw && IDS.has(raw) ? (raw as DetailTab) : "transcript";
}

/** The tabs the selected run can show: Before / After only when it has screenshots. */
export function availableTabs(hasScreenshots: boolean): DetailTab[] {
  return DETAIL_TABS.map((t) => t.id).filter((id) => id !== "before-after" || hasScreenshots);
}

/** The tab to show: the chosen one, unless the selected run doesn't have it. */
export function shownTab(chosen: DetailTab, hasScreenshots: boolean): DetailTab {
  return availableTabs(hasScreenshots).includes(chosen) ? chosen : "transcript";
}

/**
 * The page URL with `tab` set (or removed for the default), keeping every other
 * parameter and the hash.
 */
export function urlWithTab(href: string, tab: DetailTab): string {
  const url = new URL(href);
  if (tab === "transcript") url.searchParams.delete("tab");
  else url.searchParams.set("tab", tab);
  return `${url.pathname}${url.search}${url.hash}`;
}

/** Arrow-key movement in a tab list, wrapping at the ends (WAI-ARIA tabs pattern). */
export function nextTab(
  tabs: readonly DetailTab[],
  current: DetailTab,
  key: string,
): DetailTab | null {
  const i = tabs.indexOf(current);
  if (i < 0 || tabs.length === 0) return null;
  switch (key) {
    case "ArrowRight":
      return tabs[(i + 1) % tabs.length]!;
    case "ArrowLeft":
      return tabs[(i - 1 + tabs.length) % tabs.length]!;
    case "Home":
      return tabs[0]!;
    case "End":
      return tabs[tabs.length - 1]!;
    default:
      return null;
  }
}
