"""Recipe-driven LuAshitacast profile generation.

This module deliberately keeps profile construction separate from Qt.  It can
be exercised against a bridge/profile fixture, then the UI only needs to show
progress and publish the returned armor-only sets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from itertools import product
from typing import Iterable

import gear
from create_player import calculate_damage_taken
from wsdist_bridge import _gear_record


ARMOR_SLOTS = (
    "head", "neck", "ear1", "ear2", "body", "hands", "ring1", "ring2",
    "back", "waist", "legs", "feet",
)
WEAPON_SLOTS = ("main", "sub", "ranged", "ammo")
TP_SCENARIOS = (
    ("Default", "Apex Toad"),
    ("Acc", "Apex Knight Lugcrawler"),
    ("HighAcc", "Apex Archaic Cogs"),
)
_OPTIMIZER_SCENARIOS = {
    "Default": {"enemy": "Apex Toad", "pdt": 0, "mdt": 0, "dt": 0},
    "Acc": {"enemy": "Apex Knight Lugcrawler", "pdt": 0, "mdt": 0, "dt": 0},
    "HighAcc": {"enemy": "Apex Archaic Cogs", "pdt": 0, "mdt": 0, "dt": 0},
    # Hybrid is intentionally a damage-first survival set.  PDT must reach
    # the normal cap while MDT has a meaningful floor without turning every
    # hybrid profile into a dedicated magic-defense set.
    "Hybrid": {"enemy": "Apex Knight Lugcrawler", "pdt": 50, "mdt": 25, "dt": 0},
}


@dataclass(frozen=True)
class GearSources:
    accessible: bool = True
    porter: bool = True
    transferable: bool = False


@dataclass(frozen=True)
class ProfileRecipe:
    name: str
    objective: tuple[str, ...]
    caps: tuple[tuple[str, float], ...] = ()
    require_damage_cap: bool = False
    pinned_slots: tuple[str, ...] = ()


@dataclass
class BuildSet:
    name: str
    equipment: dict
    score: tuple[float, ...]
    pinned: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def child_seed(batch_seed: int, *identity: str) -> int:
    value = "|".join((str(batch_seed), *map(str, identity))).encode("utf-8")
    return int.from_bytes(sha256(value).digest()[:4], "big") & 0x7FFFFFFF


def optimizer_scenario(recipe_name: str, ws_tp: int) -> dict[str, int | str]:
    """Return the fixed combat context used to optimize a generated set.

    Weapon-category suffixes inherit their base set's scenario, so for
    example ``Savage_Hybrid_DualWield`` is still a Hybrid search.
    """
    name = str(recipe_name)
    variant = next(
        (label for label in ("HighAcc", "Hybrid", "Default", "Acc")
         if name == f"Tp_{label}" or name.startswith(f"Tp_{label}_")
         or f"_{label}" in name),
        "Default",
    )
    scenario = dict(_OPTIMIZER_SCENARIOS[variant])
    scenario["tp"] = max(1000, min(3000, int(ws_tp)))
    return scenario


def _descriptor(name: str) -> tuple[str, str]:
    parts = [part for part in str(name).split("_") if part]
    return (parts[0].casefold() if parts else "", "_".join(parts[1:]))


def weapon_overlays(payloads: Iterable[dict]) -> list[dict]:
    """Return LAC Weapon_/Gun_ layers with usable weapon slots."""
    result = []
    for payload in payloads:
        descriptor = payload.get("descriptor") or {}
        slots = set(payload.get("specified_slots") or ())
        if descriptor.get("role") == "weapon" and slots & set(WEAPON_SLOTS):
            result.append(payload)
    return sorted(result, key=lambda item: str(item.get("name", "")).casefold())


def weapon_category(overlay: dict) -> str:
    items = overlay.get("gearset") or {}
    main = items.get("main", gear.Empty)
    sub = items.get("sub", gear.Empty)
    if main.get("Skill Type") == "Hand-to-Hand" or main.get("Skill Type") in {
        "Great Axe", "Great Sword", "Polearm", "Scythe", "Staff", "Great Katana",
    }:
        return "TwoHanded"
    if sub.get("Type") == "Weapon":
        return "DualWield"
    return "SingleWield"


def profile_recipes(job: str, existing_names: Iterable[str]) -> list[ProfileRecipe]:
    """Return supported existing-profile recipes without inventing handlers."""
    existing = set(existing_names)
    recipes = [
        ProfileRecipe("Precast", ("Fast Cast", "DT", "HP"), (("Fast Cast", 80),)),
        ProfileRecipe("SIR", ("Spell interruption rate down", "DT", "HP"), (("Spell interruption rate down", 92),)),
        ProfileRecipe("Dt", ("HP", "Magic Evasion", "Defense", "Evasion"), require_damage_cap=True),
        ProfileRecipe("Evasion", ("Evasion", "DT", "HP")),
        ProfileRecipe("MEVA", ("Magic Evasion", "DT", "HP")),
        ProfileRecipe("Cure", ("Cure Potency", "MND", "Healing Magic Skill", "DT")),
        ProfileRecipe("Enhancing", ("Enhancing Magic Skill", "Enhancing Duration", "Fast Cast")),
        ProfileRecipe("Enfeebling", ("Magic Accuracy", "Enfeebling Magic Skill", "INT", "MND")),
        ProfileRecipe("Nuke", ("Magic Attack", "Magic Damage", "INT", "Magic Accuracy")),
        ProfileRecipe("Drain", ("Dark Magic Skill", "Magic Accuracy", "INT")),
        ProfileRecipe("Preshot", ("Snapshot", "Rapid Shot", "Ranged Accuracy")),
        ProfileRecipe("Midshot", ("Ranged Accuracy", "Ranged Attack", "Store TP")),
        ProfileRecipe("QD", ("Quick Draw Damage", "Quick Draw Damage%", "Magic Attack", "Magic Accuracy")),
        ProfileRecipe("QD_Acc", ("Quick Draw Magic Accuracy", "Magic Accuracy", "AGI")),
        ProfileRecipe("Enmity", ("Enmity", "Fast Cast", "DT", "HP")),
        ProfileRecipe("Counterstance", ("Counter", "Evasion", "DT")),
    ]
    # Never add a handler-only set to a profile which does not already use it.
    return [recipe for recipe in recipes if recipe.name in existing or recipe.name in {"Dt", "Evasion", "MEVA"}]


def bridge_candidates(store, job: str, sources: GearSources) -> dict[str, list[dict]]:
    """Build an owned-only pool from raw bridge data, including Porter on demand."""
    result = {slot: [gear.Empty] for slot in ARMOR_SLOTS}
    job = job.casefold()
    for raw in store.data.get("items", []):
        locations = raw.get("locations") or []
        accessible = int(raw.get("accessible_count") or 0) > 0
        porter = any(str(row.get("source", "")).casefold() == "porter slip" for row in locations)
        transferable = bool(raw.get("transferable"))
        allowed = ((sources.accessible and accessible) or (sources.porter and porter)
                   or (sources.transferable and transferable))
        # Base stats are sufficient for a conservative profile recipe even
        # when GearSetBuilder reports an unmodeled specialized augment. The
        # bridge record carries a warning; records with no stats remain out.
        if not allowed or (not raw.get("model_complete") and not raw.get("stats") and not raw.get("base_stats")):
            continue
        item = _gear_record(raw, eligible=False, hoxne_mastery_rank=store.hoxne_mastery_rank)
        if job not in [str(value).casefold() for value in item.get("Jobs", ())]:
            continue
        for slot in item.get("Slots", ()):
            if slot in result:
                result[slot].append(item)
    for slot, values in result.items():
        result[slot] = sorted(values, key=lambda item: str(item.get("Name2", "")).casefold())
    return result


def _value(item: dict, stat: str) -> float:
    value = item.get(stat, 0)
    try:
        value = float(value)
        # These reductions are stored as negative percentages in item data;
        # recipe objectives and caps use the positive amount of protection.
        if stat in {"DT", "PDT", "MDT", "Spell interruption rate down"}:
            return -value if value < 0 else value
        return value
    except (TypeError, ValueError):
        return 0.0


def _slot_best(items: list[dict], objective: tuple[str, ...]) -> list[dict]:
    """Bound a direct-stat search to useful per-slot alternatives."""
    ranked = sorted(
        items,
        key=lambda item: tuple(_value(item, stat) for stat in objective),
        reverse=True,
    )
    return ranked[: min(6, len(ranked))]


def build_stat_set(name: str, candidates: dict[str, list[dict]], recipe: ProfileRecipe,
                   *, weapons: dict | None = None, pinned: dict[str, dict] | None = None,
                   buffs: dict | None = None, abilities: dict | None = None) -> BuildSet:
    """Choose a valid armor set for a cap-aware non-combat recipe.

    The product is intentionally bounded.  A coordinate-ascent pass then
    resolves caps and defensive requirements without pretending these sets
    have a damage formula they do not possess.
    """
    equipment = {slot: gear.Empty for slot in (*WEAPON_SLOTS, *ARMOR_SLOTS)}
    equipment.update(weapons or {})
    pinned = dict(pinned or {})
    pinned_notes = {}
    active_slots = []
    for slot in ARMOR_SLOTS:
        if slot in pinned:
            equipment[slot] = pinned[slot]
            pinned_notes[slot] = str(pinned[slot].get("Name") or "manual item")
        else:
            active_slots.append(slot)

    # Seed using each slot's locally strongest item, then improve one slot at
    # a time with the real whole-set score (including rare/duplicate and DT).
    for slot in active_slots:
        equipment[slot] = _slot_best(candidates.get(slot, [gear.Empty]), recipe.objective)[0]

    def valid(current: dict) -> bool:
        names = {}
        for slot in ("ring1", "ring2", "ear1", "ear2"):
            item = current[slot]
            name = str(item.get("Name2") or item.get("Name") or "")
            if name and name != "Empty":
                if name in names and int(item.get("Accessible Count") or 1) < 2:
                    return False
                names[name] = slot
        return True

    def hard_valid(current: dict) -> bool:
        if not valid(current):
            return False
        pdt, mdt = calculate_damage_taken(current, buffs, abilities)
        return not recipe.require_damage_cap or (pdt <= -50 and mdt <= -50)

    def score(current: dict) -> tuple[float, ...]:
        values = []
        for stat, target in recipe.caps:
            total = sum(_value(item, stat) for item in current.values())
            values.append(min(total, target))
        if recipe.require_damage_cap:
            pdt, mdt = calculate_damage_taken(current, buffs, abilities)
            values.extend((-pdt, -mdt))
        values.extend(sum(_value(item, stat) for item in current.values()) for stat in recipe.objective)
        return tuple(values)

    for _pass in range(3):
        changed = False
        for slot in active_slots:
            current = equipment[slot]
            best, best_score = current, score(equipment) if valid(equipment) else tuple(float("-inf") for _ in range(8))
            for item in _slot_best(candidates.get(slot, [gear.Empty]), recipe.objective):
                equipment[slot] = item
                if valid(equipment) and score(equipment) > best_score:
                    best, best_score = item, score(equipment)
            equipment[slot] = best
            changed |= best is not current
        if not changed:
            break
    warnings = []
    if not hard_valid(equipment):
        warnings.append("Could not meet this recipe's hard defensive requirement with the selected gear pool.")
    for stat, target in recipe.caps:
        total = sum(_value(item, stat) for item in equipment.values())
        if total < target:
            warnings.append(f"{stat} reaches {total:.0f}/{target:.0f}.")
    return BuildSet(name, {slot: equipment[slot] for slot in ARMOR_SLOTS}, score(equipment), pinned_notes, warnings)


def pin_unmodeled_slots(profile_payloads: Iterable[dict], set_name: str) -> dict[str, dict]:
    """Preserve manual profile pieces not represented by the bridge model."""
    for payload in profile_payloads:
        if payload.get("name") != set_name:
            continue
        result = {}
        for slot in payload.get("specified_slots", ()):
            item = payload.get("gearset", {}).get(slot, gear.Empty)
            if item.get("Name") != "Empty" and not item.get("Model Complete", True):
                result[slot] = item
        return result
    return {}
