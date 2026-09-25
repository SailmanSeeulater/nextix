/**
 * Color themes, shared with KeyUp (same 15 themes, same values).
 *
 * A theme sets exactly the 16 base tokens below. Everything else in globals.css,
 * including the six pass colors, derives from them, so a new theme needs no CSS.
 * The contrast gate in themes.test.ts must pass for every theme.
 */

export type ThemeMode = "dark" | "light";

export interface ThemeTokens {
  bg: string;
  sidebar: string;
  pane: string;
  raised: string;
  field: string;
  line: string;
  ink: string;
  ink2: string;
  ink3: string;
  ink4: string;
  accent: string;
  onAccent: string;
  red: string;
  rose: string;
  green: string;
  shadow: string;
}

export interface Theme {
  id: string;
  name: string;
  mode: ThemeMode;
  tokens: ThemeTokens;
}

const darkShadow = "rgba(0, 0, 0, 0.72)";

export const THEMES: Theme[] = [
  {
    id: "charcoal", name: "Charcoal & Mustard", mode: "dark",
    tokens: { bg: "#1c1c1a", sidebar: "#181816", pane: "#20201e", raised: "#292926", field: "#1a1a18", line: "#2e2e2b",
      ink: "#f3f0e7", ink2: "#c9c5ba", ink3: "#a19d93", ink4: "#96938a", accent: "#d8a92b", onAccent: "#1c1b17",
      red: "#ee8479", rose: "#d1a2e6", green: "#93c996", shadow: darkShadow },
  },
  {
    id: "graphite", name: "Graphite & Amber", mode: "dark",
    tokens: { bg: "#1a1b1d", sidebar: "#161719", pane: "#1e1f22", raised: "#26282b", field: "#17181a", line: "#2c2e31",
      ink: "#eef0f3", ink2: "#c4c8ce", ink3: "#979ca4", ink4: "#8a8f97", accent: "#f5a524", onAccent: "#1a1206",
      red: "#f07a70", rose: "#cf9fe8", green: "#7fd0a0", shadow: darkShadow },
  },
  {
    id: "espresso", name: "Espresso & Cream", mode: "dark",
    tokens: { bg: "#1d1814", sidebar: "#19140f", pane: "#221c17", raised: "#2b241e", field: "#181410", line: "#332b24",
      ink: "#f4ece2", ink2: "#d4c6b6", ink3: "#a69684", ink4: "#998a79", accent: "#e9c89b", onAccent: "#2a1c10",
      red: "#ee8876", rose: "#d9a5d8", green: "#a2d198", shadow: darkShadow },
  },
  {
    id: "midnight", name: "Midnight & Coral", mode: "dark",
    tokens: { bg: "#121822", sidebar: "#0f141d", pane: "#161d28", raised: "#1d2531", field: "#10151e", line: "#252e3b",
      ink: "#edf1f7", ink2: "#c0c9d6", ink3: "#8f9bab", ink4: "#828e9e", accent: "#ff8a73", onAccent: "#2a0f08",
      red: "#f4707f", rose: "#c6a4f2", green: "#7fd6b0", shadow: darkShadow },
  },
  {
    id: "forest", name: "Forest & Brass", mode: "dark",
    tokens: { bg: "#141b17", sidebar: "#111713", pane: "#18201b", raised: "#1f2923", field: "#111814", line: "#28332c",
      ink: "#edf2ec", ink2: "#c3cdc4", ink3: "#93a096", ink4: "#869389", accent: "#d1ad5a", onAccent: "#1f1a0b",
      red: "#ee8276", rose: "#d0a3dd", green: "#a2d69e", shadow: darkShadow },
  },
  {
    id: "ink", name: "Ink & Cyan", mode: "dark",
    tokens: { bg: "#111416", sidebar: "#0e1113", pane: "#151a1d", raised: "#1b2125", field: "#0f1315", line: "#232a2f",
      ink: "#eaf2f5", ink2: "#bdc9cf", ink3: "#8b99a0", ink4: "#7f8d94", accent: "#5ccfe6", onAccent: "#06222a",
      red: "#f07b76", rose: "#cfa2e8", green: "#86d49e", shadow: darkShadow },
  },
  {
    id: "plum", name: "Plum & Peach", mode: "dark",
    tokens: { bg: "#1b1520", sidebar: "#17111b", pane: "#201926", raised: "#28202f", field: "#16111a", line: "#302738",
      ink: "#f4eef6", ink2: "#d2c4d8", ink3: "#a393aa", ink4: "#97879e", accent: "#f5ab86", onAccent: "#2b140a",
      red: "#f37f7f", rose: "#e5a9e2", green: "#99d6a2", shadow: darkShadow },
  },
  {
    id: "slate", name: "Slate & Mint", mode: "dark",
    tokens: { bg: "#161b1e", sidebar: "#13171a", pane: "#1a2024", raised: "#21292d", field: "#13181b", line: "#293237",
      ink: "#ebf1f2", ink2: "#c0cbce", ink3: "#909ea2", ink4: "#839195", accent: "#72dbad", onAccent: "#07261a",
      red: "#f07c74", rose: "#cfa3e3", green: "#b8de84", shadow: darkShadow },
  },
  {
    id: "oxblood", name: "Oxblood & Gold", mode: "dark",
    tokens: { bg: "#1e1414", sidebar: "#1a1010", pane: "#231818", raised: "#2c1f1f", field: "#181010", line: "#352626",
      ink: "#f6ecea", ink2: "#d8c3c0", ink3: "#aa9490", ink4: "#9e8884", accent: "#e0b04a", onAccent: "#231606",
      red: "#ff9484", rose: "#dea9e2", green: "#a6d69d", shadow: darkShadow },
  },
  {
    id: "obsidian", name: "Obsidian & Violet", mode: "dark",
    tokens: { bg: "#141318", sidebar: "#111015", pane: "#18171d", raised: "#201e26", field: "#110f14", line: "#28262f",
      ink: "#efedf5", ink2: "#c8c4d4", ink3: "#9994a8", ink4: "#8c879b", accent: "#b69cff", onAccent: "#1a1030",
      red: "#f37e7e", rose: "#e8a3d3", green: "#92d6a6", shadow: darkShadow },
  },
  {
    id: "carbon", name: "Carbon & Ember", mode: "dark",
    tokens: { bg: "#161616", sidebar: "#121212", pane: "#1a1a1a", raised: "#222222", field: "#121212", line: "#2a2a2a",
      ink: "#f0f0f0", ink2: "#c6c6c6", ink3: "#999999", ink4: "#8d8d8d", accent: "#ff8c42", onAccent: "#2a1100",
      red: "#ff6f6f", rose: "#cfa0ea", green: "#8bd49a", shadow: darkShadow },
  },
  {
    id: "linen", name: "Linen & Mustard", mode: "light",
    tokens: { bg: "#f6f3ec", sidebar: "#efebe2", pane: "#f2eee6", raised: "#fffdf8", field: "#ffffff", line: "#e3ddd0",
      ink: "#1f1d19", ink2: "#4a463e", ink3: "#655f55", ink4: "#6b665c", accent: "#8f6a0c", onAccent: "#ffffff",
      red: "#a8332a", rose: "#83429f", green: "#2c7038", shadow: "rgba(60, 45, 20, 0.2)" },
  },
  {
    id: "snow", name: "Snow & Cobalt", mode: "light",
    tokens: { bg: "#f5f6f8", sidebar: "#eceef2", pane: "#f0f2f5", raised: "#ffffff", field: "#ffffff", line: "#dfe3e9",
      ink: "#16191d", ink2: "#444b55", ink3: "#5d6570", ink4: "#646c77", accent: "#2359d6", onAccent: "#ffffff",
      red: "#b02318", rose: "#8a3fa8", green: "#1d7339", shadow: "rgba(20, 30, 50, 0.18)" },
  },
  {
    id: "sand", name: "Sand & Olive", mode: "light",
    tokens: { bg: "#f2ece1", sidebar: "#eae3d6", pane: "#eee8dc", raised: "#fbf8f2", field: "#fffdf9", line: "#ddd4c4",
      ink: "#22201a", ink2: "#4d483c", ink3: "#655f50", ink4: "#6a6455", accent: "#56631b", onAccent: "#ffffff",
      red: "#a2372a", rose: "#80439a", green: "#1d6a56", shadow: "rgba(60, 50, 30, 0.2)" },
  },
  {
    id: "mist", name: "Mist & Teal", mode: "light",
    tokens: { bg: "#eef2f1", sidebar: "#e5ebea", pane: "#e9eeed", raised: "#fbfdfc", field: "#ffffff", line: "#d5dedc",
      ink: "#152020", ink2: "#3f4d4c", ink3: "#526060", ink4: "#5a6868", accent: "#0b7068", onAccent: "#ffffff",
      red: "#aa3329", rose: "#80409a", green: "#2a7030", shadow: "rgba(20, 40, 40, 0.18)" },
  },
];

export const DEFAULT_DARK_THEME = "charcoal";
export const DEFAULT_LIGHT_THEME = "linen";
export const THEME_STORAGE_KEY = "nextix.theme";
export const THEME_EVENT = "nextix:theme";

const CSS_VAR: Record<keyof ThemeTokens, string> = {
  bg: "--bg",
  sidebar: "--sidebar",
  pane: "--pane",
  raised: "--raised",
  field: "--field",
  line: "--line",
  ink: "--ink",
  ink2: "--ink-2",
  ink3: "--ink-3",
  ink4: "--ink-4",
  accent: "--accent",
  onAccent: "--on-accent",
  red: "--red",
  rose: "--rose",
  green: "--green",
  shadow: "--shadow",
};

export function findTheme(id: string | null | undefined): Theme | undefined {
  return THEMES.find((t) => t.id === id);
}

function block(selector: string, theme: Theme): string {
  const vars = (Object.keys(CSS_VAR) as (keyof ThemeTokens)[])
    .map((k) => `${CSS_VAR[k]}:${theme.tokens[k]}`)
    .join(";");
  return `${selector}{${vars};color-scheme:${theme.mode}}`;
}

/** One rule per theme, keyed by `data-theme` on <html>; the default theme doubles as :root. */
export function themeCss(): string {
  const fallback = findTheme(DEFAULT_DARK_THEME) as Theme;
  return [
    block(":root", fallback),
    ...THEMES.map((t) => block(`:root[data-theme="${t.id}"]`, t)),
  ].join("\n");
}

/**
 * Runs in <head> before first paint: apply the saved theme, or follow the OS until
 * the owner picks one, so the page never flashes the wrong ground.
 */
export function themeBootScript(): string {
  const modes = Object.fromEntries(THEMES.map((t) => [t.id, t.mode]));
  const grounds = Object.fromEntries(THEMES.map((t) => [t.id, t.tokens.bg]));
  return `(function(){try{var m=${JSON.stringify(modes)},g=${JSON.stringify(grounds)},id=null;try{id=localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)})}catch(e){}if(!m[id]){id=window.matchMedia("(prefers-color-scheme: light)").matches?${JSON.stringify(DEFAULT_LIGHT_THEME)}:${JSON.stringify(DEFAULT_DARK_THEME)}}var r=document.documentElement;r.dataset.theme=id;r.dataset.mode=m[id];var c=document.querySelector('meta[name="theme-color"]');if(c)c.setAttribute("content",g[id])}catch(e){}})();`;
}

export function applyTheme(id: string): Theme | undefined {
  const theme = findTheme(id);
  if (!theme) return undefined;
  const root = document.documentElement;
  root.dataset.theme = theme.id;
  root.dataset.mode = theme.mode;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme.tokens.bg);
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme.id);
  } catch {
    // storage unavailable: the theme lasts this session
  }
  window.dispatchEvent(new CustomEvent(THEME_EVENT, { detail: theme.id }));
  return theme;
}
