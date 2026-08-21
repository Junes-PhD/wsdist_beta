"""Cross-slot equipment rules shared by simulation entry points.

The item catalog knows which individual slot accepts an item.  This module
adds the relationships between main/sub/ranged/ammo so an impossible setup
cannot contribute stats in Quick Look while being rejected only by the
optimizer (or vice versa).
"""

from __future__ import annotations

import re

from data.gear import Empty, all_gear


ONE_HANDED_SKILLS = frozenset(("Axe", "Club", "Dagger", "Sword", "Katana"))
TWO_HANDED_SKILLS = frozenset((
    "Great Sword", "Great Katana", "Great Axe", "Polearm", "Scythe", "Staff",
))
PROJECTILE_FOR_RANGED = {
    "Gun": "Bullet",
    "Bow": "Arrow",
    "Crossbow": "Bolt",
}
PROJECTILE_TYPES = frozenset(PROJECTILE_FOR_RANGED.values())
STEELFLASH_BLADEBORN_PAIR = frozenset(("steelflash earring", "bladeborn earring"))
# REMA weapons with a Dynamis-Divergence R15 augment expose the augmented
# weapon in the catalog for the main hand.  In the off hand the game applies
# only the underlying weapon's base stats; the R15 damage/stat augment does
# not carry over.  Keep this normalization here so Quick Look, the optimizer,
# and profile generation all evaluate the same equipment.
_REMA_R15_BASE_BY_NAME = {
    name: item for name, item in all_gear.items()
    if str(item.get("Name2") or "") == str(item.get("Name") or "")
    and item.get("Type") == "Weapon"
}
_DIVERGENCE_SUB_BY_NAME_PATH = {
    (str(item.get("Name") or ""), str(item.get("Augment Path") or "").upper()): item
    for item in all_gear.values()
    if item.get("Dynamis Divergence")
    and str(item.get("Name2") or "").lower().endswith("(sub)")
}
# Sortie JSE earrings are all right-ear-only.  Keep the list here so the
# picker, profile builder, and optimizer apply the same restriction even when
# an imported bridge record does not carry an explicit side flag.
SORTIE_JSE_EARRING_PREFIXES = frozenset((
    "Hattori", "Heathen's", "Lethargy", "Ebers", "Wicce", "Peltast's",
    "Boii", "Bhikku", "Skulker's", "Chevalier's", "Nukumi", "Fili",
    "Amini", "Kasuga", "Beckoner's", "Hashishin", "Chasseur's", "Karagoz",
    "Maculele", "Arbatel", "Azimuth", "Erilaz",
))


def _item_type(item: dict | None) -> str:
    return str((item or {}).get("Type") or "None")


def _skill_type(item: dict | None) -> str:
    return str((item or {}).get("Skill Type") or "None")


def _is_empty(item: dict | None) -> bool:
    return not item or str(item.get("Name") or "Empty") == "Empty"


def is_right_ear_only(item: dict | None) -> bool:
    """Return whether an earring's modeled effect requires the right ear."""
    item = item or {}
    if bool(item.get("Right Ear Only") or item.get("right_ear_only")):
        return True
    text = " ".join(str(item.get(key) or "") for key in ("Name", "Name2"))
    normalized = re.sub(r"[^a-z0-9]", "", text.casefold())
    return "earring" in normalized and any(
        normalized.startswith(re.sub(r"[^a-z0-9]", "", prefix.casefold()))
        for prefix in SORTIE_JSE_EARRING_PREFIXES
    )


def apply_ear_slot_rules(gearset: dict) -> dict[str, str]:
    """Place right-ear-only earrings in ``ear2`` (the right ear slot)."""
    gearset.setdefault("ear1", Empty)
    gearset.setdefault("ear2", Empty)
    if not is_right_ear_only(gearset["ear1"]):
        return {}
    gearset["ear1"], gearset["ear2"] = gearset["ear2"], gearset["ear1"]
    return {
        "ear1": "right-ear-only earring moved to the right ear",
        "ear2": "right-ear-only earring",
    }


def _tp_bonus(item: dict | None) -> float:
    """Return an item's modeled TP Bonus without letting metadata raise."""
    try:
        return float((item or {}).get("TP Bonus", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def has_conditional_set_effect(item: dict | None) -> bool:
    """Return whether an item participates in a modeled cross-slot effect."""
    name = str((item or {}).get("Name") or (item or {}).get("Name2") or "").casefold()
    return name in STEELFLASH_BLADEBORN_PAIR or bool(
        (item or {}).get("Conditional Effects") or (item or {}).get("conditional_effects")
    )


def conditional_gear_bonuses(gearset: dict) -> dict[str, float]:
    """Return bonuses that exist only when their complete equipment set is worn.

    Steelflash and Bladeborn retain their individual Accuracy/Attack and Store
    TP values.  Their Double Attack +7 is a single two-ear set bonus, not a
    stat supplied independently by either earring.
    """
    ear_names = {
        str((gearset.get(slot) or {}).get("Name")
            or (gearset.get(slot) or {}).get("Name2") or "").casefold()
        for slot in ("ear1", "ear2")
    }
    if STEELFLASH_BLADEBORN_PAIR <= ear_names:
        return {"DA": 7.0}
    return {}


def _can_dual_wield(main_job: str, sub_job: str, master_level: int) -> bool:
    """Return whether the modeled job pair has a native Dual Wield trait.

    The app models level-99 mains and level 49--59 subjobs.  Blue Mage's
    spell-set trait is intentionally not inferred here because it is not
    represented by the current ability inputs.
    """
    main_job = str(main_job or "").casefold()
    sub_job = str(sub_job or "").casefold()
    sub_level = 49 + max(0, min(50, int(master_level or 0))) // 5
    if main_job in {"nin", "dnc", "thf"}:
        return True
    return ((sub_job == "nin" and sub_level >= 10)
            or (sub_job == "dnc" and sub_level >= 20)
            or (sub_job == "thf" and sub_level >= 83))


def ranged_attack_ready(gearset: dict) -> bool:
    """Whether the equipped ranged weapon and projectile can perform a shot."""
    ranged_type = _item_type(gearset.get("ranged"))
    return _item_type(gearset.get("ammo")) == PROJECTILE_FOR_RANGED.get(ranged_type)


def ranged_pair_compatible(gearset: dict) -> bool:
    """Return true for an empty ranged layer or a correctly paired projectile."""
    ranged_type = _item_type(gearset.get("ranged"))
    if ranged_type in PROJECTILE_FOR_RANGED:
        return ranged_attack_ready(gearset)
    if ranged_type == "Instrument":
        return _is_empty(gearset.get("ammo"))
    return True


def apply_weapon_slot_rules(gearset: dict, main_job: str = "", sub_job: str = "",
                            master_level: int = 0) -> dict[str, str]:
    """Clear mutually exclusive weapon-slot items and return changed-slot reasons.

    Grip is retained only with a two-handed weapon.  Hand-to-hand occupies the
    sub slot.  Gun/Bow/Crossbow retain only their matching projectile ammo;
    ranged instruments use an empty ammo slot.  Non-projectile ammo remains
    usable when no ranged weapon is equipped.
    """
    for slot in ("main", "sub", "ranged", "ammo"):
        gearset.setdefault(slot, Empty)
    changed: dict[str, str] = {}

    # Dynamis-Divergence R15 REMA augments are main-hand-only.  The catalog
    # deliberately keeps the augmented item for main-hand optimization, but
    # an augmented REMA in ``sub`` must be reduced to its base weapon before
    # any compatibility or stat aggregation occurs.  Rank-based Odyssey
    # weapons are excluded by requiring the legacy R15 label and a matching
    # unaugmented catalog entry.
    sub_item = gearset.get("sub") or Empty
    sub_name2 = str(sub_item.get("Name2") or "")
    divergence_base = _DIVERGENCE_SUB_BY_NAME_PATH.get((
        str(sub_item.get("Name") or ""),
        str(sub_item.get("Augment Path") or "").upper(),
    ))
    if divergence_base is not None and not sub_name2.lower().endswith("(sub)"):
        gearset["sub"] = dict(divergence_base)
        sub_item = gearset["sub"]
        sub_name2 = str(sub_item.get("Name2") or "")
        changed["sub"] = "Dynamis-Divergence path augments apply only in main hand"
    if (
        _item_type(sub_item) == "Weapon"
        and not sub_item.get("Rank")
        and re.search(r"\sR15$", sub_name2, re.IGNORECASE)
    ):
        base_item = _REMA_R15_BASE_BY_NAME.get(str(sub_item.get("Name") or ""))
        if base_item is not None:
            gearset["sub"] = dict(base_item)
            changed["sub"] = "Dynamis-Divergence R15 augment applies only in main hand"

    def clear(slot: str, reason: str):
        if not _is_empty(gearset.get(slot)):
            gearset[slot] = Empty
            changed[slot] = reason

    main_skill = _skill_type(gearset["main"])
    sub_type = _item_type(gearset["sub"])
    if main_skill == "Hand-to-Hand":
        clear("sub", "Hand-to-Hand occupies both hand slots")
    elif main_skill in TWO_HANDED_SKILLS:
        if sub_type not in {"None", "Grip"}:
            clear("sub", "two-handed weapons permit only a grip in sub")
    elif _is_empty(gearset["main"]) and sub_type in {"Weapon", "Grip"}:
        clear("sub", "an off-hand weapon or grip requires a main weapon")
    elif main_skill in ONE_HANDED_SKILLS:
        if sub_type == "Grip":
            clear("sub", "grips require a two-handed main weapon")
        elif sub_type == "Weapon" and not _can_dual_wield(main_job, sub_job, master_level):
            clear("sub", "job pair has no modeled Dual Wield trait")

    # Centovente, Hitaki, and equivalent +1000 TP Bonus weapons are modeled
    # as a single effective weapon bonus. They can combine with Moonshade,
    # Fencer, Warcry, and ordinary gear TP Bonus, but two such weapons must
    # never contribute +2000 from main/sub.
    if _tp_bonus(gearset["main"]) >= 1000 and _tp_bonus(gearset["sub"]) >= 1000:
        clear("sub", "only one +1000 TP Bonus weapon can be equipped")

    ranged_type = _item_type(gearset["ranged"])
    ammo_type = _item_type(gearset["ammo"])
    required_projectile = PROJECTILE_FOR_RANGED.get(ranged_type)
    if required_projectile and ammo_type not in {"None", required_projectile}:
        clear("ammo", f"{ranged_type} requires {required_projectile} ammo")
    elif ammo_type in PROJECTILE_TYPES and ranged_type != next(
            (kind for kind, projectile in PROJECTILE_FOR_RANGED.items() if projectile == ammo_type),
            "",
    ):
        clear("ammo", f"{ammo_type} requires its matching ranged weapon")
    elif ranged_type == "Instrument" and not _is_empty(gearset["ammo"]):
        clear("ammo", "ranged instruments use an empty ammo slot")
    return changed
