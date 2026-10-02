/**
 * Color themes in two collections:
 * - "nextix": the 15 themes shared with KeyUp (same ids, same values; keep them in step).
 * - "portfolio": the 38 themes of the owner's portfolio site, mapped onto the same tokens.
 *   Ground, surface, text and brand keep the portfolio's values (bg, raised, ink, accent);
 *   the rest is derived from them and bent only where the gate demands it. Seven portfolio
 *   ids collide with KeyUp's, so they are renamed: forest -> meadow, midnight -> lime,
 *   espresso -> latte, slate -> ice, linen -> indigo, ink -> periwinkle, plum -> lemon.
 *   Names are the portfolio's own.
 *
 * A theme sets exactly the 16 base tokens below. Everything else in globals.css,
 * including the six pass colors, derives from them, so a new theme needs no CSS.
 * The contrast gate in themes.test.ts must pass for every theme.
 */

export type ThemeMode = "dark" | "light";

export type ThemeCollection = "nextix" | "portfolio";

/** Collections in picker order, with their display labels. */
export const THEME_COLLECTIONS: { id: ThemeCollection; label: string }[] = [
  { id: "nextix", label: "nexTix" },
  { id: "portfolio", label: "Portfolio" },
];

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
  collection: ThemeCollection;
  tokens: ThemeTokens;
}

const darkShadow = "rgba(0, 0, 0, 0.72)";

/** KeyUp's 15, unchanged. */
const NEXTIX_THEMES: Theme[] = [
  {
    id: "charcoal", name: "Charcoal & Mustard", mode: "dark", collection: "nextix",
    tokens: { bg: "#1c1c1a", sidebar: "#181816", pane: "#20201e", raised: "#292926", field: "#1a1a18", line: "#2e2e2b",
      ink: "#f3f0e7", ink2: "#c9c5ba", ink3: "#a19d93", ink4: "#96938a", accent: "#d8a92b", onAccent: "#1c1b17",
      red: "#ee8479", rose: "#d1a2e6", green: "#93c996", shadow: darkShadow },
  },
  {
    id: "graphite", name: "Graphite & Amber", mode: "dark", collection: "nextix",
    tokens: { bg: "#1a1b1d", sidebar: "#161719", pane: "#1e1f22", raised: "#26282b", field: "#17181a", line: "#2c2e31",
      ink: "#eef0f3", ink2: "#c4c8ce", ink3: "#979ca4", ink4: "#8a8f97", accent: "#f5a524", onAccent: "#1a1206",
      red: "#f07a70", rose: "#cf9fe8", green: "#7fd0a0", shadow: darkShadow },
  },
  {
    id: "espresso", name: "Espresso & Cream", mode: "dark", collection: "nextix",
    tokens: { bg: "#1d1814", sidebar: "#19140f", pane: "#221c17", raised: "#2b241e", field: "#181410", line: "#332b24",
      ink: "#f4ece2", ink2: "#d4c6b6", ink3: "#a69684", ink4: "#998a79", accent: "#e9c89b", onAccent: "#2a1c10",
      red: "#ee8876", rose: "#d9a5d8", green: "#a2d198", shadow: darkShadow },
  },
  {
    id: "midnight", name: "Midnight & Coral", mode: "dark", collection: "nextix",
    tokens: { bg: "#121822", sidebar: "#0f141d", pane: "#161d28", raised: "#1d2531", field: "#10151e", line: "#252e3b",
      ink: "#edf1f7", ink2: "#c0c9d6", ink3: "#8f9bab", ink4: "#828e9e", accent: "#ff8a73", onAccent: "#2a0f08",
      red: "#f4707f", rose: "#c6a4f2", green: "#7fd6b0", shadow: darkShadow },
  },
  {
    id: "forest", name: "Forest & Brass", mode: "dark", collection: "nextix",
    tokens: { bg: "#141b17", sidebar: "#111713", pane: "#18201b", raised: "#1f2923", field: "#111814", line: "#28332c",
      ink: "#edf2ec", ink2: "#c3cdc4", ink3: "#93a096", ink4: "#869389", accent: "#d1ad5a", onAccent: "#1f1a0b",
      red: "#ee8276", rose: "#d0a3dd", green: "#a2d69e", shadow: darkShadow },
  },
  {
    id: "ink", name: "Ink & Cyan", mode: "dark", collection: "nextix",
    tokens: { bg: "#111416", sidebar: "#0e1113", pane: "#151a1d", raised: "#1b2125", field: "#0f1315", line: "#232a2f",
      ink: "#eaf2f5", ink2: "#bdc9cf", ink3: "#8b99a0", ink4: "#7f8d94", accent: "#5ccfe6", onAccent: "#06222a",
      red: "#f07b76", rose: "#cfa2e8", green: "#86d49e", shadow: darkShadow },
  },
  {
    id: "plum", name: "Plum & Peach", mode: "dark", collection: "nextix",
    tokens: { bg: "#1b1520", sidebar: "#17111b", pane: "#201926", raised: "#28202f", field: "#16111a", line: "#302738",
      ink: "#f4eef6", ink2: "#d2c4d8", ink3: "#a393aa", ink4: "#97879e", accent: "#f5ab86", onAccent: "#2b140a",
      red: "#f37f7f", rose: "#e5a9e2", green: "#99d6a2", shadow: darkShadow },
  },
  {
    id: "slate", name: "Slate & Mint", mode: "dark", collection: "nextix",
    tokens: { bg: "#161b1e", sidebar: "#13171a", pane: "#1a2024", raised: "#21292d", field: "#13181b", line: "#293237",
      ink: "#ebf1f2", ink2: "#c0cbce", ink3: "#909ea2", ink4: "#839195", accent: "#72dbad", onAccent: "#07261a",
      red: "#f07c74", rose: "#cfa3e3", green: "#b8de84", shadow: darkShadow },
  },
  {
    id: "oxblood", name: "Oxblood & Gold", mode: "dark", collection: "nextix",
    tokens: { bg: "#1e1414", sidebar: "#1a1010", pane: "#231818", raised: "#2c1f1f", field: "#181010", line: "#352626",
      ink: "#f6ecea", ink2: "#d8c3c0", ink3: "#aa9490", ink4: "#9e8884", accent: "#e0b04a", onAccent: "#231606",
      red: "#ff9484", rose: "#dea9e2", green: "#a6d69d", shadow: darkShadow },
  },
  {
    id: "obsidian", name: "Obsidian & Violet", mode: "dark", collection: "nextix",
    tokens: { bg: "#141318", sidebar: "#111015", pane: "#18171d", raised: "#201e26", field: "#110f14", line: "#28262f",
      ink: "#efedf5", ink2: "#c8c4d4", ink3: "#9994a8", ink4: "#8c879b", accent: "#b69cff", onAccent: "#1a1030",
      red: "#f37e7e", rose: "#e8a3d3", green: "#92d6a6", shadow: darkShadow },
  },
  {
    id: "carbon", name: "Carbon & Ember", mode: "dark", collection: "nextix",
    tokens: { bg: "#161616", sidebar: "#121212", pane: "#1a1a1a", raised: "#222222", field: "#121212", line: "#2a2a2a",
      ink: "#f0f0f0", ink2: "#c6c6c6", ink3: "#999999", ink4: "#8d8d8d", accent: "#ff8c42", onAccent: "#2a1100",
      red: "#ff6f6f", rose: "#cfa0ea", green: "#8bd49a", shadow: darkShadow },
  },
  {
    id: "linen", name: "Linen & Mustard", mode: "light", collection: "nextix",
    tokens: { bg: "#f6f3ec", sidebar: "#efebe2", pane: "#f2eee6", raised: "#fffdf8", field: "#ffffff", line: "#e3ddd0",
      ink: "#1f1d19", ink2: "#4a463e", ink3: "#655f55", ink4: "#6b665c", accent: "#8f6a0c", onAccent: "#ffffff",
      red: "#a8332a", rose: "#83429f", green: "#2c7038", shadow: "rgba(60, 45, 20, 0.2)" },
  },
  {
    id: "snow", name: "Snow & Cobalt", mode: "light", collection: "nextix",
    tokens: { bg: "#f5f6f8", sidebar: "#eceef2", pane: "#f0f2f5", raised: "#ffffff", field: "#ffffff", line: "#dfe3e9",
      ink: "#16191d", ink2: "#444b55", ink3: "#5d6570", ink4: "#646c77", accent: "#2359d6", onAccent: "#ffffff",
      red: "#b02318", rose: "#8a3fa8", green: "#1d7339", shadow: "rgba(20, 30, 50, 0.18)" },
  },
  {
    id: "sand", name: "Sand & Olive", mode: "light", collection: "nextix",
    tokens: { bg: "#f2ece1", sidebar: "#eae3d6", pane: "#eee8dc", raised: "#fbf8f2", field: "#fffdf9", line: "#ddd4c4",
      ink: "#22201a", ink2: "#4d483c", ink3: "#655f50", ink4: "#6a6455", accent: "#56631b", onAccent: "#ffffff",
      red: "#a2372a", rose: "#80439a", green: "#1d6a56", shadow: "rgba(60, 50, 30, 0.2)" },
  },
  {
    id: "mist", name: "Mist & Teal", mode: "light", collection: "nextix",
    tokens: { bg: "#eef2f1", sidebar: "#e5ebea", pane: "#e9eeed", raised: "#fbfdfc", field: "#ffffff", line: "#d5dedc",
      ink: "#152020", ink2: "#3f4d4c", ink3: "#526060", ink4: "#5a6868", accent: "#0b7068", onAccent: "#ffffff",
      red: "#aa3329", rose: "#80409a", green: "#2a7030", shadow: "rgba(20, 40, 40, 0.18)" },
  },
];

/**
 * The portfolio's 38 (Portfolio_Website/src/App.jsx), in its order. Derived surfaces follow
 * the KeyUp themes: dark sidebar/field are the ground darkened 14%/8%, pane is 30% raised
 * over the ground, line is 3.5% ink over raised; light sidebar/pane/line are 3.5%/2%/9% ink
 * over the ground, field is 60% white over raised. ink-2/3/4 are ink mixed toward the ground.
 * Two portfolio values are bent: Green & White's surface gains a little green chroma
 * (#eef6f0 -> #e8f9ec) so the selected segment parts from the rail, and Cobalt & Cyan's
 * brand drops 0.005 OKLCH lightness (#6fe3ff -> #6de1fd) for the Doing label.
 */
const PORTFOLIO_THEMES: Theme[] = [
  {
    id: "maroon", name: "Maroon & Cream", mode: "light", collection: "portfolio",
    tokens: { bg: "#f3e6d5", sidebar: "#eddece", pane: "#f0e1d1", raised: "#fff9f2", field: "#fffdfa", line: "#e4d1c4",
      ink: "#4a0014", ink2: "#6c2e3b", ink3: "#875359", ink4: "#8e5c61", accent: "#800020", onAccent: "#ffffff",
      red: "#a73b15", rose: "#814893", green: "#337344", shadow: "rgba(74, 0, 20, 0.2)" },
  },
  {
    id: "ocean", name: "Blue & Yellow", mode: "light", collection: "portfolio",
    tokens: { bg: "#fdf1b8", sidebar: "#f4eab5", pane: "#f8edb6", raised: "#fffbe3", field: "#fffdf4", line: "#e7deb0",
      ink: "#0a1f5c", ink2: "#3b496e", ink3: "#616b7d", ink4: "#6b7381", accent: "#1d4ed8", onAccent: "#ffffff",
      red: "#ac312a", rose: "#874197", green: "#2b7440", shadow: "rgba(10, 31, 92, 0.2)" },
  },
  {
    id: "meadow", name: "Green & White", mode: "light", collection: "portfolio",
    tokens: { bg: "#ffffff", sidebar: "#f6f8f7", pane: "#fafbfb", raised: "#e8f9ec", field: "#f6fdf7", line: "#e9edeb",
      ink: "#0c3b22", ink2: "#3d624e", ink3: "#557664", ink4: "#5f7e6d", accent: "#1b7a43", onAccent: "#ffffff",
      red: "#a83630", rose: "#874392", green: "#107460", shadow: "rgba(12, 59, 34, 0.2)" },
  },
  {
    id: "lime", name: "Black & Lime", mode: "dark", collection: "portfolio",
    tokens: { bg: "#0f0f0f", sidebar: "#0d0d0d", pane: "#121212", raised: "#1a1a1a", field: "#0e0e0e", line: "#222221",
      ink: "#f2f2ec", ink2: "#c5c5c0", ink3: "#a0a09c", ink4: "#979794", accent: "#c6f432", onAccent: "#0f0f0f",
      red: "#fb817a", rose: "#d29ee2", green: "#71d29d", shadow: darkShadow },
  },
  {
    id: "bubblegum", name: "Pink & Black", mode: "light", collection: "portfolio",
    tokens: { bg: "#ffd6e8", sidebar: "#f7cfe1", pane: "#fad2e4", raised: "#ffe9f2", field: "#fff6fa", line: "#eac4d5",
      ink: "#1a0a12", ink2: "#48333d", ink3: "#6c535f", ink4: "#765c68", accent: "#141414", onAccent: "#ffd6e8",
      red: "#ab331f", rose: "#7847a6", green: "#227240", shadow: "rgba(26, 10, 18, 0.2)" },
  },
  {
    id: "lavender", name: "Lavender & Violet", mode: "light", collection: "portfolio",
    tokens: { bg: "#ece6ff", sidebar: "#e5dff9", pane: "#e8e2fb", raised: "#f7f4ff", field: "#fcfbff", line: "#dad3ef",
      ink: "#26104f", ink2: "#4e3b72", ink3: "#6d5d8e", ink4: "#756695", accent: "#5b2bd1", onAccent: "#ffffff",
      red: "#ac3031", rose: "#943b84", green: "#1e7546", shadow: "rgba(38, 16, 79, 0.2)" },
  },
  {
    id: "harbor", name: "Navy & Orange", mode: "dark", collection: "portfolio",
    tokens: { bg: "#0d1b2a", sidebar: "#0b1724", pane: "#0f1e2f", raised: "#15263a", field: "#0c1927", line: "#1d2d40",
      ink: "#f3efe6", ink2: "#c5c5c0", ink3: "#a0a3a2", ink4: "#979a9b", accent: "#ff8a3d", onAccent: "#0d1b2a",
      red: "#fa7f8c", rose: "#cca0e7", green: "#7ed09c", shadow: darkShadow },
  },
  {
    id: "mint", name: "Mint & Chocolate", mode: "light", collection: "portfolio",
    tokens: { bg: "#d9f2e4", sidebar: "#d3ebdd", pane: "#d6eee0", raised: "#eefaf3", field: "#f8fdfa", line: "#cbdfd1",
      ink: "#3b2416", ink2: "#5b4d3f", ink3: "#6f685a", ink4: "#757062", accent: "#5a3825", onAccent: "#ffffff",
      red: "#a83630", rose: "#834791", green: "#337344", shadow: "rgba(59, 36, 22, 0.2)" },
  },
  {
    id: "tangerine", name: "Tangerine & Cream", mode: "light", collection: "portfolio",
    tokens: { bg: "#fff1e0", sidebar: "#f8e9d8", pane: "#fbeddc", raised: "#fff8ef", field: "#fffcf9", line: "#eeddcc",
      ink: "#431407", ink2: "#694032", ink3: "#876455", ink4: "#8e6c5e", accent: "#c2410c", onAccent: "#ffffff",
      red: "#af283d", rose: "#834596", green: "#337344", shadow: "rgba(67, 20, 7, 0.2)" },
  },
  {
    id: "coral", name: "Coral & Teal", mode: "light", collection: "portfolio",
    tokens: { bg: "#ffe1d6", sidebar: "#f6dbd0", pane: "#faded3", raised: "#fff1eb", field: "#fff9f7", line: "#e9d2c8",
      ink: "#0b3b37", ink2: "#3c5c57", ink3: "#526b65", ink4: "#5c726b", accent: "#0d6b64", onAccent: "#ffffff",
      red: "#ac3031", rose: "#854494", green: "#3e7232", shadow: "rgba(11, 59, 55, 0.2)" },
  },
  {
    id: "synthwave", name: "Purple & Hot Pink", mode: "dark", collection: "portfolio",
    tokens: { bg: "#1a0b2e", sidebar: "#160928", pane: "#1e0d33", raised: "#26123f", field: "#180a2a", line: "#2d1a46",
      ink: "#f5e9ff", ink2: "#c9bdd5", ink3: "#a699b4", ink4: "#9d90ab", accent: "#ff4fd8", onAccent: "#1a0b2e",
      red: "#fb8276", rose: "#b3a7fb", green: "#6ed2a0", shadow: darkShadow },
  },
  {
    id: "sky", name: "Sky & Navy", mode: "light", collection: "portfolio",
    tokens: { bg: "#dff1ff", sidebar: "#d7eaf8", pane: "#dbedfb", raised: "#f2f9ff", field: "#fafdff", line: "#cbdfee",
      ink: "#062a45", ink2: "#31526a", ink3: "#4e6c82", ink4: "#56748a", accent: "#0369a1", onAccent: "#ffffff",
      red: "#ac312c", rose: "#854494", green: "#2b7440", shadow: "rgba(6, 42, 69, 0.2)" },
  },
  {
    id: "latte", name: "Espresso & Latte", mode: "dark", collection: "portfolio",
    tokens: { bg: "#2b1d16", sidebar: "#251913", pane: "#302119", raised: "#3a2920", field: "#281b14", line: "#413027",
      ink: "#f5e8dc", ink2: "#cdbfb4", ink3: "#ac9f95", ink4: "#a4978d", accent: "#e8b98a", onAccent: "#2b1d16",
      red: "#f28979", rose: "#d59ed8", green: "#9cc992", shadow: darkShadow },
  },
  {
    id: "matcha", name: "Matcha & Oat", mode: "light", collection: "portfolio",
    tokens: { bg: "#eef0dc", sidebar: "#e7ead5", pane: "#eaecd8", raised: "#f8f9ee", field: "#fcfdf8", line: "#dce0ca",
      ink: "#2a3a10", ink2: "#515e39", ink3: "#616d49", ink4: "#697451", accent: "#4d6b1f", onAccent: "#ffffff",
      red: "#a8372a", rose: "#85468f", green: "#1b735b", shadow: "rgba(42, 58, 16, 0.2)" },
  },
  {
    id: "grape", name: "Grape & Mint", mode: "dark", collection: "portfolio",
    tokens: { bg: "#2a1245", sidebar: "#240f3b", pane: "#2e144b", raised: "#37195a", field: "#27113f", line: "#3e2060",
      ink: "#f2eaff", ink2: "#cabfda", ink3: "#aa9cbc", ink4: "#a294b5", accent: "#7af0c2", onAccent: "#2a1245",
      red: "#f6857a", rose: "#e398ca", green: "#99cc7c", shadow: darkShadow },
  },
  {
    id: "inferno", name: "Red & Black", mode: "dark", collection: "portfolio",
    tokens: { bg: "#111111", sidebar: "#0f0f0f", pane: "#141414", raised: "#1c1c1c", field: "#101010", line: "#242424",
      ink: "#f5f5f5", ink2: "#c7c7c7", ink3: "#a3a3a3", ink4: "#9a9a9a", accent: "#ff3b3b", onAccent: "#111111",
      red: "#f68675", rose: "#cf9fe5", green: "#85cf95", shadow: darkShadow },
  },
  {
    id: "gameboy", name: "Game Boy", mode: "light", collection: "portfolio",
    tokens: { bg: "#c4cfa1", sidebar: "#beca9c", pane: "#c1cc9e", raised: "#d6dfb5", field: "#eff2e1", line: "#b5c295",
      ink: "#1f3a1f", ink2: "#3b5335", ink3: "#42593a", ink4: "#485f40", accent: "#2f4d09", onAccent: "#ffffff",
      red: "#8b3c31", rose: "#73548a", green: "#066054", shadow: "rgba(31, 58, 31, 0.2)" },
  },
  {
    id: "barbie", name: "Barbie Pink", mode: "light", collection: "portfolio",
    tokens: { bg: "#ffe3f1", sidebar: "#f9dbea", pane: "#fbdfed", raised: "#fff0f7", field: "#fff9fc", line: "#efcfe0",
      ink: "#4a0930", ink2: "#6e3557", ink3: "#8b5775", ink4: "#92607d", accent: "#b80f6b", onAccent: "#ffffff",
      red: "#ab331f", rose: "#6b4cae", green: "#1e7546", shadow: "rgba(74, 9, 48, 0.2)" },
  },
  {
    id: "terminal", name: "Terminal Green", mode: "dark", collection: "portfolio",
    tokens: { bg: "#050805", sidebar: "#040704", pane: "#070c07", raised: "#0c140c", field: "#050705", line: "#131c13",
      ink: "#c9ffd5", ink2: "#a2ceab", ink3: "#82a68a", ink4: "#7b9c82", accent: "#39ff6a", onAccent: "#050805",
      red: "#fb8276", rose: "#d19ce9", green: "#79cfab", shadow: darkShadow },
  },
  {
    id: "ice", name: "Ice & Slate", mode: "light", collection: "portfolio",
    tokens: { bg: "#eef2f6", sidebar: "#e6eaef", pane: "#eaeef2", raised: "#f8fafc", field: "#fcfdfe", line: "#dadee4",
      ink: "#0f172a", ink2: "#3c4353", ink3: "#5f6673", ink4: "#686f7c", accent: "#334155", onAccent: "#ffffff",
      red: "#ac312c", rose: "#834596", green: "#2b7440", shadow: "rgba(15, 23, 42, 0.2)" },
  },
  {
    id: "mustard", name: "Charcoal & Mustard", mode: "dark", collection: "portfolio",
    tokens: { bg: "#1f2124", sidebar: "#1b1c1f", pane: "#222528", raised: "#2a2d31", field: "#1d1e21", line: "#313437",
      ink: "#f1efe8", ink2: "#c7c6c1", ink3: "#a5a5a1", ink4: "#9d9d9a", accent: "#f2c230", onAccent: "#1f2124",
      red: "#f6857a", rose: "#cca1e0", green: "#92cc93", shadow: darkShadow },
  },
  // The 17 added to the portfolio after the first 21, in its order. Same derivation; renamed:
  // linen -> indigo, ink -> periwinkle, plum -> lemon. Cobalt & Cyan's brand is bent a hair
  // (#6fe3ff -> #6de1fd) so the Doing pass label clears 4.5:1.
  {
    id: "peach", name: "Peach & Plum", mode: "light", collection: "portfolio",
    tokens: { bg: "#ffe8d6", sidebar: "#f8e0d2", pane: "#fbe3d4", raised: "#fff4ec", field: "#fffbf7", line: "#edd4cc",
      ink: "#3b0764", ink2: "#62347b", ink3: "#82588d", ink4: "#896192", accent: "#6b21a8", onAccent: "#ffffff",
      red: "#ac312c", rose: "#8e4088", green: "#267543", shadow: "rgba(59, 7, 100, 0.2)" },
  },
  {
    id: "dune", name: "Dune & Sea", mode: "light", collection: "portfolio",
    tokens: { bg: "#f1e7d3", sidebar: "#e9e1ce", pane: "#ece4d0", raised: "#fbf5ea", field: "#fdfbf7", line: "#dcd8c6",
      ink: "#0b3b42", ink2: "#395d5f", ink3: "#4b6b6b", ink4: "#557270", accent: "#0f6f7a", onAccent: "#ffffff",
      red: "#a83630", rose: "#854494", green: "#367341", shadow: "rgba(11, 59, 66, 0.2)" },
  },
  {
    id: "sage", name: "Blush & Sage", mode: "light", collection: "portfolio",
    tokens: { bg: "#f6ebe7", sidebar: "#eee5e0", pane: "#f2e7e3", raised: "#fdf6f4", field: "#fefbfb", line: "#e3dbd6",
      ink: "#1f3a27", ink2: "#4a5d4d", ink3: "#5d6d5f", ink4: "#667466", accent: "#3f6b4a", onAccent: "#ffffff",
      red: "#a83630", rose: "#854494", green: "#107460", shadow: "rgba(31, 58, 39, 0.2)" },
  },
  {
    id: "citrus", name: "Citrus & Cobalt", mode: "light", collection: "portfolio",
    tokens: { bg: "#eef7cf", sidebar: "#e6f0cb", pane: "#eaf3cd", raised: "#f7fbe6", field: "#fcfdf5", line: "#dbe4c5",
      ink: "#16265e", ink2: "#415075", ink3: "#5f6d84", ink4: "#687589", accent: "#1e40af", onAccent: "#ffffff",
      red: "#ac312c", rose: "#854494", green: "#1f744f", shadow: "rgba(22, 38, 94, 0.2)" },
  },
  {
    id: "quartz", name: "Rose Quartz & Wine", mode: "light", collection: "portfolio",
    tokens: { bg: "#fbe4ec", sidebar: "#f5dce5", pane: "#f8e0e8", raised: "#fdf1f5", field: "#fef9fb", line: "#ebd0d9",
      ink: "#4c0519", ink2: "#6f3243", ink3: "#8b5565", ink4: "#925e6d", accent: "#9d174d", onAccent: "#ffffff",
      red: "#a8372a", rose: "#6b4cae", green: "#267543", shadow: "rgba(76, 5, 25, 0.2)" },
  },
  {
    id: "butter", name: "Butter & Berry", mode: "light", collection: "portfolio",
    tokens: { bg: "#fff5d1", sidebar: "#f9edcd", pane: "#fbf0ce", raised: "#fffbe9", field: "#fffdf6", line: "#efe0c6",
      ink: "#4a0d52", ink2: "#6e3b6b", ink3: "#895e7e", ink4: "#916784", accent: "#a21caf", onAccent: "#ffffff",
      red: "#ac312c", rose: "#6b4cae", green: "#267543", shadow: "rgba(74, 13, 82, 0.2)" },
  },
  {
    id: "indigo", name: "Linen & Indigo", mode: "light", collection: "portfolio",
    tokens: { bg: "#f5f1e8", sidebar: "#edeae3", pane: "#f1ede5", raised: "#fcfaf4", field: "#fefdfb", line: "#e2deda",
      ink: "#1e1b4b", ink2: "#49466a", ink3: "#6b6884", ink4: "#74718a", accent: "#3730a3", onAccent: "#ffffff",
      red: "#ac312c", rose: "#8a428e", green: "#2b7440", shadow: "rgba(30, 27, 75, 0.2)" },
  },
  {
    id: "seafoam", name: "Seafoam & Rust", mode: "light", collection: "portfolio",
    tokens: { bg: "#dff0ec", sidebar: "#dae9e4", pane: "#dcece7", raised: "#f1f9f7", field: "#f9fdfc", line: "#d2ddd7",
      ink: "#4a1c08", ink2: "#684636", ink3: "#7b6253", ink4: "#816a5c", accent: "#9a3412", onAccent: "#ffffff",
      red: "#af2843", rose: "#854494", green: "#2b7440", shadow: "rgba(74, 28, 8, 0.2)" },
  },
  {
    id: "abyss", name: "Abyss & Aqua", mode: "dark", collection: "portfolio",
    tokens: { bg: "#04252c", sidebar: "#032026", pane: "#062a32", raised: "#0b3540", field: "#042228", line: "#133c46",
      ink: "#e6fbf8", ink2: "#b9d0cf", ink3: "#95aeaf", ink4: "#8ca5a6", accent: "#4fe0d2", onAccent: "#04252c",
      red: "#fb817a", rose: "#d19ce9", green: "#90ce83", shadow: darkShadow },
  },
  {
    id: "periwinkle", name: "Ink & Periwinkle", mode: "dark", collection: "portfolio",
    tokens: { bg: "#12142b", sidebar: "#0f1125", pane: "#151831", raised: "#1d2040", field: "#111228", line: "#242747",
      ink: "#ecefff", ink2: "#c0c3d5", ink3: "#9ea0b3", ink4: "#9597aa", accent: "#aab6ff", onAccent: "#12142b",
      red: "#fb8276", rose: "#d69be4", green: "#77d199", shadow: darkShadow },
  },
  {
    id: "moss", name: "Moss & Honey", mode: "dark", collection: "portfolio",
    tokens: { bg: "#12251a", sidebar: "#0f2016", pane: "#15291d", raised: "#1b3325", field: "#112218", line: "#223a2c",
      ink: "#ecf3e8", ink2: "#c0cabf", ink3: "#9ea99e", ink4: "#95a196", accent: "#f0c86a", onAccent: "#12251a",
      red: "#fb8276", rose: "#d49be6", green: "#70d1a9", shadow: darkShadow },
  },
  {
    id: "merlot", name: "Merlot & Rose", mode: "dark", collection: "portfolio",
    tokens: { bg: "#2a0e18", sidebar: "#240c15", pane: "#2f111c", raised: "#3a1724", field: "#270d16", line: "#411e2b",
      ink: "#fdeaf0", ink2: "#d3bec5", ink3: "#b19ba2", ink4: "#a9929a", accent: "#ffa6c1", onAccent: "#2a0e18",
      red: "#fb8371", rose: "#c0a2f5", green: "#7cd194", shadow: darkShadow },
  },
  {
    id: "steel", name: "Steel & Ice", mode: "dark", collection: "portfolio",
    tokens: { bg: "#1b222b", sidebar: "#171d25", pane: "#1e2630", raised: "#26303c", field: "#191f28", line: "#2d3743",
      ink: "#eaf2fa", ink2: "#c1c8d1", ink3: "#9fa7af", ink4: "#979fa7", accent: "#8fd3ff", onAccent: "#1b222b",
      red: "#fb8276", rose: "#d49be6", green: "#7cd194", shadow: darkShadow },
  },
  {
    id: "olive", name: "Olive & Apricot", mode: "dark", collection: "portfolio",
    tokens: { bg: "#232712", sidebar: "#1e220f", pane: "#272c15", raised: "#31371c", field: "#202411", line: "#383e23",
      ink: "#f3f1e1", ink2: "#c9c9b8", ink3: "#a8a896", ink4: "#a0a08e", accent: "#ffb877", onAccent: "#232712",
      red: "#fe7d85", rose: "#d49be6", green: "#77d0a2", shadow: darkShadow },
  },
  {
    id: "lemon", name: "Plum & Lemon", mode: "dark", collection: "portfolio",
    tokens: { bg: "#2d0f2a", sidebar: "#270d24", pane: "#32122e", raised: "#3c1938", field: "#290e27", line: "#43203f",
      ink: "#f8eaf6", ink2: "#cfbecd", ink3: "#af9bad", ink4: "#a792a4", accent: "#f6e06b", onAccent: "#2d0f2a",
      red: "#fb8276", rose: "#cc9eed", green: "#7cd194", shadow: darkShadow },
  },
  {
    id: "cobalt", name: "Cobalt & Cyan", mode: "dark", collection: "portfolio",
    tokens: { bg: "#101f4a", sidebar: "#0e1b40", pane: "#132351", raised: "#1a2c60", field: "#0f1d44", line: "#213366",
      ink: "#e9eeff", ink2: "#bec5db", ink3: "#9ba3be", ink4: "#929bb7", accent: "#6de1fd", onAccent: "#101f4a",
      red: "#fb8276", rose: "#d69be4", green: "#7cd194", shadow: darkShadow },
  },
  {
    id: "onyx", name: "Onyx & Violet", mode: "dark", collection: "portfolio",
    tokens: { bg: "#121016", sidebar: "#0f0e13", pane: "#15131a", raised: "#1d1a23", field: "#110f14", line: "#24212a",
      ink: "#efecf5", ink2: "#c3c0c8", ink3: "#9f9da5", ink4: "#97949c", accent: "#bb92ff", onAccent: "#121016",
      red: "#fb8276", rose: "#d69be4", green: "#7cd194", shadow: darkShadow },
  },
];

export const THEMES: Theme[] = [...NEXTIX_THEMES, ...PORTFOLIO_THEMES];

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
