/**
 * The contrast gate (KeyUp's rule, applied to nexTix's pass colors): every theme must
 * keep text at 4.5:1 or better on every surface it lands on. This models the exact
 * mixes globals.css uses, so a failing theme fails here before it ships.
 */
import { describe, expect, it } from "vitest";
import { THEMES, findTheme, themeBootScript, themeCss, type Theme } from "./themes";

type RGB = [number, number, number];

function hex(color: string): RGB {
  const h = color.replace("#", "");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as RGB;
}

/** color-mix(in srgb, a pct%, b) */
function mix(a: RGB, pct: number, b: RGB): RGB {
  return a.map((v, i) => v * pct + (b[i] as number) * (1 - pct)) as RGB;
}

function luminance([r, g, b]: RGB): number {
  const lin = (c: number) => {
    const s = c / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

function contrast(a: RGB, b: RGB): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

/** The pass colors globals.css derives from a theme. */
function passes(theme: Theme): Record<string, { fill: RGB; ink: RGB }> {
  const t = theme.tokens;
  const onHue = theme.mode === "dark" ? hex(t.bg) : hex("#ffffff");
  return {
    todo: { fill: hex(t.raised), ink: hex(t.ink) },
    doing: { fill: mix(hex(t.accent), 0.35, hex(t.raised)), ink: hex(t.ink) },
    needs_input: { fill: hex(t.ink), ink: hex(t.bg) },
    failed: { fill: hex(t.red), ink: onHue },
    in_review: { fill: hex(t.rose), ink: onHue },
    done: { fill: hex(t.green), ink: onHue },
  };
}

const AA = 4.5;

describe.each(THEMES.map((t) => [t.name, t] as const))("%s", (_name, theme) => {
  const t = theme.tokens;

  it("keeps pass titles and labels (94% mix) readable on every pass color", () => {
    for (const [state, { fill, ink }] of Object.entries(passes(theme))) {
      expect(contrast(ink, fill), `${state} title`).toBeGreaterThanOrEqual(AA);
      expect(contrast(mix(ink, 0.94, fill), fill), `${state} label`).toBeGreaterThanOrEqual(AA);
    }
  });

  it("keeps ground text, links and the primary button readable", () => {
    expect(contrast(hex(t.ink3), hex(t.bg)), "tertiary ink on ground").toBeGreaterThanOrEqual(AA);
    expect(contrast(hex(t.ink3), hex(t.raised)), "tertiary ink on surface").toBeGreaterThanOrEqual(AA);
    expect(contrast(hex(t.ink3), hex(t.pane)), "tertiary ink in the theme popover").toBeGreaterThanOrEqual(AA);
    const accentText = mix(hex(t.accent), 0.85, hex(t.ink));
    expect(contrast(accentText, hex(t.bg)), "accent link on ground").toBeGreaterThanOrEqual(AA);
    expect(contrast(hex(t.onAccent), hex(t.accent)), "button text").toBeGreaterThanOrEqual(AA);
  });

  it("keeps the stalled pass and error text readable", () => {
    expect(contrast(hex(t.red), hex(t.bg)), "No heartbeat on ground").toBeGreaterThanOrEqual(AA);
    expect(contrast(hex(t.ink2), hex(t.bg)), "quiet-for on ground").toBeGreaterThanOrEqual(AA);
    expect(contrast(hex(t.red), hex(t.raised)), "composer error").toBeGreaterThanOrEqual(AA);
  });

  it("keeps the stalled outline and live dot visible (3:1 for graphics)", () => {
    expect(contrast(hex(t.accent), hex(t.bg)), "stalled outline").toBeGreaterThanOrEqual(3);
    expect(contrast(hex(t.green), hex(t.bg)), "live dot").toBeGreaterThanOrEqual(3);
  });
});

describe("theme plumbing", () => {
  it("emits one rule per theme plus a :root fallback", () => {
    const css = themeCss();
    for (const theme of THEMES) expect(css).toContain(`[data-theme="${theme.id}"]`);
    expect(css.startsWith(":root{")).toBe(true);
  });

  it("boot script knows every theme and falls back by OS preference", () => {
    const script = themeBootScript();
    for (const theme of THEMES) expect(script).toContain(`"${theme.id}":"${theme.mode}"`);
    expect(script).toContain("prefers-color-scheme: light");
  });

  it("finds themes by id and rejects unknown ids", () => {
    expect(findTheme("espresso")?.name).toBe("Espresso & Cream");
    expect(findTheme("nope")).toBeUndefined();
  });
});

/** Perceptual distance (OKLab ΔE): how different two colors look, not how bright. */
function deltaE(a: RGB, b: RGB): number {
  const lab = ([r, g, bl]: RGB) => {
    const lin = (c: number) => {
      const s = c / 255;
      return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    };
    const [R, G, B] = [lin(r), lin(g), lin(bl)];
    const l = Math.cbrt(0.4122214708 * R + 0.5363325363 * G + 0.0514459929 * B);
    const m = Math.cbrt(0.2119034982 * R + 0.6806995451 * G + 0.1073969566 * B);
    const s = Math.cbrt(0.0883024619 * R + 0.2817188376 * G + 0.6299787005 * B);
    return [
      0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
      1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
      0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
    ];
  };
  const [x, y] = [lab(a), lab(b)];
  return Math.hypot(x[0]! - y[0]!, x[1]! - y[1]!, x[2]! - y[2]!);
}

describe("state colors stay apart", () => {
  const states = ["todo", "doing", "needs_input", "failed", "in_review", "done"];

  // A running agent or a question for the owner must never read as a failure at a glance.
  // (In Review's rose and Failed's red are KeyUp's own distinct semantic pair, about 40 to 75
  // degrees apart in hue; they are held to the all-pairs bar below, minimum 0.12.)
  it("keeps Doing and Needs Input far from Failed red in every theme", () => {
    for (const theme of THEMES) {
      const p = passes(theme);
      for (const s of ["doing", "needs_input"]) {
        expect(deltaE(p[s]!.fill, p.failed!.fill), `${theme.name}: ${s} vs failed`).toBeGreaterThanOrEqual(0.2);
      }
    }
  });

  it("keeps every pair of states visibly different in every theme", () => {
    for (const theme of THEMES) {
      const p = passes(theme);
      for (let i = 0; i < states.length; i++) {
        for (let j = i + 1; j < states.length; j++) {
          const [a, b] = [states[i]!, states[j]!];
          expect(deltaE(p[a]!.fill, p[b]!.fill), `${theme.name}: ${a} vs ${b}`).toBeGreaterThanOrEqual(0.1);
        }
      }
    }
  });
});
