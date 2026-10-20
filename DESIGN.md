---
name: Deletarr
description: A cinematic control room for deliberate media cleanup.
colors:
  background: "#101113"
  surface: "#191b1e"
  raised: "#24272b"
  border: "#30343a"
  text: "#f0f1f3"
  muted: "#a6abb4"
  sidebar: "#151619"
  accent: "#b0ed83"
  accent-text: "#162016"
  destructive: "#ffaaa5"
  light-background: "#f4f5f7"
  light-surface: "#ffffff"
  light-raised: "#eaedf0"
  light-border: "#d8dde3"
  light-text: "#20252d"
  light-muted: "#596370"
  light-sidebar: "#eceff2"
  light-destructive: "#a33830"
typography:
  headline:
    fontFamily: '"Manrope Variable", sans-serif'
    fontSize: "32px"
    fontWeight: 650
    lineHeight: 1.25
    letterSpacing: "-0.035em"
  title:
    fontFamily: '"Manrope Variable", sans-serif'
    fontSize: "15px"
    fontWeight: 650
    letterSpacing: "-0.02em"
  body:
    fontFamily: '"Manrope Variable", sans-serif'
    fontSize: "14px"
    lineHeight: 1.6
  label:
    fontFamily: '"Manrope Variable", sans-serif'
    fontSize: "12px"
rounded:
  badge: "5px"
  control: "8px"
  strip: "12px"
  panel: "14px"
  dialog: "16px"
spacing:
  control: "9px 13px"
  panel-heading: "19px 22px"
  content: "34px 36px"
  mobile-content: "26px 18px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-text}"
    rounded: "{rounded.control}"
    padding: "{spacing.control}"
  panel:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.panel}"
  input:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
---

# Design System: Deletarr

## Overview

**Creative North Star: “The cinematic control room.”** Neutral charcoal surfaces provide a quiet frame for the user's actual library. Pale green marks useful actions and selected details; clear hierarchy puts cleanup decisions and storage context before secondary activity. Expression comes from composition, typography, and a translucent context header rather than decoration.

The implementation is the visual authority. `web/src/main.tsx` imports `style.css`, then `themes.css`, then `workspace.css`. The last sheet supplies the replacement neutral shell and responsive composition; existing component behavior and the optional anime worlds remain in place. `PRODUCT.md` owns product truth.

**Key characteristics:** compact operational typography, broad breathing room between decisions, thin borders, restrained tonal layers, real artwork, and explicit safety context.

Taste provided no learned profile for this session. A [community reference](https://buildwithtaste.com/nitin/5d64b296-a6fe-4ff6-9ee1-53da97b92305) informed visual restraint. The user's annotation “love the floating piece” on [this reference](https://buildwithtaste.com/alexhoover/03b048a5-33ab-4448-a8e6-ccc1749b88cd) informed the sticky floating-context header for this redesign; it is not a global preference. Reference screenshots are not shipped assets.

## Colors

### Primary

Pale green is the default accent. It supplies primary buttons, focus rings, the logo mark, selected navigation icons, and small status details. The administrator's custom accent is preserved for System, Dark, and Light. Destructive meaning uses independent red treatment and explicit labels.

### Neutral

The frontmatter records the current neutral dark and light values from `workspace.css`. Background, sidebar, surface, and raised layers separate the page, navigation, working panels, and selected states. Use muted text for supporting evidence, never as the only distinction for action or status. System follows the OS color preference; Light uses the corresponding light tokens.

### Optional worlds

All three dark anime palettes remain available in Appearance. `themes.css` owns their complete surface and text overrides; its theme-specific selectors retain precedence over the neutral root defaults. One Piece uses ocean blue, treasure gold (`#f4c56a`), and compass geometry. Black Clover uses grimoire black, emerald (`#91e0ac`), and clover/bookplate geometry. High School DxD uses crimson, midnight violet, rose (`#ef9fbe`), and arcane rings. Their original local SVG motifs are decorative. Presets retain fixed signature accents, readable text, independent destructive colors, shared layout, and all safety behavior; System light-mode selectors do not override these dark presets. The picker previews each world before saving.

## Typography

Manrope Variable is locally hosted through `@fontsource-variable/manrope`; no external font service is required. The base is 14px/1.6, with 12–13px operational copy, 10–12px supporting metadata, and 32px/1.25 page headings at weight 650. Compact page headings are 28px. Panel headings are 15px; cleanup focus copy scales from 23px to 30px, with a 26px compact override. Library summary metrics use 25px tabular figures, 24px on compact screens. Paragraphs retain a 75ch maximum; paths and technical output use the system monospace stack and wrap or scroll as appropriate.

## Layout

The desktop frame uses a fixed 212px sidebar, a 64px sticky topbar, and a content region capped at 1540px with 34px vertical/36px horizontal padding. The topbar keeps the route and dry-run/live status visible while scrolling.

The Overview dashboard uses an unboxed four-column library summary above a two-column cleanup/storage region (`1.13fr 1fr`, 24px gap). A single four-service connection strip follows. Recently watched artwork and upcoming activity are secondary, in a `1.25fr 1fr` region. Empty states describe the next real action. Candidate size is explicitly a logical estimate, not a promise of reclaimed capacity.

Responsive thresholds come from the final stylesheet:

- At 1200px and below: 28px content padding, narrower candidate artwork/actions, and a 160px settings rail.
- At 1000px and below: dashboard regions stack, connections become two columns, and candidate actions move beneath the main candidate information.
- At 900px and below: sidebar narrows to 190px, library metrics become two columns, and settings navigation becomes wrapping grouped rows above the form.
- At 640px and below: the main region fills the viewport, sidebar becomes a 232px toggle-controlled drawer, topbar is 60px, content padding is 26px/18px, and poster grids use two columns. Candidate actions span beneath artwork and information; secondary actions wrap after expansion.

Dense tables retain their field relationships in horizontal scrolling containers. Settings groups are Workspace, Connections, Library & cleanup, and Administration; on compact screens their headings remain visible above wrapping controls.

## Elevation & Depth

Depth comes mainly from tonal surfaces and one-pixel borders. Ordinary panels and primary buttons have no decorative shadow. The sticky topbar uses `color-mix(in srgb, var(--bg) 90%, transparent)` and 16px backdrop blur. Dialogs use `0 25px 100px #0008`; their backdrop uses `#07090db8` and 5px blur. The compact menu overlays a `#0009` backdrop with 4px blur and a `10px 0 50px #0006` drawer shadow.

Motion is limited to state transitions: 150ms button backgrounds, 200ms drawer movement, and a subtle 1.025 poster-image hover scale. Reduced-motion preferences minimize global animation/transition duration and remove poster-image transitions.

## Shapes

Panels and review candidates use 14px corners; the connection strip and onboarding banner use 12px. Controls use 8px, badges 5px, dialogs 16px, and the mode indicator a pill. Posters keep their native portrait proportion, including a 2:3 watch-shelf ratio. Thin borders define structure without enclosing every summary statistic in a card.

## Components

### Buttons and fields

Primary buttons use the accent and accent-text colors, 9px/13px padding, and a 39px minimum height (40px compact). Secondary buttons retain surface/border treatment; quiet buttons reduce visual weight while keeping hover feedback. Destructive buttons retain their separate red treatment. Disabled buttons reduce opacity and use a not-allowed cursor. Inputs, selects, and textareas use a surface background, 8px radius, 10px/12px padding, and a 40px minimum height; checkbox and radio controls keep native compact dimensions.

### Navigation and context

The active sidebar item uses a raised neutral background, text label, and accent icon. `aria-current` marks the selected route. The compact menu button exposes its expanded state, hidden navigation is inert, Escape dismisses the drawer, and a labelled backdrop button closes it. Secondary header icon buttons hide on compact screens; the mode indicator remains visible. The skip link appears on focus and explicitly focuses `main[tabindex="-1"]` without changing the hash-based route.

### Cleanup review

Candidate rows keep artwork, eligibility, last-watched evidence, exact-file counts, and logical bytes together. “Inspect plan” and “Approve” remain visible where applicable. Native `details`/`summary` exposes Postpone 7 days, Keep Forever, and Reject under “More actions”; another disclosure retains eligibility and exact paths. This reduces repeated controls without removing review evidence or secondary actions.

### Settings and safety

Grouped settings navigation uses labelled navigation and groups; the selected section is reflected by `aria-current`. Fields retain explicit labels and separate help descriptions. Native dialogs retain focus and Escape behavior; destructive activation uses protected initial focus, password entry, and explicit acknowledgment. Dry-run/live state stays visible, exact-plan inspection precedes confirmation of count and bytes, and failures remain actionable. This redesign does not alter the safety workflow.

## Do's and Don'ts

- **Do** give the next cleanup decision and storage context the strongest dashboard hierarchy.
- **Do** preserve native disclosures, visible keyboard focus, labelled controls, reduced motion, and honest empty/loading/error states.
- **Do** use real Plex artwork and retain all appearance presets and administrator accent choices.
- **Do** wait for navigation transitions and dismiss transient notifications when capturing stable screenshot evidence.
- **Don't** present logical file size as guaranteed reclaimed space, invent analytics, or add promotional imagery to the operational app.
- **Don't** hide dry-run/live context or weaken the protected activation and exact-plan confirmation flows.
- **Don't** translate this task's Taste annotation into a permanent preference for every future surface.
