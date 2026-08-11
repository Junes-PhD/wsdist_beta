"""Cross-slot equipment rules shared by simulation entry points.

The item catalog knows which individual slot accepts an item.  This module
adds the relationships between main/sub/ranged/ammo so an impossible setup
cannot contribute stats in Quick Look while being rejected only by the
optimizer (or vice versa).
"""

from __future__ import annotations

from gear import Empty


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


def _item_type(item: dict | None) -> str:
    return str((item or {}).get("Type") or "None")


def _skill_type(item: dict | None) -> str:
    return str((item or {}).get("Skill Type") or "None")


def _is_empty(item: dict | None) -> bool:
    return not item or str(item.get("Name") or "Empty") == "Empty"


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
