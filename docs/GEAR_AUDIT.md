# Gear audit checklist

Imported gear should be checked by item name, slot, augment/path suffix, and
the authoritative item description. Keep imported records available for review,
but correct curated models and add a focused regression test before using a
changed item in an optimizer winner.

| Gear or rule | Expected model behavior | Import cross-check |
| --- | --- | --- |
| Wakido Kote +2 | Adds 3% Hasso Job Ability Haste only while Hasso and a two-handed main weapon are active; it is not Gear Haste. | Compare Wakido Kote +3 (4%) and Wakido Sune-Ate +2/+3 with Hasso toggled on and off. |
| Crocea Mors | Includes 20% Fast Cast, 130 HP, and 70 MP on the modeled R25C weapon. | Compare the imported record with the authoritative entry and another RDM sword. |
| Bifrost Ring | Converts -70 HP into +70 MP; it does not provide two positive flat stats. | Compare the imported description and raw record with the curated entry. |
| Lethargy Earring and other Sortie earrings | A right-ear-only item must not be offered in EAR1 and may be used in EAR2. | Try it in both ear slots and compare another Sortie earring. |
| Steelflash Earring + Bladeborn Earring | The special set bonus applies only when the legal pair is equipped together. | Evaluate each earring alone, then as a pair. |
| Rostam and Dynamis Divergence path weapons | Preserve A/B/C path identity and path-specific augments; off-hand use gets base stats only. | Compare each path with its R0/base counterpart in main- and off-hand. |
| Sakpata rank variants | Shared Fast Cast/HP/MP/Phalanx values remain present across ranks while rank augments change. | Compare at least R0, R15, and R30. |
| Masamune icon/name variants | Base and R15 models resolve to the Masamune icon and name. | Check both in the 4x4 preview and exported profile. |

The local `_reference/ffxiah_items.tgz` archive is useful for checking IDs,
slots, jobs, and descriptions. Effect-only behavior still needs an in-game or
authoritative source check, such as the [Hasso reference](https://www.bg-wiki.com/ffxi/Hasso).
