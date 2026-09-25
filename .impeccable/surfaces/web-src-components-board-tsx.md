---
version: 1
slug: "web-src-components-board-tsx"
primary_target: "web/src/components/Board.tsx"
related_targets: ["web/src/app/page.tsx","web/src/app/login/page.tsx"]
---

# Board surface brief

Scope: the board route (`/`), its ticket composer, and the sign-in page. Mode: Operate.

Audience and job: the single owner, glancing between editor and board to see which agents are alive, stuck, waiting on them, or ready for review; filing new work in one sentence.

Constraints: KeyUp's color themes (owner request, 2026-09-25); six derived columns (Todo, Doing, Needs Input, Failed, In Review, Done) never set by hand; repo filter; live SSE with full resync on reconnect; Doing passes show agent, elapsed, heartbeat liveness (stale after 30s), and cost; composer posts to POST /api/tickets with created_via "web"; the browser never holds the API token. Brief pin: inspiration from Apple Wallet.

Unresolved: Needs Input passes cannot show the clarifying question (the board API does not carry it yet); drag actions arrive in Phase 5.

## Direction contract

THESIS: Every ticket is a Wallet pass and every state is a stack of passes. Refuses the category default of flat grey kanban cards in bordered lanes.

OWN-WORLD: KeyUp's 15 themes, same values, owner-directed on 2026-09-25 (11 dark, 4 light; picker in the top bar, saved per browser; OS preference picks Charcoal or Linen until chosen). A theme sets 16 base tokens and every color derives from them. Pass fills per state come from the theme: Todo the raised surface, Doing the raised surface warmed 35% by the accent (a full accent fill read as Failed where the accent sits near red), Needs Input inverted ink (bone on dark, ink-black on light: the loudest pass, clear of red in every theme), Failed red, In Review rose, Done green; a stalled Doing pass is hollow with a dashed accent outline. Links stay neutral ink so the accent marks only actions and live work. Rounded 14px passes with KeyUp's stacked-card shadow; PassKit anatomy (header strip with repo and issue number, primary field, small-caps label-over-value fields, boarding-pass notch). SF on Apple platforms, self-hosted Geist elsewhere, tabular numerals. Contrast gate: themes.test.ts.

STORY: The owner sees at a glance which stacks hold passes, reads liveness from the Doing stack without opening anything, and taps a pass to see its fields and links. Filing work is one sentence typed into a blank pass at the top.

FIRST VIEWPORT: Top bar: nexTix wordmark left; repo filter, live indicator, sign-out right. Below it, full width, the blank composer pass: one-line prompt, repo, "Write with Claude" toggle, Create. Below, six pass stacks side by side, each titled with state and count; collapsed passes overlap showing only header strips; the newest pass in each stack is open. Mobile: segmented state switcher over one stack.

FORM: Apple Wallet pass stacks (brief-pinned), fused with ticket-rail ageing from my grounded list position 4; seed key aab3b269. Signature interaction: passes travel between stacks with a view transition when GitHub state changes, and a created pass morphs from the composer into its stack. Raise (from the jackfield schedule): no state is carried by color alone; every pass state also has a glyph and a word. Raise (from the oscilloscope bench): time and money readouts are exact and tabular. Raise (from the pop sleeve): flat color at full commitment, no gradients.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
