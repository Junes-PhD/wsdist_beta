# WSDist stat reference

This is the vocabulary used by the simulator's gear importer, player builder,
combat formulas, optimizer, and GUI.  Names in backticks are the exact stat
keys used in `gear.py` and `Player.stats`.  A percentage value is stored in
percentage points unless a formula explicitly says otherwise (for example,
`ftp` is a multiplier). `main` and `sub` mean the main-hand and off-hand
weapon respectively.

## Reading the abbreviations

| Abbreviation | Meaning |
| --- | --- |
| ACC / M.ACC | Accuracy / Magic Accuracy |
| ATK / M.ATK | Attack / Magic Attack |
| DEF / M.DEF | Defense / Magic Defense |
| EVA / M.EVA | Evasion / Magic Evasion |
| DA / TA / QA | Double / Triple / Quadruple Attack |
| OA | Occasionally attacks additional times; `OA2` through `OA8` are the modeled maximum hit counts |
| FUA | Follow-up attack |
| DW / MA | Dual Wield / Martial Arts |
| STP | Store TP |
| TP Bonus | TP added before the 1,000–3,000 TP WS cap |
| WSC | Weapon-skill correction/stat contribution |
| fTP | Weapon-skill damage multiplier; the code key is lowercase `ftp` |
| WSD | Weapon Skill Damage |
| PDL | Physical Damage Limit |
| DT / PDT / MDT | Damage Taken / Physical Damage Taken / Magic Damage Taken |
| FC | Fast Cast |
| JA Haste | Job-ability haste, separate from gear and magic haste |
| dSTAT | The relevant player-minus-enemy magic WS stat difference |
| pDIF | Physical damage multiplier derived from attack and defense |
| SC | Skillchain |

## Player attributes

| Key | Abbreviation / meaning |
| --- | --- |
| `STR` | Strength; physical attack and WS modifier stat |
| `DEX` | Dexterity; accuracy, critical-rate dDEX, and WS modifier stat |
| `VIT` | Vitality; defense and WS modifier stat |
| `AGI` | Agility; evasion, ranged/critical interactions, and WS modifier stat |
| `INT` | Intelligence; magic damage, magic WS, and WS modifier stat |
| `MND` | Mind; magic damage, magic WS, and WS modifier stat |
| `CHR` | Charisma; enfeebling/support and WS modifier stat |
| `HP` | Hit points |
| `MP` | Magic points |

## Combat skill and weapon data

These are used to derive skill-based accuracy, attack, damage, or spell
behavior.  Weapon skills are filtered by the corresponding `Skill Type`.

`Hand-to-Hand Skill`, `Dagger Skill`, `Sword Skill`, `Great Sword Skill`,
`Axe Skill`, `Great Axe Skill`, `Scythe Skill`, `Polearm Skill`, `Katana Skill`,
`Great Katana Skill`, `Club Skill`, `Staff Skill`, `Archery Skill`,
`Marksmanship Skill`, `Throwing Skill`, `Evasion Skill`, `Parrying Skill`,
`Divine Magic Skill`, `Elemental Magic Skill`, `Dark Magic Skill`,
`Ninjutsu Skill`, `Summoning Magic Skill`, and `Blue Magic Skill`.

| Key | Meaning |
| --- | --- |
| `DMG` | Base weapon damage |
| `Ranged DMG` | Ranged weapon damage where supplied |
| `Ammo DMG` | Arrow/bolt/bullet damage |
| `Delay` | Weapon delay |
| `Ranged Delay` | Ranged weapon delay |
| `Ammo Delay` | Ammunition delay |
| `Skill Type` | Weapon family metadata, not a numeric stat |
| `Type` | Gear/item type metadata |
| `Rank` | Augment/rank metadata used to identify progression variants |
| `Augment Path` | Dynamis/Oboro path metadata, such as A/B/C |

## Accuracy, attack, and damage

| Key | Meaning |
| --- | --- |
| `Accuracy` | General/main accuracy bonus |
| `Accuracy1` / `Accuracy2` | Derived main-hand/off-hand accuracy |
| `Ranged Accuracy` | Ranged accuracy bonus |
| `Attack` | General attack bonus |
| `Attack1` / `Attack2` | Derived main-hand/off-hand attack |
| `Ranged Attack` | Ranged attack bonus |
| `Attack%` | Attack percentage modifier |
| `Ranged Attack%` | Ranged attack percentage modifier |
| `Magic Accuracy` | General magic accuracy |
| `Magic Accuracy Skill` | Magic-skill accuracy contribution |
| `Magic Attack` | Magic Attack Bonus (MAB) |
| `Magic Damage` | Flat magic damage bonus |
| `Weapon Skill Accuracy` | Weapon-skill accuracy bonus |
| `Weapon Skill Damage` | General WSD percentage |
| `Weapon Skill Damage Trait` | Job/trait WSD percentage |
| `Elemental WS Damage%` | Elemental WS damage percentage |
| `DA Damage%` | Extra damage applied to double-attack hits |
| `TA Damage%` | Extra damage applied to triple-attack hits |
| `Crit Damage` | Critical-hit damage percentage |
| `Ranged Crit Damage` | Ranged critical-hit damage percentage |
| `Climactic Crit Damage` | Dancer Climactic Flourish critical damage |
| `Crit Rate` | General critical-hit rate |
| `Ranged Crit Rate` | Ranged critical-hit rate |
| `Striking Crit Rate` | Striking/critical WS-specific critical rate |
| `Double Damage` | Special double-damage effect |
| `Smite` | Job/weapon smite damage effect |
| `PDL` | Gear physical damage-limit bonus |
| `PDL Trait` | Trait physical damage-limit bonus |

## Haste, delay, and TP

| Key | Meaning |
| --- | --- |
| `Gear Haste` | Equipment haste |
| `Magic Haste` | Spell/buff haste |
| `JA Haste` | Job-ability haste |
| `Hasso+ JA Haste` | Hasso enhancement added to JA haste while Hasso is active and legal |
| `Dual Wield` | Dual-wield percentage |
| `Martial Arts` | Hand-to-hand delay reduction |
| `Store TP` | TP gained per hit |
| `TP Bonus` | Added TP used for WS scaling |
| `Fencer` | Fencer trait/bonus marker |
| `Fencer TP Bonus` | Fencer TP bonus |
| `Regain` | TP gained over time |
| `Occult Acumen` | TP gained from magic actions |
| `Conserve TP` | Chance/amount of conserved TP |
| `Recycle` | Ammunition conservation |
| `ftp` | Flat fTP adjustment from gear; lowercase by design |

## Multi-attack and follow-up attacks

| Key | Meaning |
| --- | --- |
| `DA` | Double Attack |
| `TA` | Triple Attack |
| `QA` | Quadruple Attack |
| `OA2 main` … `OA8 main` | Main weapon occasionally-attacks count |
| `OA2 sub` … `OA8 sub` | Off-hand occasionally-attacks count |
| `OA2` … `OA8` | Raw item OAX keys before weapon-slot separation |
| `FUA main` / `FUA sub` | Main/off-hand follow-up attack |
| `Zanshin` | Samurai missed-hit follow-up chance |
| `Zanshin Attack` | Attack bonus on Zanshin hits |
| `Zanshin OA2` | Zanshin additional-hit effect |
| `Daken` | Ninja throwing follow-up attack |
| `Kick Attacks` | Kick-attack chance/trait |
| `Kick Attacks Attack` | Kick attack bonus |
| `Kick Attacks Attack%` | Kick attack percentage modifier |
| `Kick Attacks DMG` | Kick damage |

## Defense and survivability

| Key | Meaning |
| --- | --- |
| `Defense` | Physical defense |
| `Defense%` | Defense percentage modifier |
| `Evasion` | Physical evasion |
| `Magic Defense` | Magic defense |
| `Magic Evasion` | Magic evasion |
| `DT` | General damage taken |
| `PDT` / `PDT2` | Physical damage taken; `PDT2` is a separate source bucket |
| `MDT` / `MDT2` | Magic damage taken; `MDT2` is a separate source bucket |
| `Magic Damage Taken` | Enemy/player magic damage-taken modifier used by spell and magic WS formulas |
| `Phalanx Received` | Phalanx damage reduction received |
| `Subtle Blow` | TP reduction inflicted on an enemy |
| `Subtle Blow II` | Additional Subtle Blow source |

## Magic, elemental, and spell effects

| Key | Meaning |
| --- | --- |
| `Fast Cast` | Fast Cast percentage |
| `Elemental Bonus` | General elemental damage bonus |
| `<Element> Affinity` | Magian/weapon elemental affinity level |
| `<Element> Elemental Bonus` | Element-specific elemental damage bonus |

The exact elemental keys are `Fire Affinity`, `Ice Affinity`, `Wind Affinity`,
`Earth Affinity`, `Thunder Affinity`, `Water Affinity`, `Light Affinity`,
`Dark Affinity`, and the matching `Fire Elemental Bonus`, `Ice Elemental Bonus`,
`Wind Elemental Bonus`, `Earth Elemental Bonus`, `Thunder Elemental Bonus`,
`Water Elemental Bonus`, `Light Elemental Bonus`, and `Dark Elemental Bonus`.
| `Magic Burst Damage` | Magic burst damage |
| `Magic Burst Damage II` | Additional magic burst damage source |
| `Magic Burst Accuracy` | Magic burst accuracy |
| `Magic Burst Damage Trait` | Job/trait magic burst damage |
| `Magic Crit Rate II` | Magic critical rate source |
| `EnSpell Damage` | Enspell flat damage |
| `EnSpell Damage%` | Enspell percentage damage |
| `EnSpell Damage main` / `sub` | Weapon-specific main/off-hand enspell damage |
| `EnSpell Damage% main` / `sub` | Weapon-specific main/off-hand enspell percentage |
| `Ninjutsu Damage%` | Ninjutsu damage percentage |
| `Ninjutsu Magic Accuracy` | Ninjutsu magic accuracy |
| `Ninjutsu Magic Attack` | Ninjutsu magic attack |
| `Ninjutsu Magic Damage` | Ninjutsu flat magic damage |
| `Helix Magic Accuracy` | Scholar Helix accuracy merit/gear source |
| `Helix Magic Attack` | Scholar Helix attack merit/gear source |
| `Quick Draw Damage` | Quick Draw flat damage |
| `Quick Draw Damage%` | Quick Draw percentage damage |
| `Quick Draw Magic Accuracy` | Quick Draw magic accuracy |
| `Ebullience Bonus` | Scholar Ebullience bonus |
| `Futae Bonus` | Ninja Futae bonus |
| `Klimaform Damage%` | Klimaform damage bonus |
| `Innin DA%` | Innin double-attack modifier |
| `Building Flourish WSD` | Dancer Building Flourish WSD |
| `Flourish CHR%` | Dancer flourish charisma contribution |
| `Sneak Attack Bonus` | Sneak Attack damage contribution |
| `Trick Attack Bonus` | Trick Attack damage contribution |
| `Skillchain Bonus` | Skillchain damage bonus |

## Ranged attacks and ammunition

| Key | Meaning |
| --- | --- |
| `Barrage` | Barrage shot count/trait |
| `Barrage Ranged Accuracy` | Barrage accuracy |
| `Barrage Ranged Attack` | Barrage attack |
| `Double Shot` | Double Shot chance/trait |
| `Double Shot Damage%` | Double Shot damage |
| `Triple Shot` | Triple Shot chance/trait |
| `Triple Shot Damage%` | Triple Shot damage |
| `Quad Shot` | Quad Shot chance/trait |
| `True Shot` | True Shot ranged WS/attack effect |
| `Hover Shot` | Ability state used by ranged WS calculations |
| `Food Ranged Attack` | Food ranged attack |

## Pet and automaton stats

These keys are accepted for pet/automaton gear and are considered by the
corresponding pet-facing portions of the model. They do not automatically
change a master-only melee or magic calculation.

`Automaton Skill`, `Pet:Level`, `Pet:STR`, `Pet:DEX`, `Pet:VIT`, `Pet:AGI`,
`Pet:INT`, `Pet:MND`, `Pet:CHR`, `Pet:Damage Dealt%`, `Pet:Crit Rate`,
`Pet:TP Bonus`, `Pet:Magic Attack`, `Pet:Magic Accuracy`, `Pet:Magic Evasion`,
`Pet:Magic Defense`, `Pet:Evasion`, `Pet:Attack`, `Pet:Accuracy`,
`Pet:Ranged Attack`, `Pet:Ranged Accuracy`, `Pet:Gear Haste`, `Pet:DA`,
`Pet:Store TP`, `Pet:Subtle Blow`, `Pet:PDT`, `Pet:MDT`, and `Pet:DT`.

## Job and special-effect keys

`Hasso`, `Fencer`, `Barrage`, `Double Shot`, `Triple Shot`, `Quad Shot`,
`True Shot`, `Footwork Attack%`, `Footwork`, `Zanhasso`, `Blood Pact Damage`,
`Phantom Roll`, `Phantom Roll 11 Recovery`, `Roll Duration`, `Weather`, and
`FUA` are special effect or state keys. Some are numeric gear bonuses, while
others are ability/effect markers read by the selected job and buffs.

## Derived/internal keys

The following names are produced after gear and job data are combined. They
are useful when reading optimizer logs or debugging, but they are generally
not entered directly on an item:

`Accuracy1`, `Accuracy2`, `Attack1`, `Attack2`, `Delay1`, `Delay2`, `Food Attack`,
`Food Ranged Attack`, `main Hand-to-Hand Skill`, `main Magic Accuracy Skill`,
`EnSpell Damage main`, `EnSpell Damage sub`, `EnSpell Damage% main`,
`EnSpell Damage% sub`, `OA2`, `OA3`, `OA4`, `OA5`, `OA6`, `OA7`, `OA8`,
`OA2 main`–`OA8 main`, `OA2 sub`–`OA8 sub`, `FUA main`,
`FUA sub`, `Magic Damage Taken`, `TP Bonus`, `WSC`, and `ftp`.

These derived values are intentionally kept separate from the raw item keys:
for example, main/off-hand OAX effects must not leak into the opposite weapon,
and `WSC` is a list of stat-contribution bonuses rather than one flat number.

## Import metadata versus numeric combat stats

The importer also accepts non-combat fields needed to identify or restrict an
item: `Name`, `Name2`, `Location`, `Jobs`, `Type`, `Skill Type`, `Rank`,
`Augment Path`, `Dynamis Divergence`, `Conditional Effects`, `Item ID`, and
`Model Warning`. These fields control catalog identity, slot legality,
augmentation provenance, transfer rules, or UI display; they are not added to
the player's numeric totals.

When adding a new stat, use the exact spelling above and update the relevant
formula before relying on it in an optimizer result. A stat can be accepted by
the importer yet remain job-specific or metadata-only if no active calculation
reads it.
