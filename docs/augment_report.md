# Rank and augment audit

This is a working report for validating rank-bearing equipment imported from
GearSetBuilder/LuAshitacast. A rank value is an identity/progression marker;
it must not be treated as a combat stat unless the item has a documented
rank-dependent effect.

## How to fill this in

For each row, record the exact information shown by the in-game help text or a
trusted BG-Wiki table:

- **Rank cap**: the maximum rank for this item/path (for example `15`, `25`,
  or `30`).
- **Rank bonuses**: the incremental stats at the observed rank, not the full
  item total. Use simulator stat names where possible (`Accuracy`, `Attack`,
  `DMG`, `Weapon Skill Damage`, `DA`, etc.).
- **Progression-only**: write this when the rank is only a progression marker
  and does not add modeled combat stats.
- **Source**: BG-Wiki URL, an in-game screenshot, or another verifiable source.

Do not guess. If a row is not known, leave the bonus blank and ask for that
item/path specifically before adding it to the calculator.

## Current implementation notes

- The Python bridge preserves `augment_rank`, `augment_path`, and the scanned
  full `stats` record.
- When a producer supplies a separate `augment_rank_stats` payload, the
  bridge now applies it once unless the producer marks it as already included
  in `stats`; the separate payload is also retained for hover text.
- The current bridge export does not consistently populate
  `augment_rank_stats`; therefore a rank can be visible in metadata while its
  separate rank contribution is not visible in hover text.
- Rank metadata is intentionally ignored by `create_player.py`; only numeric
  stats in the item record affect calculations.
- Dynamic rank scaling currently exists for Dynamis-Divergence JSE necks. REMA,
  Ergon, Oboro JSE weapons, and Unity accessories are recognized as single-path
  systems when an older import omits the path; the bridge records that path as A.
- Epeolatry R10 is now modeled from the supplied Stuxnet observation. Other
  intermediate weapon ranks remain table-driven or unresolved until their
  exact path/rank values are supplied.
- Augment paths are now matched as **A through D**. This includes compact
  legacy labels such as `Carmine Cuisses +1D`; a legitimate D path can no
  longer miss its model or fall back to a neighbouring path.
- Exact, reviewed Kroot and Malware observations are applied as separate,
  one-time rank deltas when an import has no decoded rank-stat payload. The
  abbreviated in-game JSE labels are normalized before this lookup.
- The latest Krooti GearSetBuilder export (`wsdist_bridge.json`, generated
  2026-08-14) contains 801 inventory records. Its unknown-stat audit has been
  reconciled below: Beck. Earring +2 now uses the decoded pet/DT payload; the
  remaining records are intentionally left unresolved until a base-stat source
  is available rather than being treated as zero-stat gear.

## Krooti export audit (2026-08-14)

| Item | ID | Export state | Simulator action |
|---|---:|---|---|
| Beck. Earring +2 | 25506 | Augment payload decoded: Pet Accuracy/Ranged Accuracy/Magic Accuracy +16, DT -6%, Pet Store TP +6 | Added to `curated_item_models.py`; applied to bridge records and hover metadata. |
| Eminent Bullet | 21331 | Delirium owns 140 accessible rounds; Krooti has only inaccessible Satchel rounds | Added the item-level 117 ammo model (DMG 238, delay 240). Enable **Use eligible gear from all characters for optimization** to include Delirium's rounds in Krooti's Corsair candidate pool. |
| Bifrost Ring | 11640 | Base stat source missing in export | Already modeled as HP -70 / MP +70; retained and covered by the curated model. |
| Era. Bul. Pouch | 26347 | Base stat source missing | Unresolved; do not assume zero. |
| Light Gorget | 15501 | Base stat source missing | Unresolved; elemental WS modifier needs an authoritative item description. |
| Soil Gorget | 15498 | Base stat source missing | Unresolved; elemental WS modifier needs an authoritative item description. |
| Reraise Ring / Sneak Ring / Noddy Ring / Puffin Ring | 26169 / 26167 / 11655 / 11654 | Base stat source missing | Unresolved utility rings; no combat stats added. |
| Wyvern Feed | 18242 | Base stat source missing | Unresolved pet item; no combat stats added. |
| Argute Bracers -1 / Ptn. Dastanas -1 | 2725 / 2674 | Base stat source missing | Unresolved relic -1 records; no fabricated stats added. |
| Far East Hearth / Net and Lure | 3705 / 3670 | Base stat source missing | Unresolved furnishing records; excluded from combat modeling. |

The export also reports profile-only names that are not present in the
inventory payload (for example Gjallarhorn, Solemnity Cape, and several
augmented job pieces). Those remain visible as profile unknowns until the
GearSetBuilder profile export includes their base-stat records; inventory
absence is not evidence that their stats are zero.

## Highest-priority known gap

| Item | Path | Observed rank | Rank cap | Rank bonuses | Source / notes |
|---|---:|---:|---:|---|---|
| Epeolatry | — | 10 | 15 | **R10 values still need confirmation** | BG-Wiki confirms all REMA/Ergon weapons cap at R15 and lists the R15 package as DMG+39, Dimidiation damage +15%, Accuracy+30, Magic Accuracy+30. The imported R10 record has no decoded rank stats. |

## Observed rank-bearing records

The following rows were found in the character bridge files available during
the audit. Duplicate character copies are collapsed; observed ranks are listed
so the table can be filled in once per item/path.

| Item | Path | Observed ranks | Rank cap | Rank bonuses | Confidence / source |
|---|---|---:|---:|---|---|
| Abyssal Beads +1 | — | 1 |  |  |  |
| Acuity Belt +1 | — | 2 |  |  |  |
| Adhemar Bonnet +1 | A / — | 15 |  |  |  |
| Adhemar Jacket | — | 15 |  |  |  |
| Adhemar Jacket +1 | A / — | 15 |  |  |  |
| Adhemar Wrist. +1 | A / — | 15 |  |  |  |
| Adhemar Wristbands | — | 15 |  |  |  |
| Aettir | — | 15 |  |  | REMA/Ergon weapon; verify rank augment separately. |
| Akademos | B / — | 15 |  |  |  |
| Amalric Doublet | — | 15 |  |  |  |
| Amalric Gages | C | 15 |  |  |  |
| Amalric Nails | C | 15 |  |  |  |
| Amalric Slops | — | 15 |  |  |  |
| Annihilator | — | 7 |  |  | REMA weapon; verify rank augment separately. |
| Argute Stole +1 | — | 1, 5, 7 |  |  |  |
| Asn. Gorget +1 | — | 1 |  |  |  |
| Bagua Charm +1 | — | 1, 5 |  |  |  |
| Bard's Charm +1 | — | 1, 8 |  |  | JSE neck; compare with Dynamis rank rules. |
| Bihu Knife | B | 3 |  |  |  |
| Cabal. Sword | B | 9 |  |  |  |
| Carmine Cuisses +1 | C | 15 |  |  |  |
| Carmine Fin. Ga. +1 | C | 15 |  |  |  |
| Carmine Greaves +1 | A | 15 |  |  |  |
| Carmine Mask +1 | C | 15 |  |  |  |
| Clr. Torque +1 | — | 3, 7, 10 |  |  | JSE neck; compare with Dynamis rank rules. |
| Coiste Bodhar | — | 1, 3, 5, 6 |  |  | Confirm whether these ranks are actual augments or imported progression metadata. |
| Comm. Charm +1 | — | 1 |  |  |  |
| Comm. Charm +2 | — | 6, 14 |  |  |  |
| Compensator | — | 15 |  |  |  |
| Crocea Mors | B | 17 | 25 |  | Dynamis-Divergence weapon; maximum Path B package is documented, but R17 increments remain unresolved. |
| Dgn. Collar +1 | — | 1 |  |  |  |
| Dls. Torque +1 | — | 1 |  |  |  |
| Dls. Torque +2 | — | 15 |  |  |  |
| Dunna | — | 15 |  |  |  |
| Emet Harness +1 | — | 1 |  |  |  |
| Emissary | C | 11 |  |  |  |
| Enchufla | B | 15 |  |  |  |
| Epeolatry | — | 10 | 15 |  | **Highest-priority gap:** R15 cap and max package are known; R10 incremental values are not. |
| Eschite Greaves | — | 15 |  |  |  |
| Etoile Gorget +1 | — | 1 |  |  | JSE neck; compare with Dynamis rank rules. |
| Futhark Torque +2 | — | 16 |  |  | JSE neck; verify cap and rank scaling. |
| Hippo. Socks +1 | — | 15 |  |  |  |
| Ichigohitofuri | — | 15 |  |  |  |
| Kali | — | 15 |  |  |  |
| Kaykaus Cuffs +1 | — | 15 |  |  |  |
| Kgt. Beads +1 | — | 12 |  |  |  |
| Kikoku | — | 2 | 15 |  | REMA weapon; cap is confirmed, but the R2 incremental values still need confirmation. |
| Lathi | B / — | 15 |  |  |  |
| Loxotic Mace +1 | — | 5, 11 |  |  |  |
| Lugra Earring +1 | — | 1 |  |  |  |
| Lustr. Harness +1 | — | 15 |  |  |  |
| Lustr. Subligar +1 | — | 15 |  |  |  |
| Lustra. Leggings +1 | C | 15 |  |  |  |
| Mirage Stole +1 | — | 3 |  |  | JSE neck; compare with Dynamis rank rules. |
| Mirage Stole +2 | — | 6 |  |  | JSE neck; compare with Dynamis rank rules. |
| Mnk. Nodowa +1 | — | 8 |  |  | JSE neck; compare with Dynamis rank rules. |
| Montante +1 | — | 15 |  |  |  |
| Murky Ring | — | 3 |  |  |  |
| Nibiru Cudgel | A | 15 |  |  |  |
| Nibiru Harp | C | 15 |  |  |  |
| Ninja Nodowa +1 | — | 9 |  |  | JSE neck; compare with Dynamis rank rules. |
| Obstin. Sash | — | 2 |  |  |  |
| Pedagogy Staff | B | 7 |  |  |  |
| Plun. Knife | A / B | 1 |  |  |  |
| Priwen | — | 15 |  |  |  |
| Psycloth Lappas | A / C | 15 |  |  |  |
| Pursuer's Beret | — | 11 |  |  |  |
| Pursuer's Cuffs | A / — | 15 |  |  |  |
| Pursuer's Doublet | C | 15 |  |  |  |
| Pursuer's Gaiters | C | 15 |  |  |  |
| Queller Rod | A / C | 15 |  |  |  |
| Rawhide Mask | A | 11 |  |  |  |
| Rawhide Vest | C | 15 |  |  |  |
| Refined Grip +1 | — | 15 |  |  |  |
| Rostam | B / — | 14 / 16 | 25 |  | Dynamis-Divergence weapon; maximum Path B package is documented, but R14/R16 increments remain unresolved. |
| Ryuo Sune-Ate +1 | B | 15 |  |  |  |
| Ryuo Tekko +1 | C | 15 |  |  |  |
| Sagitta | — | 23 | 25 |  | Dynamis-Divergence weapon; path is missing from the import, so only the all-path maximum DMG+12 is confirmed. |
| Sailfi Belt +1 | — | 7, 14, 15 |  |  | Confirm whether these are augments or imported progression metadata. |
| Sam. Nodowa +1 | — | 1 |  |  | JSE neck; compare with Dynamis rank rules. |
| Sam. Nodowa +2 | — | 4, 6 |  |  | JSE neck; compare with Dynamis rank rules. |
| Sandung | — | 15 |  |  | REMA weapon; verify rank augment separately. |
| Schere Earring | — | 1, 2 |  |  | Confirm whether rank changes stats. |
| Scout's Gorget +1 | — | 3 |  |  | JSE neck; compare with Dynamis rank rules. |
| Seeth. Bomblet +1 | — | 1 |  |  |  |
| Smn. Collar +1 | — | 1 |  |  | JSE neck; compare with Dynamis rank rules. |
| Solstice | C | 15 |  |  |  |
| Souv. Cuirass +1 | B | 15 |  |  |  |
| Souv. Diechlings +1 | B | 15 |  |  |  |
| Souv. Handsch. +1 | C | 15 |  |  |  |
| Souv. Schaller +1 | B | 15 |  |  |  |
| Souveran Schuhs +1 | B | 15 |  |  |  |
| Src. Stole +1 | — | 1 |  |  | JSE neck; compare with Dynamis rank rules. |
| Tatena. Gote +1 | — | 15 |  |  |  |
| Tatena. Haidate +1 | — | 15 |  |  |  |
| Tatena. Sune. +1 | — | 15 |  |  |  |
| Vanya Clogs | C | 15 |  |  |  |
| Vanya Cuffs | A / B | 15 |  |  |  |
| Vanya Hood | C | 15 |  |  |  |
| Vanya Robe | B | 15 |  |  |  |
| Vanya Slops | B | 15 |  |  |  |
| War. Beads +1 | — | 1, 8 |  |  | JSE neck; compare with Dynamis rank rules. |
| War. Beads +2 | — | 12 |  |  | JSE neck; compare with Dynamis rank rules. |
| Warder's Charm +1 | — | 1 |  |  |  |

## Resolution log

| Date | Item/path | Decision | Implemented in | Evidence / follow-up |
|---|---|---|---|---|
| 2026-08-14 | Epeolatry / A / R10 | Added exact supplied R10 package; missing path normalizes to A. | `gear.py`, `weapon_bonus.py`, `wsdist_bridge.py` | Stuxnet observation plus [Oboro](https://www.bg-wiki.com/ffxi/Oboro); R15 cap/package cross-checked against [Ultimate Weapon Augments](https://www.bg-wiki.com/ffxi/BGWiki%3AUltimate_Weapon_Augments). |
| 2026-08-14 | Paths Aâ€“D / compact path labels | Fixed the path matcher to recognize D and labels such as `+1D`; rank-specific weapon-skill effects now take precedence over an unranked weapon entry. | `wsdist_bridge.py`, `weapon_bonus.py`, `test_wsdist_bridge.py` | Prevents valid D-path armor from resolving as no path and prevents Kikoku R2 from inheriting the ordinary Blade: Metsu bonus. |
| 2026-08-14 | Kroot and Malware reviewed ranks | Added exact one-time deltas for the supplied Coiste, Loxotic, Ryuo, Sailfi, JSE neck, Schere, Tatena, Warrior's Bead, Akademos, Bihu, Kali, Lathi, Nibiru, Queller, Vanya, Kikoku, and Crocea observations. | `wsdist_bridge.py`, `test_wsdist_bridge.py` | Values remain visible in `Rank Stats`; incomplete/unreviewed report rows stay unresolved. |
| 2026-08-14 | Shared character gear/path/rank matches | Propagated documented values to matching records on other characters (including Sailfi, Tatena, Pursuer, Vanya, Aettir, Sagitta, Souveran, and Summoner's Collar). Single-path records with a missing path are normalized to A; different paths/ranks remain unresolved. | `augment_report.md`, `wsdist_bridge.py` | Exact matching is keyed by normalized item, path, and rank; no values are inferred across a different path or rank. |

## Confidence boundaries from the source audit

These are the rules that can be applied safely from the current scanner and
bridge data. They are deliberately separated from item-specific claims.

| System / record shape | Confidence | Safe interpretation |
|---|---|---|
| Delve item with decoded text such as `STR+12; DEX+12; Attack+20` | High | The decoded text is the fixed path augment. The exported `stats` already contains that contribution; the rank is a progression marker and must not be multiplied again. |
| Dynamis-Divergence JSE neck with a readable base description | High | The scanner subtracts the base description from the maximum displayed values and scales that rank contribution to the item cap. This is the only generic rank-scaling rule currently implemented. |
| Dynamis-Divergence weapon or non-neck accessory with a rank | Not verified | Do not infer a linear rank bonus. The current bridge has no separate rank-stat payload for these records. |
| REMA / Ergon weapon with a rank | Not verified | Do not infer a rank cap or intermediate values from the R15 fallback rows in `gear.py`. A rank-bearing import may currently be using only its base stats. |
| Built-in `gear.py` row at R15/R25/R30 | High for that exact row | It is a manually entered snapshot for that named breakpoint/path, not evidence for every intermediate rank. |

### Verified maximum packages (not intermediate-rank formulas)

These maximum packages are useful for checking the built-in snapshots, but
they must not be back-filled linearly to an imported intermediate rank.

### Mid-rank calculation policy

The source tables show that there is no single universal “max package times
rank” rule. Many primary values are linear or rounded linear values, while
secondary effects unlock at rank thresholds. For example, Sailfi Belt +1 uses
STR equal to rank but Double Attack starts at rank 6 and steps separately.
Odyssey weapons use similar rounded per-rank tables. Therefore the importer
uses this order:

1. Use an exact item/path/rank table when one is available.
2. Use a verified linear rule only for a named stat whose source table proves
   that rule (including its rounding behavior).
3. Keep thresholded effects and weapon-specific WS effects as explicit table
   entries; never interpolate them from the maximum package.
4. If a ranked item has one legal path, normalize a missing path to **A**.
   Missing paths on three-path Dynamis weapons remain unresolved.

This is why the supplied Stuxnet values can be used directly for their exact
ranks, while unlisted intermediate ranks remain marked for confirmation.

- **REMA / Ergon:** maximum rank 15. Epeolatry R15 is DMG+39, Dimidiation
  damage +15%, Accuracy+30, and Magic Accuracy+30.
- **Crocea Mors:** Dynamis-Divergence weapon, maximum rank 25. Path B at the
  maximum is follow-up attack chance +50%, Subtle Blow II +25, and DMG+7.
- **Rostam:** Dynamis-Divergence weapon, maximum rank 25. Path B at the
  maximum is follow-up attack chance +50%, Subtle Blow II +25, and DMG+5.
- **Sagitta:** Dynamis-Divergence weapon, maximum rank 25. Paths A/B/C have
  different effects; all paths add DMG+12 at the maximum. The imported R23
  record does not identify a path, so its path effects remain unresolved.

Sources: [BG-Wiki Ultimate Weapon Augments](https://www.bg-wiki.com/ffxi/BGWiki%3AUltimate_Weapon_Augments),
[Crocea Mors](https://www.bg-wiki.com/ffxi/Crocea_Mors),
[Rostam](https://www.bg-wiki.com/ffxi/Rostam), and
[Sagitta](https://www.bg-wiki.com/ffxi/Sagitta).

### Single-path source rules

- [Oboro](https://www.bg-wiki.com/ffxi/Oboro) documents that REMA/Ergon
  weapons and Oboro JSE weapons display Path A but have no alternate path.
- [JSE Necks](https://www.bg-wiki.com/ffxi/JSE_Necks) documents one path for
  every neck, even where the item help text prints Path A; Divergence weapons
  remain three-path A/B/C items.
- [Unity Accessories](https://www.bg-wiki.com/ffxi/Category:Unity_Accessories)
  documents rank-15, single-path Odyssey augments for Unity +1 accessories.

The bridge uses these rules only when a ranked import has no path token. It does
not infer Path A for a three-path Divergence weapon, so a missing Crocea Mors,
Rostam, or Sagitta path remains visible as unresolved rather than changing the
weapon's modeled effect.

### Items still needing item-specific evidence

The following are intentionally left unresolved. Fill in the exact rank
bonuses and source in the character worksheet before adding them to the
calculator. A blank entry means “unknown”, not zero.

| Item / group | Observed records | Why it is unresolved |
|---|---|---|
| Epeolatry | R10, no path | Resolved: the bridge infers single Path A and the supplied exact R10 package is modeled. |
| Crocea Mors | R17 B | Confirm whether this path has a rank-scaled weapon stat, a fixed path effect, or only the displayed base/path stats. It is a Dynamis-Divergence weapon. |
| Rostam | R14 B; R16 no path | Current records already show path-like stats, but there is no verified intermediate-rank table. It is a Dynamis-Divergence weapon. |
| Sagitta | R23, no path | Current record has base weapon stats but no decoded rank contribution. |
| Aettir, Annihilator, Kikoku, Sandung | R2/R7/R15 samples | BG-Wiki confirms REMA/Ergon cap at R15; the bridge still does not provide the intermediate rank contributions needed for these imported ranks. |
| Coiste Bodhar, Sailfi Belt +1, Schere Earring | Multiple low/intermediate ranks | Repeated records have identical exported combat stats at different ranks; determine whether rank is merely item metadata or whether a hidden effect is missing. |
| Rank-bearing JSE necks | R1-R16 samples | The generic Dynamis-neck scaler is safe only when the resource description provides a reliable unaugmented baseline. Each fallback/unknown-base item needs a source check. |

### Current evidence from live bridge files

- Every sampled rank record had `augment_rank_stats = null`.
- Delve records commonly had decoded fixed augments and `stats` already
  included them. For example, Adhemar Bonnet +1 A exported
  `STR+12; DEX+12; Attack+20`, while the non-A record exported
  `DEX+12; AGI+12; Accuracy+20`.
- Epeolatry R10 originally exported only `DMG 305`, `PDT -25`, weapon skills,
  and magic damage; the supplied Stuxnet package now fills the missing exact
  R10 contribution without changing the raw scan record.
- Crocea Mors R17 B exported its weapon/path stats but no separate rank
  payload.
- Rostam R14 B and R16 (no path) exported the same combat-stat shape, so the
  difference cannot safely be interpreted as an intermediate-rank formula.

Until the unresolved rows are filled, the simulator should preserve their
rank/path metadata for display and matching, but must not invent or scale
additional combat stats.

## Character-specific worksheets

These sections preserve the exact character/path/rank combinations found in
the bridge files. Fill in the rank bonuses here first; the consolidated table
above can then hold the shared item rule.

### Delirium_272177

| Item | Path | Rank | Rank bonuses | Source / notes |
|---|---|---:|---|---|
| Adhemar Bonnet +1 | B | 15 |  |  |
| Adhemar Jacket | A | 15 |  |  |
| Adhemar Jacket +1 | B | 15 |  |  |
| Adhemar Wrist. +1 | B | 15 |  |  |
| Akademos | C | 15 |  |  |
| Amalric Doublet | A | 15 |  |  |
| Amalric Gages | D | 15 |  |  |
| Amalric Nails | D | 15 |  |  |
| Amalric Slops | A | 15 |  |  |
| Carmine Cuisses +1 | D | 15 |  |  |
| Carmine Mask +1 | D | 15 |  |  |
| Comm. Charm +2 | A | 6 |  |  |
| Mirage Stole +2 | A | 6 |  |  |
| Nibiru Cudgel | B | 15 |  |  |
| Psycloth Lappas | B | 15 |  |  |
| Rawhide Mask | B | 11 |  |  |
| Sailfi Belt +1 | A | 7 |  |  |
| Solstice | D | 15 |  |  |
| Vanya Robe | C | 15 |  |  |

### Kroot_361003

| Item | Path | Rank | Rank bonuses | Source / notes |
|---|---|---:|---|---|
| Adhemar Bonnet +1 | A | 15 | DEX +12; AGI +12; Accuracy +20 | Kroot observed import |
| Adhemar Jacket +1 | A | 15 |  |  | DEX +12; AGI +12; Accuracy +20 | Kroot observed import |
| Adhemar Wrist. +1 | A | 15 |  |  |  DEX +12; AGI +12; Accuracy +20 | Kroot observed import |
| Akademos | A | 15 | MP +80; INT +20; Magic Attack +20 | Kroot observed import |
| Argute Stole +1 | A | 5 | INT +3; MND +3; Magic Damage +5 | Helix duration is not modeled |
| Asn. Gorget +1 | A | 1 | DEX +1; AGI +1; TA +1% | Kroot observed import |
| Bagua Charm +1 | A | 1 | MP +1 | Luopan effects are not modeled |
| Bard's Charm +1 | A | 8 | DEX +8; CHR +8; Store TP +2; PDL +3% | Kroot observed import |
| Bihu Knife | C | 3 | DA +1% | Main-hand song effects/casting time are not modeled |
| Carmine Cuisses +1 | D | 15 |  |  | Accuracy +20 attack +12 Dual Wield +6
| Carmine Greaves +1 | B | 15 |  |  | accuracy +12 dex +12 mnd +20 
| Clr. Torque +1 | A | 3 | INT +2; MND +2; Fast Cast +1% | Enmity is not modeled |
| Coiste Bodhar | A | 1 | Attack +1 | Kroot observed import |
| Comm. Charm +1 | A | 1 |  |  | STR +1 AGI +1 Magic Damage +1 MAB +1
| Crocea Mors | C | 17 | DMG +5; Elemental WS Damage +60%; EnSpell Damage +340% | Dynamis-Divergence weapon |
| Dgn. Collar +1 | A | 1 | STR +1; VIT +1; PDL +1% | Wyvern effect is not modeled |
| Dls. Torque +2 | A | 15 | INT +10; MND +10 | Spell-duration effects are not modeled |
| Ichigohitofuri | A | 15 |  |   | DMG +30 STR +20 Attack +20
| Kali | A | 15 | DMG +15; CHR +15; Magic Accuracy +15 | Kroot observed import |
| Kaykaus Cuffs +1 | A | 15 |  |  | MP +80 MND +12 Magic Accuracy +20
| Kikoku | A | 2 | DMG +1; Blade: Metsu Damage +2% | Ninjutsu cast-time effect is not modeled |
| Lathi | A | 15 | MP +80; INT +20; Magic Attack +20 | Kroot observed import |
| Lugra Earring +1 | A | 1 |  |  | Def +1
| Mirage Stole +1 | A | 3 | STR +3; DEX +3; Store TP +1; Crit Rate +1% | Kroot observed import |
| Nibiru Harp | D | 15 | Magic Evasion +20; PDT -3%; MDT -3% | Kroot observed import |
| Ninja Nodowa +1 | A | 9 |  |  | Dex +5 AGI +5 Daken +9 PDL +3%
| Psycloth Lappas | D | 15 |  |  | MP +80 Magic accuracy +15 Fast Cast +7%
| Pursuer's Beret | A | 11 |  |  | AGI +8 Rapid Shot +8 Subtle Blow +5
| Pursuer's Cuffs | A | 15 |  |  | AGI +10 Rapid Shot +10 Subtle Blow +7
| Pursuer's Gaiters | D | 15 |  |  | Range Accuracy +10 Rapid Shot +10% Recycle +15
| Queller Rod | B | 15 | MND +15; Magic Accuracy +15 | Cure potency is not modeled in WS calculations |
| Rawhide Vest | D | 15 |  |  | HP +50 Subtle Blow +7 TA+2%
| Sailfi Belt +1 | A | 14 |  |  | STR +14 DA +5%
| Seeth. Bomblet +1 | A | 1 |  |  | STR +1
| Smn. Collar +1 | A | 1 |  |  | MP +1 Avatar All Attributes +1 Blood Pact Damage +1
| Vanya Cuffs | B | 15 |  |  | Healing Magic Skill +20 Cure Spell Casting time - 7% MDT -3%
| Vanya Hood | D | 15 | MP +50; Fast Cast +10%; Gear Haste +2% | Kroot observed import |
| Vanya Slops | C | 15 |  |  | MND +10 SIRD 15% Conserve MP +6

### Krooti_512814

| Item | Path | Rank | Rank bonuses | Source / notes |
|---|---|---:|---|---|
| Acuity Belt +1 | A | 2 |  |  | Magic Accuracy +2
| Adhemar Jacket | A | 15 |  |  | DEX +10 AGI +10 Accuracy +15
| Adhemar Wristbands | A | 15 |  |  | Dex +10 AGI +10 Accuracy +15
| Akademos | C | 15 |  |  | INT +15 MAB +15 Magic Accurcy +15
| Annihilator | A | 7 |  | REMA weapon; verify rank augment. | DMG +4 Coronach DMG +7% Store TP +2
| Argute Stole +1 | A | 5 | INT +1; MND +1; Magic Damage +1 | Helix duration +1 |
| Asn. Gorget +1 | A | 1 |  |  | Dex +1 AGI +1 Evasion +1 TA +1%
| Bagua Charm +1 | A | 5 |  |  | MP +10 Luopan duration +5% Luopan: Absorbs danage taken +2%
| Bard's Charm +1 | A | 1 |  |  | Dex +1 CHR +1 PDL +1%
| Carmine Cuisses +1 | D | 15 |  |  | Accuracy +20 Attack +12 Dual Wield +6
| Carmine Fin. Ga. +1 | D | 15 |  |  | Range Attack +20 MAB +12 STP +6
| Carmine Greaves +1 | B | 15 |  |  | Accuracy +12 Dex +12 MND +20
| Clr. Torque +1 | A | 10 |  |  | Int +5 MND +5 Enmity -10 Fast Cast +4%
| Coiste Bodhar | A | 5 |  |  | Attack +5
| Comm. Charm +2 | A | 14 |  |  | STR +9 AGI +9 Magic Damage +14 MAB +4
| Compensator | A | 15 |  |  | DMG +15 AGI +15 Ranged Attack +15
| Dls. Torque +1 | A | 1 |  |  |
| Dunna | A | 15 |  |  | MP +20 Magic Accuracy +10 Fast Cast +3%
| Emissary | D | 11 |  |  | Magic Accuracy +11 MAB +16
| Enchufla | C | 15 |  |  | DMG +15 DEX +15 Subtle Blow +7
| Etoile Gorget +1 | A | 1 |  |  |
| Lathi | C | 15 |  |  | INT +15 MAB +15 Magic Accurcy +15
| Nibiru Cudgel | B | 15 |  |  | MP +50 Int +10 MAB +15
| Plun. Knife | B | 1 |  |  |Main Hand: Chance of follow up attack +1% Subtle Blow II +1%
| Plun. Knife | C | 1 |  |  | Main Hand: Evasion +1 TP During Evasion +1
| Pursuer's Cuffs | B | 15 | Dex +7 AGI +10 Recycle +15 
| Pursuer's Doublet | D | 15 |  |  | HP +50 Critical Hit Rate +4% Snapshot +6
| Pursuer's Gaiters | D | 15 |  |  | Ranged Accuracy +10 Rapid Shot +10 Recycle +15
| Queller Rod | D | 15 |  |  | Healing Magic Skill +15 Cure Potency +10% Cure Spell Casting time -7%
| Rawhide Vest | D | 15 |  |  | HP +50 Subtle Blow +7 TA +2%
| Rostam | A | 16 |  | Dynamis-Divergence weapon. | Main Hand: Chance of Double Damage +32% Store TP +16 DMG +3
| Rostam | C | 14 |  | Dynamis-Divergence weapon. |Main Hand: Phantom Roll effect duration +27 Phantom Roll 11 Recover HP and MP +9% Phantom Rolls +5
| Sandung | — | 15 |  | REMA weapon; verify rank augment. |
| Scout's Gorget +1 | A | 3 |  |  | AGI +3 STP +1 PDL +1%
| Smn. Collar +1 | A | 1 | MP +1; Avatar: All Attributes +1; Blood Pact Damage +1 | Missing path normalized to single-path A; copied from reviewed Kroot record |
| Src. Stole +1 | A | 1 |  |  |
| Vanya Cuffs | C | 15 |  MND +10 SIRD 15% Conserve MP +6
| War. Beads +1 | A | 1 |  |  |

### Malware_512810

| Item | Path | Rank | Rank bonuses | Source / notes |
|---|---|---:|---|---|
| Coiste Bodhar | A | 3 | Attack +3 | Malware observed import |
| Loxotic Mace +1 | A | 11 | DMG +25; Accuracy +24; Magic Accuracy +24; Weapon Skill Damage +2% | Malware observed import |
| Ryuo Sune-Ate +1 | C | 15 | HP +65; Store TP +5; Subtle Blow +8 | Malware observed import |
| Sailfi Belt +1 | A | 15 | STR +15; DA +5% | Malware observed import |
| Sam. Nodowa +2 | A | 6 | STR +6; Store TP +2; PDL +2% | Malware observed import |
| Schere Earring | A | 2 | Accuracy +2 | Malware observed import |
| Tatena. Haidate +1 | A | 15 | Accuracy +60; All Attributes +10; TA +3% | Malware observed import |
| War. Beads +2 | A | 12 | HP +37; STR +7; DEX +7; DA +4% | Malware observed import |

### Mocha_565671

| Item | Path | Rank | Rank bonuses | Source / notes |
|---|---|---:|---|---|
| Coiste Bodhar | A | 5 |  |  |
| Ryuo Sune-Ate +1 | C | 15 |  |  |
| Sailfi Belt +1 | A | 15 | STR +15; Double Attack +5% | Missing path normalized to single-path A; copied from reviewed Malware/Stuxnet record |
| Sam. Nodowa +2 | A | 4 |  |  |
| Tatena. Gote +1 | A | 15 |  |  | accuracy +40 all attributes +10 (STR VIT DEX etc) Triple attack +4%
| Tatena. Haidate +1 | A | 15 | Accuracy +60; All Attributes +10; Triple Attack +3% | Missing path normalized to single-path A; copied from reviewed Kroot/Malware record |
| Tatena. Sune. +1 | A | 15 | Accuracy +60; All Attributes +10; Triple Attack +3% | Missing path normalized to single-path A; copied from reviewed Stuxnet record |

### Pilfered_337602

| Item | Path | Rank | Rank bonuses | Source / notes |
|---|---|---:|---|---|
| Argute Stole +1 | A | 7 |  |  |
| Clr. Torque +1 | A | 7 |  |  |
| Murky Ring | A | 3 |  |  |
| Obstin. Sash | A | 2 |  |  |
| Pedagogy Staff | C | 7 |  |  |
| Psycloth Lappas | S | 15 |  |  |
| Queller Rod | D | 15 |  |  |
| Vanya Clogs | D | 15 |  |  |
| Vanya Hood | D | 15 |  |  |

### Stuxnet_119614 (normalized interpretation)

The raw Stuxnet observations below use an extra free-text column. The
following rules are now applied when interpreting them:

- A ranked one-path item with no path token is treated as **Path A**.
- A three-path Dynamis weapon with no path token stays unresolved; it is not
  silently changed to A.
- The supplied values are rank-specific additions. They are not added again
  when the bridge already includes the same decoded augment in `stats`.
- Exact rank values take precedence over interpolation. Only a stat proven to
  be linear by its source table may use a rounded linear rule.

High-priority supplied values now normalized for the model audit (the exact
Epeolatry R10 package is implemented; the other rows remain source records
until their item totals and rank paths are independently matched):

| Item | Path | Rank | Rank additions |
|---|---|---:|---|
| Epeolatry | A | 10 | DMG +24; Dimidiation damage +10%; Accuracy +19; Magic Accuracy +19 |
| Sagitta | A | 23 | Chance of Double Damage +46%; Store TP +23; DMG +11 |
| Aettir | A | 15 | Accuracy +70; Magic Evasion +50; Weapon Skill Damage +10% |
| Sailfi Belt +1 | A | 15 | STR +15; Double Attack +5% |
| War. Beads +1 | A | 8 | HP +21; STR +4; DEX +4; Double Attack +2% |

The remaining Stuxnet rows are retained as source observations below and will
be promoted only after their base totals and supported simulator stat names
are matched.

Reference pages supplied for this audit: [Dynamis Divergence Weapon
Augments](https://www.bg-wiki.com/ffxi/Dynamis_Divergence_Weapon_Augments),
[JSE Necks](https://www.bg-wiki.com/ffxi/JSE_Necks),
[Oboro](https://www.bg-wiki.com/ffxi/Oboro), and
[Unity Accessories](https://www.bg-wiki.com/ffxi/Category:Unity_Accessories).

### Stuxnet_119614 (raw notes supplied)

| Item | Path | Rank | Rank bonuses | Source / notes |
|---|---|---:|---|---|
| Abyssal Beads +1 | A | 1 |  |  | str +1 store tp +1 PDL +1%
| Adhemar Bonnet +1 | B | 15 |  |  | Str +12 Dex +12 attack +20  
| Adhemar Jacket +1 | B | 15 |  |  |  Str +12 Dex +12 attack +20
| Adhemar Wrist. +1 | A | 15 |  |  | Dex +12 AGI +12 accuracy +20 
| Aettir | A | 15 | Accuracy +70; Magic Evasion +50; Weapon Skill Damage +10% | Missing path normalized to single-path A; reviewed Stuxnet value |
| Asn. Gorget +1 | A | 1 |  |  | dex +1 agi +1 evasion +1 triple attack +1%
| Cabal. Sword | C | 9 |  |  | Dynamis weapon max rank 20 current rank hp + 90 cure potency + 9% refresh +1 
| Carmine Cuisses +1 | D | 15 |  |  | accuracy +20 attack +12 dual wield +6
| Carmine Greaves +1 | B | 15 |  |  | accuracy +12 dex +12 mnd +20 
| Coiste Bodhar | A | 6 |  |  | attack +6
| Dgn. Collar +1 | A | 1 |  |  |str +1 vit +1 pdl +1% wyvern: damage taken -1%
| Emet Harness +1 | A | 1 |  |  | evasion +2
| Epeolatry | — | 10 |  | REMA/Ergon weapon; rank stats currently missing. | dmg +24 dimidiation dmg +10% look at BGwiki for formuL notes acurracy +19 magic accuracy +19 
| Eschite Greaves | A | 15 |  |  | hp +80 enmity +7 pdt -4%
| Futhark Torque +2 | A | 16 |  |  | HP =33 Str +10 MND +10 DT -5%
| Hippo. Socks +1 | A | 15 |  |  | Resist Bind +45 Evasion +20 All Attributes +10
| Kgt. Beads +1 | — | 12 |  |  | hp +24 vit +7 MND +7 DT -4%
| Loxotic Mace +1 | — | 5 |  |  | dmg +13 
| Lugra Earring +1 | — | 1 |  |  | def +1
| Lustr. Harness +1 | A | 15 |  |  |attack +20 str +8 Double attack +3%
| Lustr. Subligar +1 | A | 15 |  |  | attack +20 str +8 double attack +3%
| Lustra. Leggings +1 | D | 15 |  |  | HP +65 Str +15 Dex +15
| Mnk. Nodowa +1 | — | 8 |  |  | dex +4 mnd +4 kick attacks +8 PDL +3%
| Montante +1 | A | 15 |  |  |  dmg +20 accuracy +40 magic accuracy +40 HP +100
| Priwen | A | 15 |  |  | hp +50 magic evasion +50 dt -3%
| Refined Grip +1 | A | 15 |  |  | Def +20 Parry Skill +10
| Ryuo Tekko +1 | D | 15 |  |  | Dex +12 Accuracy +25 Double attack +4%
| Sagitta | A | 23 |  | Dynamis weapon; verify rank model. | chance of double damage +46% store tp +23 dmg + 11
| Sailfi Belt +1 | A | 15 | STR +15; Double Attack +5% | Missing path normalized to single-path A; reviewed Malware/Stuxnet value |
| Sam. Nodowa +1 | — | 1 |  |  | str +1 store tp +1 PDL +1%
| Schere Earring | — | 1 |  |  | accuracy +1
| Smn. Collar +1 | A | 1 | MP +1; Avatar: All Attributes +1; Blood Pact Damage +1 | Missing path normalized to single-path A; reviewed Kroot value |
| Souv. Cuirass +1 | C | 15 | HP +105; Enmity +9; Potency of Cure Effect Received +15% | Reviewed Stuxnet value |
| Souv. Diechlings +1 | C | 15 | HP +105; Enmity +9; Potency of Cure Effect Received +15% | Reviewed Stuxnet value |
| Souv. Handsch. +1 | D | 15 | HP +65; Shield Skill +15; PDT -4% | Reviewed Stuxnet value |
| Souv. Schaller +1 | C | 15 | HP +105; Enmity +9; Potency of Cure Effect Received +15% | Reviewed Stuxnet value |
| Souveran Schuhs +1 | C | 15 | HP +105; Enmity +9; Potency of Cure Effect Received +15% | Reviewed Stuxnet value |
| Tatena. Gote +1 | A | 15 |  |  | accuracy +40 all attributes +10 (STR VIT DEX etc) Triple attack +4%
| Tatena. Haidate +1 | A | 15 |  |  | accuracy +60 all attributes +10 (STR VIT DEX etc) Triple attack +3%
| Tatena. Sune. +1 | A | 15 |  |  | accuracy +60 all attributes +10 (STR VIT DEX etc) Triple attack +3%
| War. Beads +1 | — | 8 |  |  | hp+21 str +4 dex +4 double attack +2%
| Warder's Charm +1 | — | 1 |  |  | Skillchain damage + 1%
