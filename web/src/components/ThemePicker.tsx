"use client";

import { Check, Palette } from "lucide-react";
import { useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import { THEMES, THEME_COLLECTIONS, THEME_EVENT, applyTheme, findTheme, type Theme } from "@/lib/themes";

// Collection first (KeyUp's set, then the portfolio's), then Dark and Light within each.
const SECTIONS = THEME_COLLECTIONS.map((c) => {
  const themes = THEMES.filter((t) => t.collection === c.id);
  return {
    ...c,
    count: themes.length,
    groups: [
      { label: "Dark", themes: themes.filter((t) => t.mode === "dark") },
      { label: "Light", themes: themes.filter((t) => t.mode === "light") },
    ].filter((g) => g.themes.length > 0),
  };
});

function collectionLabel(theme: Theme): string {
  return THEME_COLLECTIONS.find((c) => c.id === theme.collection)?.label ?? "";
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(THEME_EVENT, onChange);
  return () => window.removeEventListener(THEME_EVENT, onChange);
}

/** The theme on <html>, set before paint by the boot script; null during server render. */
function useCurrentTheme(): Theme | null {
  const id = useSyncExternalStore(
    subscribe,
    () => document.documentElement.dataset.theme ?? null,
    () => null,
  );
  return findTheme(id) ?? null;
}

/** A theme drawn in its own colors: its ground with a tiny stack of Todo, Doing and Needs Input passes. */
function Swatch({
  theme,
  selected,
  onPick,
}: {
  theme: Theme;
  selected: boolean;
  onPick: () => void;
}) {
  const t = theme.tokens;
  return (
    <button
      type="button"
      className="theme-swatch"
      aria-pressed={selected}
      onClick={onPick}
      data-theme-id={theme.id}
    >
      <span className="theme-preview" style={{ background: t.bg, borderColor: t.line }} aria-hidden>
        <span className="theme-mini" style={{ background: t.raised, top: 10 }} />
        <span className="theme-mini" style={{ background: t.ink, top: 22 }} />
        <span className="theme-mini" style={{ background: t.accent, top: 34 }} />
      </span>
      <span className="theme-name">
        {selected ? <Check size={12} strokeWidth={2.5} aria-hidden /> : null}
        {theme.name}
      </span>
    </button>
  );
}

export function ThemePicker() {
  const current = useCurrentTheme();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    // Start keyboard users on the theme they already have, scrolled to the middle of the
    // popover so it doesn't sit under the pinned collection name or at the bottom edge.
    const selected = wrapRef.current?.querySelector<HTMLButtonElement>('.theme-swatch[aria-pressed="true"]');
    selected?.focus({ preventScroll: true });
    selected?.scrollIntoView({ block: "center" });
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  return (
    <div className="theme-wrap" ref={wrapRef}>
      <button
        ref={buttonRef}
        type="button"
        className="theme-button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
        title={current ? `${current.name} · ${collectionLabel(current)}` : undefined}
      >
        <Palette size={15} strokeWidth={2} aria-hidden />
        <span className="theme-button-name">{current ? current.name.split(" & ")[0] : "Theme"}</span>
        <span className="sr-only">theme, change</span>
      </button>
      {open ? (
        <div className="theme-popover" id={panelId} role="dialog" aria-label="Choose a theme">
          <div className="theme-popover-head">
            <span>Theme</span>
            <span className="theme-popover-current">
              {current
                ? `${current.name} · ${collectionLabel(current)} · ${current.mode === "dark" ? "Dark" : "Light"}`
                : ""}
            </span>
          </div>
          {SECTIONS.map((c) => (
            <section key={c.id} className="theme-collection" aria-labelledby={`${panelId}-${c.id}`}>
              <h3 className="theme-collection-head" id={`${panelId}-${c.id}`}>
                {c.label}
                <span className="theme-collection-count">{c.count}</span>
              </h3>
              {c.groups.map((g) => (
                <div key={g.label} role="group" aria-label={`${c.label} ${g.label.toLowerCase()} themes`}>
                  <p className="theme-group-label">{g.label}</p>
                  <div className="theme-grid">
                    {g.themes.map((t) => (
                      <Swatch
                        key={t.id}
                        theme={t}
                        selected={t.id === current?.id}
                        onPick={() => applyTheme(t.id)}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </section>
          ))}
          <p className="theme-popover-note">nexTix themes match KeyUp; Portfolio themes match your site. Saved in this browser.</p>
        </div>
      ) : null}
    </div>
  );
}
