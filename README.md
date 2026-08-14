# wsdist_beta
 
GUI application for simulating damage dealt with various spells and abilities using user-defined gear sets.

The repository archive `_reference/ffxiah_items.tgz` can be used as a local
FFXIAH reference when adding or reviewing gear: it contains item IDs, names,
descriptions, slot/job restrictions, weapon skills, and source stats. Use it to
enrich bridge or catalog records for newly exported GearSetBuilder unknown
items, while leaving effect-only or unresolved descriptions marked incomplete.

You may run the code with as a simple Python file:

    python qt_gui_main.py

or you may simple double-click the qt_gui_main.exe executable. Note that if you try to double-click the executable and it crashes before you can read the error, then you'll need to open a Windows Powershell, navigate to the executable file location, and type "qt_gui_main.exe" to run it from the powershell to read the error causing the crash.

If you choose to use the executable version of this application, then I recommend downloading the application from the actions page for this repository (https://github.com/IzaKastra/wsdist_beta/actions), which contains the executable and necessary files for running it in one simple download. "kastra_ffxi_sim-full" contains all necessary files to run the application. "kastra_ffxi_sim-executable-only" contains only the executable and will not run without the other necessary files. These files from the Actions page were created on GitHub servers using the commands found in the "workflow file" and are therefore considered as "probably safe to run."



Note that the .exe application will not notice any changes made to the .py files (except gear.py and enemies.py). If you wish to make changes to any other file, then you will need to run the qt_gui_main.py version of the code.

## LuAshitacast profile workflow

The Build Dashboard now keeps profile creation to three visible stages:

1. Load a character-specific GearSetBuilder export and LAC job profile.
2. Generate deterministic armor-only base sets from owned gear.
3. Optionally improve TP and weapon-skill sets with combat simulation, then review the exact Lua diff before publishing.

Profile Builder shows the generated catalog as a filterable set list with one
selected 4x4 equipment preview. Direct-stat utility and defense sets are ready
after generation; combat sets are marked as base sets until they are improved.
TP generation includes Default, Accuracy, and High Accuracy versions for both
normal and Hybrid sets; all Hybrid tiers retain the configured PDT/MDT floor.
Existing weapon overlays remain fixed, aspirational gear is excluded from
publishing, and profile writes retain stale-file checks plus timestamped backups.

Generated sets can be loaded into Gear Workspace, edited, returned to their
catalog entry, or exported as one reviewed LAC set. Combat improvement retains
the three best legal optimizer choices. The Build Dashboard can also rank a
fixed weapon family's WS against the Default enemy and group armor sets that
differ by at most two slots; profiles with an existing `Ws_Default` fallback
can publish the largest group as one shared base set. Porter-slip gear is shown
as a distinct optimizer candidate section, and general buff presets preserve
checked job abilities such as Hasso during Profile Builder runs.

## Optimizer search quality and saved work

Optimizer, Build Dashboard, and Profile Builder use the same three search
qualities. Fast performs 6 searches with 4 passes, Standard performs 10
searches with 10 passes, and Deep performs 12 independent searches with 10
passes. Deep never uses a winner from another character or job as a starting
set; it may only restore an exact calculation whose effective inputs match.

Reproducibility numbers are managed internally. When matching work exists, the
GUI offers to reuse it or explore fresh paths. Completed independent restarts
are stored separately, so interrupted and expanded searches can reuse finished
work. Cache storage, precompute tools, and clearing are available from
**File > Performance and storage**.

## Findings and maintenance notes

These are the confirmed behavior and UI findings collected while reviewing the
Build Dashboard, Profile Builder, Optimizer, Gear Workspace, and Simulation
Status screens:

- Hasso is conditional: it requires a two-handed main weapon and its gear
  enhancements belong in Job Ability Haste, not equipment haste. Wakido Kote
  +2 contributes 3% Hasso JA haste (Wakido Kote +3 contributes 4%); the stat
  must disappear when Hasso is not active. Keep this behavior covered by a
  combat regression test.
- Job-specific abilities selected in a general buff preset must survive
  profile generation. In particular, a Samurai Hasso selection must remain
  active for every generated combat loadout that uses a two-handed weapon.
- Earring and set-bonus rules are positional. Sortie earrings with a right-ear
  restriction must not be offered in EAR1, and paired earrings such as
  Steelflash/Bladeborn only grant their pair bonus when both legal earrings are
  equipped.
- Augmented equipment is character-specific for transfer purposes. Dynamis
  Divergence weapon augments apply in the main hand but not the off-hand; only
  the weapon's base off-hand stats may be used. Porter-slip gear remains visible
  as a separate optimizer candidate group.
- Generated sets are a workflow, not just a report: they should round-trip
  between the catalog and Gear Workspace, retain weapon overlays, and export to
  LAC only after review. TP Hybrid generation includes Default, Accuracy, and
  High Accuracy variants; weapon-skill optimization should group equivalent
  armor sets instead of producing one duplicate set per WS.
- Primary screens should hide technical seed numbers and cache internals.
  Results retain the seed in technical metadata and expose a "Repeat exact run"
  action. Deep search may reuse exact matching restarts only; Fast and Standard
  may use a validated shared starting winner after current-candidate and
  restriction checks.
- The three search qualities are fixed at Fast 6x4, Standard 10x10, and Deep
  12x10. The per-run status matrix therefore uses compact cards and a
  three-column layout at normal widths so Standard and Deep remain readable.
- Readability is a correctness feature: center labels in 4x4 gear cells, keep
  controls and status cards compact on resize, use high-contrast yellow checks
  and obvious locked/blacklisted colors, separate stat-priority chips, and
  keep graph legends/mean/range labels inside their plot boxes. Simulation
  status should remain visually distinct from the main FFXI-themed workspace.

### Gear audit checklist

Imported gear should be checked by item name, slot, augment/path suffix, and
the authoritative item description. These records exposed known stat or rule
gaps; use the alternate checks below whenever a new import is added or a
generated profile looks suspicious:

| Gear or rule | Expected model behavior | Import cross-check |
| --- | --- | --- |
| Wakido Kote +2 | Adds 3% Hasso Job Ability Haste only while Hasso and a two-handed main weapon are active; it is not Gear Haste. | Compare Wakido Kote +3 (4%) and Wakido Sune-Ate +2/+3 (their separate Hasso values), with Hasso toggled on and off. |
| Crocea Mors | Includes 20% Fast Cast, 130 HP, and 70 MP on the modeled R25C weapon. | Compare the imported base/name record with the authoritative Crocea Mors entry and another RDM sword to catch missing Fast Cast or HP/MP fields. |
| Bifrost Ring | Uses the item conversion of -70 HP and +70 MP, not two positive flat stats. | Compare the imported item description and raw record with the curated Bifrost entry; do not validate it against ordinary HP/MP rings. |
| Lethargy Earring and other Sortie earrings | The right-ear-only restriction must prevent the item from appearing in EAR1; it may be used in EAR2. | Try the item in both ear slots and compare another Sortie earring for the same positional restriction. |
| Steelflash Earring + Bladeborn Earring | Their special set bonus applies only when the legal pair is equipped together. | Evaluate each earring alone, then as a pair; a solo import must not carry the pair bonus. |
| Rostam and Dynamis Divergence path weapons | Preserve A/B/C path identity and path-specific augments. In the off-hand, use only base weapon stats; do not apply path augments. | Check each A, B, and C item against its R0/base counterpart and test the same weapon in main-hand and off-hand. |
| Sakpata rank variants | Shared Fast Cast/HP/MP/Phalanx values must remain present across R0/R15/R20/R25/R30 while rank augments change. | Compare at least R0, R15, and R30 after import; rank-only attack, accuracy, and augment fields should be the differences. |
| Masamune icon/name variants | Both the base model and R15 model must resolve to the Masamune icon and name. | Check `Masamune0` and `Masamune` in the 4x4 preview and in an exported profile; a blank icon usually indicates an ID/name mapping problem. |

When a discrepancy is found, keep the imported item available for review but
fix the curated model and add a focused regression test before using it for an
optimizer winner. The local `_reference/ffxiah_items.tgz` archive is useful for
checking IDs, slots, jobs, and descriptions; effect-only behavior still needs
an in-game or authoritative source check (for example, the [Hasso reference]
(https://www.bg-wiki.com/ffxi/Hasso)).

I prefer that all issues are reported as issues on the GitHub page. I rarely check FFXIAH, so I may be delayed when responding to posts there.



GUI preview images:

<img src="https://i.imgur.com/XI1MpKP.png" alt="Quicklook tab preview" width="527" height="677">
<img src="https://i.imgur.com/2ASbWhA.png" alt="Optimize tab preview" width="527" height="677">
<img src="https://i.imgur.com/ti7dczS.png" alt="Simulations tab preview" width="527" height="677">
<img src="https://i.imgur.com/NbXwpWf.png" alt="Player Stats tab preview" width="527" height="677">
