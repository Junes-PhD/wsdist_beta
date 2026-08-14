# FFXI Simulator Interface Style Guide

This document is the visual source of truth for the simulator. The screenshots
in `_references/` are authoritative visual evidence. `qt_gui_menu.py` is an
implementation study, not the authority. New screens should preserve the game
menu's visual grammar while remaining usable as a desktop analysis tool.

## Reference catalog

Use each screenshot by interaction type instead of treating any one capture as
a complete application mockup.

| Reference | Primary lessons |
|---|---|
| `equipment menu.png` | Three-pane composition, paired title bars, canonical 4x4 equipment grid, metallic slots, inventory list, and item detail pane |
| `Profile.PNG` | Compact character status hierarchy, job blue, bonus green, aligned base/bonus values, and narrow progress presentation |
| `job change.PNG` | Dense two-column choices, capsule job selectors, gold active job, blue secondary job, and left cursor arrow |
| `job levels.PNG` | Two-column read-only data, compact number alignment, gold primary identity, and blue secondary identity |
| `job points.PNG` | Category rail plus instruction bar, selected-row pink, capped-value cyan, right-aligned progress values, and a persistent description pane |
| `job abilities.PNG` | Icon-led action list, selected pink text, one-line rows, and minimal empty chrome |
| `magic.PNG` | Icon-led spell list, narrow custom scrollbar, pink current spell, and tightly packed rows |
| `weapon skill.PNG` | Weapon-family icon repetition, pink current skill, and external left cursor marker |
| `macros.PNG` | Wide 10x2 command grid, compact key hints, metallic cells, and warm selected macro |
| `macro commands.PNG` | Paired title blocks, sparse black command-entry fields, page navigation, and strong input focus |
| `search.PNG` | Mixed icon/text results, aligned metadata columns, muted unavailable rows, cyan names, and warm full-row selection |
| `check.PNG` | Compact 4x4 inspection grid, readable empty-slot labels, character/job header, and a small action choice below the grid |
| `checkparam.PNG` | Console-style aligned stat output and violet timestamps separated from white values |
| `play time.PNG` | Full-width message strip, white system output, yellow NPC speech, and bright lower boundary |
| `timestamp.PNG` | Fixed-width violet timestamp column and color-coded message categories |
| `map.PNG` | Content-specific parchment surface, small translucent overlays, icon-first landmarks, and unobtrusive controls |
| `mogle.PNG` | World-label green and visual context for NPC interactions; not a general panel-layout reference |

When references differ, follow the screenshot matching the interaction being
built. Use equipment-selection rules for slot grids and list-selection rules
for spells, abilities, search results, or other lists.

## 1. Design character

- The interface should feel like an FFXI menu first and a generic desktop
  utility second: dense, framed, dark, and information-forward.
- Use rectangular panels, tight gaps, and crisp borders. Avoid large cards,
  excessive whitespace, generic pill controls, and soft dashboard styling.
- Rounded capsules are reserved for compact category or job choices matching
  the `job change.PNG` pattern.
- Decoration supports hierarchy. Purple and silver define structure; bright
  text carries data; gold, pink, blue, and green have distinct semantic roles.
- Preserve desktop affordances such as resizable splitters, accessible names,
  keyboard focus, and tooltips where the game reference has no equivalent.

## 2. Page composition

- Use a full-window graphite gradient behind primary panels.
- Start major screens with a two-part header: compact title block on the left
  and wider instruction/context block on the right.
- Arrange content as adjacent bordered panes with 2-4 px gaps. The equipment
  reference uses status/context left, equipment center, and inventory/results
  right.
- Keep persistent character/scenario context narrower than the active work
  pane. The divider should look like a silver-purple seam.
- Prefer one strong content frame over several nested card layers.
- Native panels use subtle horizontal-line texture and luminous silver top and
  bottom edges. Desktop implementations may approximate this with a dark
  indigo gradient and crisp light edge; do not substitute a flat gray card.
- Prefer two-column lists when entries are short, parallel, and comparable,
  as in job selection. Use one column when icons, descriptions, or variable
  metadata need more room.

## 3. Equipment presentation

- Equipment always follows the canonical 4x4 FFXI slot map:

  | Main | Sub | Ranged | Ammo |
  |---|---|---|---|
  | Head | Neck | Ear 1 | Ear 2 |
  | Body | Hands | Ring 1 | Ring 2 |
  | Back | Waist | Legs | Feet |

- Do not split weapons and armor in Quick View. Spatial memory is part of the
  interaction, so the same slot always occupies the same cell.
- Use square or slightly landscape metallic tiles with large centered item
  icons. Filled slots do not repeat item names inside the grid.
- Empty slots may show a short slot label. Full names and modeled attributes
  belong in the detail strip and tooltip.
- Center empty-slot labels independently of icon layout. In Qt, do not rely on
  `QPushButton` icon-plus-text positioning: it can reserve icon space and push
  or clip labels such as RANGE and BODY. Use a centered, mouse-transparent
  overlay label and abbreviate only when the native-size cell requires it.
- Hover uses a warm gold border. Selection/current focus uses a stronger warm
  outline. The grid remains compact enough to scan as one object.
- Place a dark item-detail strip immediately below the grid. Use gold for the
  item name, muted lavender for the slot, and white for relevant data.

## 4. Color tokens

| Role | Value | Use |
|---|---|---|
| Window ink | `#171624` | Deepest application background |
| Panel ink | `#17142e` | Lists, editors, and recessed areas |
| Panel violet | `#242344` | Raised panel gradient start |
| Panel edge | `#57536f` | Standard structural border |
| Control edge | `#716893` | Interactive control border |
| Primary text | `#f5f1ff` | Labels and values |
| Muted text | `#d0ccd6` | Secondary explanations |
| Focus gold | `#d6ad68` | Equipment focus and heading accent |
| Item gold | `#e6c983` | Selected item name |
| Menu pink | `#ff6fb3` | Current spell, ability, skill, or list choice |
| Active orange | `#c98232` | Current capsule, macro, or warm row selection |
| Job blue | `#35aee9` | Job/category identity |
| Positive green | `#8cf3b2` | Bonuses, positive state, or world-name reference |
| Capped cyan | `#36bde8` | Completed ranks, secondary job, or capped values |
| Timestamp violet | `#b35cff` | Fixed-width time/log metadata |
| System yellow | `#fff45c` | NPC/system emphasis and important notices |
| Warning amber | `#ffe2a8` | Under-cap state |
| Danger rose | `#ffc4c1` | Over-cap or invalid state |

Do not introduce light surfaces into the main window. State colors use dark
tinted surfaces with readable light foregrounds, not pastel cards. Do not
collapse equipment gold, list pink, and full-row orange into one accent; the
game uses them for different interactions.

## 5. Typography and density

- The reference uses a condensed, slightly italic fantasy UI face with strong
  shadowing. Use Segoe UI Semibold/Italic as the practical control-label
  substitute and Consolas for numeric output, timestamps, or raw data.
- Main titles are 20-22 px and bold. Panel titles are 13-16 px and bold.
- Body controls are 12-14 px. Dense stat/detail text may be 11-12 px.
- Labels are short. Put explanations in the instruction bar, persistent detail
  pane, or tooltips.
- Default panel padding is 4-8 px; adjacent pane spacing is 2-4 px.
- Numeric columns must align. Keep labels left-aligned and values right-aligned
  or tabular; never fake alignment with arbitrary spaces in proportional text.

## 6. Controls and states

- Buttons are dark violet rectangles with a 1 px purple-silver edge.
- Hover brightens violet and changes the edge to gold.
- Equipment selection uses the stronger gold/orange outline seen in the slot
  grid.
- Spell, ability, weapon-skill, and ordinary list selection uses pink text on
  a subtly brighter indigo row.
- Search results and macro cells may use a warm red-brown/orange full-row fill
  when the whole cell is selected. Preserve strong foreground contrast.
- Selected tabs use a warm edge and brighter violet fill.
- Inputs are recessed navy or near-black fields. Avoid native light backgrounds
  in popups, editors, tables, and list views.
- Disabled controls remain legible but recede to charcoal and muted lavender.
- Status/cap feedback uses color plus text; color alone is insufficient.
- Long-running optimizer controls occupy one persistent run bar. Start is the
  dominant gold action, Stop is a distinct danger action, and progress/detail
  buttons remain beside them instead of being split across unrelated rows.
  The main tab must continue to show an explicit `SIMULATION RUNNING` state,
  phase summary, and progress even when the detailed status window is closed.

## 7. Lists, categories, and navigation

- Keep list rows close to icon height with little vertical padding. A list
  reads as one continuous menu, not a stack of independent cards.
- Use a leading icon when it communicates action family, spell element, weapon
  type, status, or availability. Repeated icons may establish category.
- Current spell/action/skill: pink text with a subtle brighter indigo row.
- Current search result or macro: warm full-row or cell highlight.
- Current category/job capsule: gold/orange fill; secondary identity may use
  blue. Do not apply multiple selection treatments to the same control.
- Represent the original left cursor arrow with a slim gold marker or border on
  desktop. It must not shift row content when selection changes.
- Put counts, ranks, levels, costs, and status metadata in stable aligned
  columns. Dim unavailable metadata instead of removing it.
- Long lists use a narrow custom scrollbar with a high-contrast thumb. Avoid a
  wide operating-system scrollbar that dominates the panel.
- Table headers are part of the dark menu frame, never a native white surface.
  Use a dark violet header, light text, crisp one-pixel separators, and a dark
  corner button. Hide vertical row-number headers unless the numbers carry
  domain meaning.
- Keep table rows close to their icon or text height. Do not add cell borders
  around every selected column; a selected record reads as one continuous
  full-row highlight.
- Move explanations longer than one row into a persistent detail pane below or
  beside the list, following `job points.PNG`.
- Detail panes size to their current content up to a small scrolling cap. Do
  not reserve several blank text rows or an empty table body beneath a short
  result; the primary result list should absorb that space.

## 8. Status, values, and progress

- Character identity is the first line. Main job uses blue or gold according
  to context; subjob uses secondary blue or muted text.
- Base attributes and bonuses occupy separate aligned columns. Bonuses use
  green and retain their explicit sign, such as `+137`.
- Completed ranks use cyan plus the numeric cap (`20/20`), not color alone.
  Costs and next-rank values remain muted until actionable.
- Progress bars are thin, framed, and subordinate to numeric labels.
- Dense stat summaries use label/value rows or tabular text instead of
  dashboard cards when direct comparison is the primary task.
- Ordered stat priorities follow the compact menu/list references: show a
  narrow gold ordinal column (`01`, `02`, ...) beside plain white stat names,
  using one or two aligned columns. Do not concatenate rank and stat into
  filled chips such as `1. Fast Cast`; the variable chip widths jumble the
  reading order and do not match the reference menus.
- Cap/target rows must show achieved and target values together (`8 / 80`). If
  the selected modeled inventory cannot reach the target, say that it is the
  best available modeled result instead of implying that generation is
  complete or that an unrelated defensive requirement failed.

## 9. Grids, forms, logs, and specialized surfaces

- Command/macro grids use repeated metallic cells with command name top-left
  and key hint bottom-right. A selected cell uses warm orange.
- Form editors use deeply recessed near-black fields inside the indigo frame.
  Empty command rows remain visible to communicate capacity.
- Log/message views reserve a fixed-width left column for violet timestamps.
  Use white for normal messages, yellow for NPC/system emphasis, orange for
  categories, and green/cyan only for established semantic states.
- Map parchment is a specialized content surface and does not change the main
  application palette. Floating map controls are small, dark, translucent,
  and icon-first.
- World labels and 3D name colors are overlay references, not ordinary desktop
  heading styles.

## 10. Quick View rules

- Quick View is one glanceable composition: equipment and actions on the left,
  live totals/results on the right.
- The 4x4 equipment grid is the dominant object on the left.
- Action buttons form a compact grid below equipment; the current result sits
  directly beneath them.
- Use the result pane for a compact action-specific graph as well as the
  numeric summary. Deterministic evaluations show their real modeled metrics
  (for example, expected TP pace or separately scaled damage and TP-return
  bars); do not imply a sampled distribution or compare unlike units on one
  unlabeled scale.
- Totals remain visible beside equipment where window width permits and scroll
  inside their own pane.
- Stat groups use compact framed sections. Individual values may use tinted
  state cells, but remain subordinate to equipment and use the dark palette.
- The TP → WS cycle uses two equal-width phase cards with the phase name,
  fixed weapons, and a one-line summary visible without opening a picker.
  Run, stop, copy/swap, and distribution actions belong in one compact action
  panel; the current run message belongs in a separate status panel.

## 11. Set workflow rules

- A generated or optimized set is never a dead-end preview. Put its primary
  transfer action beside the set title: load it into Gear Workspace, retain
  its catalog identity while editing, and provide an explicit action to return
  the edited armor to that entry.
- Gear Workspace owns ad-hoc set editing. Exporting one workspace set to LAC
  requires a visible set name, exact diff review, stale-file protection, and a
  timestamped backup; do not force a full-catalog publish for a one-set edit.
- When an optimizer retains alternatives, show no more than the top three in a
  stable rank control near the preview. Applying an alternative changes the
  generated catalog entry, not merely the temporary optimizer workspace.
- Weapon-skill batch tools state the fixed weapon family, enemy, and TP tier.
  Consolidation may use a shared WS base only when the imported profile has an
  actual fallback handler/set for it. Show the grouped WS names and preserve
  distinct overrides when their armor differs materially.
- Inventory-source identity remains visible in candidate lists. Porter-only,
  transferable, and aspirational gear use separate labeled sections; do not
  silently merge unavailable gear into the character's accessible inventory.
- General buff presets and job-ability state are independent layers. Applying
  a Profile Builder party/general preset must preserve checked job abilities
  such as Hasso, Impetus, or Innin unless the user explicitly loads a preset
  intended to replace ability state.

## 12. Acceptance checklist

- Does the screen preserve the canonical 4x4 slot map where equipment appears?
- Can the major panes be understood from silhouette alone?
- Are all primary surfaces dark and all borders intentional?
- Is the correct reference-specific selection treatment used?
- Is the layout compact without clipping at the supported minimum size?
- Are table headers dark, row-number gutters intentional, and detail panes free
  of reserved blank space?
- Are hover, selected, disabled, and cap states visually distinct?
- Does every icon-only equipment control have a tooltip and accessible name?
- Are lists continuous and dense rather than decomposed into modern cards?
- Are numeric metadata columns stable and aligned?
- Are specialized palettes such as map parchment and log colors confined to
  their relevant content surface?
- Can generated sets make a visible round trip through Gear Workspace, and are
  all LAC writes reviewed and recoverable?
