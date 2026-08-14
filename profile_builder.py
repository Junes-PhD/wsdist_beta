"""Recipe-driven LuAshitacast profile generation.

This module deliberately keeps profile construction separate from Qt.  It can
be exercised against a bridge/profile fixture, then the UI only needs to show
progress and publish the returned armor-only sets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from itertools import product
import re
from typing import Iterable

import gear
from create_player import calculate_damage_taken
from equipment_rules import (
    apply_ear_slot_rules, conditional_gear_bonuses, has_conditional_set_effect,
    is_right_ear_only,
)
from wsdist_bridge import _gear_record


ARMOR_SLOTS = (
    "head", "neck", "ear1", "ear2", "body", "hands", "ring1", "ring2",
    "back", "waist", "legs", "feet",
)
WEAPON_SLOTS = ("main", "sub", "ranged", "ammo")
SET_SLOTS = ("ammo", *ARMOR_SLOTS)
TP_SCENARIOS = (
    ("Default", "Apex Toad"),
    ("Acc", "Apex Knight Lugcrawler"),
    ("HighAcc", "Apex Archaic Cogs"),
)
_OPTIMIZER_SCENARIOS = {
    "Default": {"enemy": "Apex Toad", "pdt": 0, "mdt": 0, "dt": 0},
    "Acc": {"enemy": "Apex Knight Lugcrawler", "pdt": 0, "mdt": 0, "dt": 0},
    "HighAcc": {"enemy": "Apex Archaic Cogs", "pdt": 0, "mdt": 0, "dt": 0},
    # Hybrid sets retain the same three accuracy contexts as ordinary TP.
    # Each remains damage-first while meeting a full PDT cap and a meaningful
    # MDT floor instead of becoming a dedicated magic-defense set.
    "HybridDefault": {"enemy": "Apex Toad", "pdt": 50, "mdt": 25, "dt": 0},
    # Weapon-skill Hybrid remains the established medium-accuracy scenario.
    "Hybrid": {"enemy": "Apex Knight Lugcrawler", "pdt": 50, "mdt": 25, "dt": 0},
    "HybridAcc": {"enemy": "Apex Knight Lugcrawler", "pdt": 50, "mdt": 25, "dt": 0},
    "HybridHighAcc": {"enemy": "Apex Archaic Cogs", "pdt": 50, "mdt": 25, "dt": 0},
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
    pdt_target: float = 50
    mdt_target: float = 50
    pinned_slots: tuple[str, ...] = ()


TP_RECIPES = (
    ProfileRecipe(
        "Tp_Default", ("DA", "TA", "QA", "Store TP", "Attack", "Accuracy"),
        (("Gear Haste", 25),),
    ),
    ProfileRecipe(
        "Tp_Acc", ("Accuracy", "Attack", "Store TP", "DA", "TA"),
        (("Gear Haste", 25),),
    ),
    ProfileRecipe(
        "Tp_HighAcc", ("Accuracy", "Store TP", "Attack", "DA"),
        (("Gear Haste", 25),),
    ),
    ProfileRecipe(
        "Tp_Hybrid",
        ("DA", "TA", "Store TP", "Attack", "Accuracy"),
        (("Gear Haste", 25),),
        require_damage_cap=True,
        pdt_target=50,
        mdt_target=25,
    ),
    ProfileRecipe(
        "Tp_Hybrid_Acc",
        ("Accuracy", "Attack", "Store TP", "DA", "TA"),
        (("Gear Haste", 25),),
        require_damage_cap=True,
        pdt_target=50,
        mdt_target=25,
    ),
    ProfileRecipe(
        "Tp_Hybrid_HighAcc",
        ("Accuracy", "Store TP", "Attack", "DA"),
        (("Gear Haste", 25),),
        require_damage_cap=True,
        pdt_target=50,
        mdt_target=25,
    ),
)

WS_VARIANTS = (
    ("Default", ("Weapon Skill Damage", "STR", "DEX", "VIT", "Attack", "Accuracy"), False),
    ("Acc", ("Accuracy", "Weapon Skill Accuracy", "Attack", "STR"), False),
    ("HighAcc", ("Accuracy", "Weapon Skill Accuracy", "Attack"), False),
    ("Hybrid", ("Weapon Skill Damage", "STR", "Attack", "Accuracy"), True),
)


@dataclass
class BuildSet:
    name: str
    equipment: dict
    score: tuple[float, ...]
    pinned: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    cap_results: list[dict] = field(default_factory=list)


def child_seed(batch_seed: int, *identity: str) -> int:
    value = "|".join((str(batch_seed), *map(str, identity))).encode("utf-8")
    return int.from_bytes(sha256(value).digest()[:4], "big") & 0x7FFFFFFF


def optimizer_scenario(recipe_name: str, ws_tp: int) -> dict[str, int | str]:
    """Return the fixed combat context used to optimize a generated set.

    Weapon-category suffixes inherit their base set's scenario, so for
    example ``Savage_Hybrid_DualWield`` is still a Hybrid search.
    """
    name = str(recipe_name)
    tp_variant = next((variant for prefix, variant in (
        ("Tp_Hybrid_HighAcc", "HybridHighAcc"),
        ("Tp_Hybrid_Acc", "HybridAcc"),
        ("Tp_Hybrid", "Hybrid"),
        ("Tp_HighAcc", "HighAcc"),
        ("Tp_Acc", "Acc"),
        ("Tp_Default", "Default"),
    ) if name == prefix or name.startswith(prefix + "_")), None)
    variant = ({"Hybrid": "HybridDefault"}.get(tp_variant, tp_variant) if tp_variant else None) or next(
        (label for label in ("HighAcc", "Hybrid", "Default", "Acc") if f"_{label}" in name),
        "Default",
    )
    scenario = dict(_OPTIMIZER_SCENARIOS[variant])
    scenario["tp"] = max(1000, min(3000, int(ws_tp)))
    return scenario


def group_similar_ws_sets(ws_sets: dict[str, dict[str, dict]], *,
                          max_slot_differences: int = 2) -> list[dict]:
    """Group WS armor sets around a representative with few slot changes.

    Weapons are fixed by the profile overlay, so only publishable ``SET_SLOTS``
    participate. ``Name2`` preserves augment identity when it is available.
    The deterministic largest-first result is suitable for choosing a shared
    ``Ws_Default`` set while retaining genuinely different WS overrides.
    """
    max_slot_differences = max(0, int(max_slot_differences))

    def identity(item: dict | None) -> str:
        item = item if isinstance(item, dict) else gear.Empty
        return str(item.get("Name2") or item.get("Name") or "Empty").casefold()

    def distance(left: dict, right: dict) -> int:
        return sum(
            identity(left.get(slot)) != identity(right.get(slot))
            for slot in SET_SLOTS
        )

    remaining = {
        str(name): {slot: (gearset.get(slot) or gear.Empty) for slot in SET_SLOTS}
        for name, gearset in ws_sets.items()
        if str(name) and isinstance(gearset, dict)
    }
    groups = []
    while remaining:
        candidates = []
        for representative in sorted(remaining, key=str.casefold):
            members = sorted(
                (name for name, value in remaining.items()
                 if distance(remaining[representative], value) <= max_slot_differences),
                key=str.casefold,
            )
            total_distance = sum(
                distance(remaining[representative], remaining[name]) for name in members
            )
            candidates.append((-len(members), total_distance, representative.casefold(), representative, members))
        _size, _distance, _sort_name, representative, members = min(candidates)
        groups.append({
            "representative": representative,
            "members": members,
            "gearset": remaining[representative],
        })
        for name in members:
            remaining.pop(name, None)
    return groups


def _descriptor(name: str) -> tuple[str, str]:
    parts = [part for part in str(name).split("_") if part]
    return (parts[0].casefold() if parts else "", "_".join(parts[1:]))


def weapon_overlays(payloads: Iterable[dict]) -> list[dict]:
    """Return usable LAC weapon layers in their profile-defined order.

    The first weapon set is normally the profile's default cycle. Sorting the
    names made ``Weapon_Bow`` silently replace ``Weapon_Masamune`` for SAM.
    """
    result = []
    for payload in payloads:
        descriptor = payload.get("descriptor") or {}
        slots = set(payload.get("specified_slots") or ())
        if descriptor.get("role") == "weapon" and slots & set(WEAPON_SLOTS):
            result.append(payload)
    return result


def weapon_category(overlay: dict) -> str:
    items = overlay.get("gearset") or {}
    main = items.get("main", gear.Empty)
    sub = items.get("sub", gear.Empty)
    ranged = items.get("ranged", gear.Empty)
    ranged_name = str(ranged.get("Name") or "")
    if (ranged.get("Skill Type") in {"Archery", "Marksmanship", "Throwing"}
            or ranged_name not in {"", "Empty"}):
        return "Ranged"
    if main.get("Skill Type") == "Hand-to-Hand" or main.get("Skill Type") in {
        "Great Axe", "Great Sword", "Polearm", "Scythe", "Staff", "Great Katana",
    }:
        return "TwoHanded"
    if sub.get("Type") == "Weapon":
        return "DualWield"
    return "SingleWield"


def weapon_overlay_suffix(overlay: dict) -> str:
    """Return a stable catalog suffix for one fixed LAC weapon layer."""
    name = str(overlay.get("name") or "").strip()
    suffix = re.sub(r"^(Weapon|Gun|Range|Ranged)_?", "", name, flags=re.IGNORECASE)
    suffix = re.sub(r"[^A-Za-z0-9]+", "_", suffix).strip("_")
    return suffix or weapon_category(overlay)


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
    # Ammo is set-specific stat gear for melee jobs, not merely a fixed weapon
    # overlay. Keep it in generated and optimized sets instead of silently
    # publishing an empty ammo slot.
    result = {slot: [gear.Empty] for slot in SET_SLOTS}
    job = job.casefold()
    for raw in store.data.get("items", []):
        locations = raw.get("locations") or []
        accessible = int(raw.get("accessible_count") or 0) > 0
        porter = any(
            str(row.get("source", "")).casefold().replace("_", " ") == "porter slip"
            or str(row.get("container", "")).casefold() == "porter slip"
            for row in locations
        )
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
            if slot in result and not (slot == "ear1" and is_right_ear_only(item)):
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


def _set_stat_total(equipment: dict, stat: str) -> float:
    """Return a whole-set stat total including modeled cross-slot effects."""
    return (
        sum(_value(item, stat) for item in equipment.values())
        + conditional_gear_bonuses(equipment).get(stat, 0.0)
    )


def _slot_best(items: list[dict], recipe: ProfileRecipe) -> list[dict]:
    """Bound a search without pruning cap or defensive specialists.

    A single lexicographic ranking is unsafe for recipes such as ``Dt`` and
    ``Tp_Hybrid``: high-HP or high-damage pieces can otherwise displace every
    PDT/MDT option before the whole-set scorer sees them.  Keep the strongest
    representatives for each relevant stat, then fill the remaining shortlist
    with the original multi-stat ranking.
    """
    considered = [stat for stat, _target in recipe.caps]
    if recipe.require_damage_cap:
        considered.extend(("PDT", "MDT", "DT"))
    considered.extend(stat for stat in recipe.objective if stat not in considered)

    selected: list[dict] = []
    seen: set[int] = set()

    def add(item: dict):
        identity = id(item)
        if identity not in seen:
            seen.add(identity)
            selected.append(item)

    for stat in considered:
        for item in sorted(items, key=lambda value: _value(value, stat), reverse=True)[:2]:
            add(item)
    # An item with a cross-slot effect may look dominated in isolation. Keep
    # it in the bounded pool so the whole-set scorer can evaluate its pair.
    for item in items:
        if has_conditional_set_effect(item):
            add(item)
    ranked = sorted(
        items,
        key=lambda item: tuple(_value(item, stat) for stat in considered),
        reverse=True,
    )
    for item in ranked[:6]:
        add(item)
    empty = next((item for item in items if item.get("Name") == "Empty"), None)
    if empty is not None:
        add(empty)
    return selected[:24]


def build_stat_set(name: str, candidates: dict[str, list[dict]], recipe: ProfileRecipe,
                   *, weapons: dict | None = None, pinned: dict[str, dict] | None = None,
                   starting: dict | None = None, buffs: dict | None = None,
                   abilities: dict | None = None) -> BuildSet:
    """Choose a valid armor set for a cap-aware non-combat recipe.

    The product is intentionally bounded.  A coordinate-ascent pass then
    resolves caps and defensive requirements without pretending these sets
    have a damage formula they do not possess.
    """
    equipment = {slot: gear.Empty for slot in (*WEAPON_SLOTS, *ARMOR_SLOTS)}
    equipment.update(starting or {})
    equipment.update(weapons or {})
    pinned = dict(pinned or {})
    pinned_notes = {}
    active_slots = []
    for slot in SET_SLOTS:
        if slot in (weapons or {}):
            continue
        if slot in pinned:
            equipment[slot] = pinned[slot]
            pinned_notes[slot] = str(pinned[slot].get("Name") or "manual item")
        else:
            active_slots.append(slot)

    def valid(current: dict) -> bool:
        names = {}
        for slot in ("ring1", "ring2", "ear1", "ear2"):
            item = current[slot]
            # Augment text in Name2 does not make a second copy of a Rare item
            # legal. Compare the base resource name across paired slots.
            name = str(item.get("Name") or item.get("Name2") or "").casefold()
            if name and name != "empty":
                rare = bool(item.get("Rare")) or bool(
                    int(item.get("Resource Flags") or 0) & 0x8000
                )
                if name in names and (
                    rare or names[name] or int(item.get("Accessible Count") or 1) < 2
                ):
                    return False
                names[name] = rare
        return True

    # Preserve a usable imported set as the seed. Fill missing slots in order,
    # rejecting a second one-copy ring/ear before coordinate ascent begins.
    for pair in (("ear1", "ear2"), ("ring1", "ring2")):
        first, second = pair
        first_item, second_item = equipment[first], equipment[second]
        first_name = str(first_item.get("Name") or first_item.get("Name2") or "")
        second_name = str(second_item.get("Name") or second_item.get("Name2") or "")
        if (first_name and first_name.casefold() == second_name.casefold()
                and second_name != "Empty"
                and (
                    bool(second_item.get("Rare"))
                    or bool(int(second_item.get("Resource Flags") or 0) & 0x8000)
                    or int(second_item.get("Accessible Count") or 1) < 2
                )):
            equipment[second] = gear.Empty
    for slot in active_slots:
        current_name = str(equipment.get(slot, gear.Empty).get("Name") or "Empty")
        if current_name != "Empty":
            continue
        for item in _slot_best(candidates.get(slot, [gear.Empty]), recipe):
            equipment[slot] = item
            if valid(equipment):
                break
        else:
            equipment[slot] = gear.Empty

    def hard_valid(current: dict) -> bool:
        if not valid(current):
            return False
        pdt, mdt = calculate_damage_taken(current, buffs, abilities)
        return not recipe.require_damage_cap or (
            pdt <= -recipe.pdt_target and mdt <= -recipe.mdt_target
        )

    def score(current: dict) -> tuple[float, ...]:
        values = []
        for stat, target in recipe.caps:
            total = _set_stat_total(current, stat)
            values.append(min(total, target))
        if recipe.require_damage_cap:
            pdt, mdt = calculate_damage_taken(current, buffs, abilities)
            # Improve the weaker requirement first so a pure-PDT set cannot
            # outrank a balanced legal set. Once both targets are met, resume
            # optimizing the recipe instead of rewarding defensive over-cap.
            pdt_progress = min(-pdt / recipe.pdt_target, 1.0)
            mdt_progress = min(-mdt / recipe.mdt_target, 1.0)
            values.extend((min(pdt_progress, mdt_progress), pdt_progress + mdt_progress))
        # A lexicographic tuple made the first named stat absolute: one point
        # of DA could beat any amount of Store TP, accuracy, or attack. Base
        # sets are optimizer seeds, so blend the priorities using rough
        # per-stat scales and avoid extreme, obviously unusable combinations.
        scales = {
            "HP": 25, "MP": 20, "Attack": 10, "Ranged Attack": 10,
            "Defense": 10, "Accuracy": 5, "Ranged Accuracy": 5,
            "Magic Accuracy": 5, "Magic Attack": 5, "Magic Evasion": 5,
            "Evasion": 5, "STR": 5, "DEX": 5, "VIT": 5, "AGI": 5,
            "INT": 5, "MND": 5, "CHR": 5,
        }
        objective_score = 0.0
        for index, stat in enumerate(recipe.objective):
            total = _set_stat_total(current, stat)
            weight = max(0.35, 1.0 - index * 0.12)
            objective_score += weight * total / scales.get(stat, 1)
        values.append(objective_score)
        return tuple(values)

    for _pass in range(3):
        changed = False
        # Evaluate paired accessories together. Single-slot coordinate ascent
        # cannot discover a bonus when neither half improves the score alone.
        for first, second in (("ear1", "ear2"), ("ring1", "ring2")):
            if first not in active_slots and second not in active_slots:
                continue
            first_items = (_slot_best(candidates.get(first, [gear.Empty]), recipe)
                           if first in active_slots else [equipment[first]])
            second_items = (_slot_best(candidates.get(second, [gear.Empty]), recipe)
                            if second in active_slots else [equipment[second]])
            current_pair = (equipment[first], equipment[second])
            best_pair = current_pair
            best_score = score(equipment) if valid(equipment) else tuple(
                float("-inf") for _ in range(8)
            )
            for first_item, second_item in product(first_items, second_items):
                equipment[first], equipment[second] = first_item, second_item
                if valid(equipment) and score(equipment) > best_score:
                    best_pair, best_score = (first_item, second_item), score(equipment)
            equipment[first], equipment[second] = best_pair
            changed |= best_pair != current_pair
        for slot in active_slots:
            current = equipment[slot]
            best, best_score = current, score(equipment) if valid(equipment) else tuple(float("-inf") for _ in range(8))
            for item in _slot_best(candidates.get(slot, [gear.Empty]), recipe):
                equipment[slot] = item
                if valid(equipment) and score(equipment) > best_score:
                    best, best_score = item, score(equipment)
            equipment[slot] = best
            changed |= best is not current
        if not changed:
            break
    warnings = []
    apply_ear_slot_rules(equipment)
    if recipe.require_damage_cap and not hard_valid(equipment):
        pdt, mdt = calculate_damage_taken(equipment, buffs, abilities)
        warnings.append(
            f"Could not meet the PDT {recipe.pdt_target:.0f}% / MDT {recipe.mdt_target:.0f}% "
            "requirement with the selected gear pool "
            f"(PDT {-pdt:.0f}%, MDT {-mdt:.0f}%)."
        )
    cap_results = []
    for stat, target in recipe.caps:
        total = _set_stat_total(equipment, stat)
        cap_results.append({"stat": stat, "target": target, "reached": total})
        if total < target:
            warnings.append(
                f"Best modeled {stat} from the selected gear is {total:.0f}/{target:.0f}; "
                "the target is not available from this gear pool."
            )
    return BuildSet(
        name,
        {slot: equipment[slot] for slot in SET_SLOTS},
        score(equipment),
        pinned_notes,
        warnings,
        cap_results,
    )


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


def profile_catalog_plan(job: str, profile_payloads: Iterable[dict], ws_tp: int) -> list[tuple[ProfileRecipe, dict]]:
    """Describe the base catalog before any gear search runs.

    The plan is intentionally derived from the imported profile.  Utility
    recipes are only added when the profile already uses them, while the
    standard defense and TP families are always available.  Weapon-skill
    families are generated only when an existing set can be mapped to a
    modeled weapon skill.
    """
    payloads = list(profile_payloads)
    names = {str(payload.get("name") or "") for payload in payloads}
    plan: list[tuple[ProfileRecipe, dict]] = []
    defense_names = {"Dt", "Evasion", "MEVA"}
    for recipe in profile_recipes(job, names):
        plan.append((recipe, {
            "section_type": "Defense" if recipe.name in defense_names else "Utility",
            "family": recipe.name,
            "variant": "Default",
            "optimization_state": "ready",
        }))

    for recipe in TP_RECIPES:
        variant = {
            "Hybrid": "Hybrid · Default",
            "Hybrid_Acc": "Hybrid · Accuracy",
            "Hybrid_HighAcc": "Hybrid · High accuracy",
        }.get(recipe.name.removeprefix("Tp_"), recipe.name.removeprefix("Tp_"))
        plan.append((recipe, {
            "section_type": "TP",
            "family": "TP",
            "variant": variant,
            "optimization_state": "base",
            "optimizer": {
                "action": "attack round",
                "metric": "Time to WS",
                **optimizer_scenario(recipe.name, ws_tp),
            },
        }))

    ws_families: dict[str, str] = {}
    for payload in payloads:
        descriptor = payload.get("descriptor") or {}
        family = str(descriptor.get("family") or "")
        ws_name = str(descriptor.get("ws_name") or "")
        if descriptor.get("role") == "ws" and family and ws_name:
            ws_families.setdefault(family, ws_name)
    for family, ws_name in sorted(ws_families.items(), key=lambda item: item[0].casefold()):
        for variant, objective, defensive in WS_VARIANTS:
            set_name = f"{family}_{variant}"
            plan.append((
                ProfileRecipe(
                    set_name,
                    objective,
                    require_damage_cap=defensive,
                    pdt_target=50,
                    mdt_target=25 if defensive else 50,
                ),
                {
                    "section_type": "Weapon skill",
                    "family": family,
                    "variant": variant,
                    "optimization_state": "base",
                    "optimizer": {
                        "action": "weapon skill",
                        "ws_name": ws_name,
                        "metric": "Damage dealt",
                        **optimizer_scenario(set_name, ws_tp),
                    },
                },
            ))
    return plan


def build_profile_catalog(job: str, profile_payloads: Iterable[dict],
                          candidates: dict[str, list[dict]], ws_tp: int, *,
                          buffs: dict | None = None,
                          abilities: dict | None = None) -> dict:
    """Generate a complete armor-only LAC base catalog.

    This pure generation boundary keeps Qt responsible for orchestration and
    review only.  It returns the same set/detail shape used by the optimizer
    refinement and publishing paths.
    """
    payloads = list(profile_payloads)
    overlays = weapon_overlays(payloads)
    payload_by_name = {
        str(payload.get("name") or ""): payload for payload in payloads
    }
    result: dict[str, dict] = {}
    warnings: list[str] = []
    recipe_details: dict[str, dict] = {}
    # Keep each fixed weapon layer distinct. Two overlays can share the same
    # armor while still requiring different LAC set names.
    used_overlay_names: set[str] = set()

    def detail_for(recipe: ProfileRecipe, metadata: dict, section_name: str,
                   overlay_name: str = "") -> dict:
        detail = {
            "objective": recipe.objective,
            "caps": recipe.caps,
            "require_damage_cap": recipe.require_damage_cap,
            "pdt_target": recipe.pdt_target,
            "mdt_target": recipe.mdt_target,
            "section_type": metadata.get("section_type", "Utility"),
            "family": metadata.get("family", recipe.name),
            "variant": metadata.get("variant", "Default"),
            "optimization_state": metadata.get("optimization_state", "ready"),
        }
        if metadata.get("optimizer"):
            detail["optimizer"] = {
                **metadata["optimizer"],
                **optimizer_scenario(section_name, ws_tp),
            }
        if overlay_name:
            detail["overlay_name"] = overlay_name
        return detail

    def record_warnings(detail: dict, label: str, build: BuildSet):
        detail["cap_results"] = list(build.cap_results)
        direct_warnings = [f"{label}: {warning}" for warning in build.warnings]
        detail["direct_warnings"] = direct_warnings
        warnings.extend(direct_warnings)

    for recipe, metadata in profile_catalog_plan(job, payloads, ws_tp):
        pinned = pin_unmodeled_slots(payloads, recipe.name)
        imported = payload_by_name.get(recipe.name, {})
        starting = {
            slot: item for slot, item in (imported.get("gearset") or {}).items()
            if slot in SET_SLOTS and str(item.get("Name") or "Empty") != "Empty"
        }
        base = build_stat_set(
            recipe.name,
            candidates,
            recipe,
            pinned=pinned,
            starting=starting,
            buffs=buffs,
            abilities=abilities,
        )
        result[recipe.name] = base.equipment
        base_detail = detail_for(recipe, metadata, recipe.name)
        recipe_details[recipe.name] = base_detail
        record_warnings(base_detail, recipe.name, base)

        for overlay in overlays:
            overlay_items = overlay.get("gearset") or {}
            weapons = {
                slot: overlay_items[slot]
                for slot in overlay.get("specified_slots", ())
                if slot in WEAPON_SLOTS and slot in overlay_items
            }
            built = build_stat_set(
                recipe.name,
                candidates,
                recipe,
                weapons=weapons,
                pinned=pinned,
                starting=starting,
                buffs=buffs,
                abilities=abilities,
            )
            overlay_name = str(overlay.get("name") or weapon_category(overlay))
            section_name = f"{recipe.name}_{weapon_overlay_suffix(overlay)}"
            # A profile can contain duplicate weapon declarations. Preserve
            # the first deterministic layer instead of duplicate catalog rows.
            if section_name in used_overlay_names:
                continue
            used_overlay_names.add(section_name)
            result[section_name] = built.equipment
            detail = detail_for(
                recipe,
                metadata,
                section_name,
                overlay_name,
            )
            detail["weapon_category"] = weapon_category(overlay)
            detail["weapon_overlay"] = overlay_name
            recipe_details[section_name] = detail
            record_warnings(detail, f"{recipe.name}/{overlay_name}", built)

    return {
        "sets": result,
        "warnings": warnings,
        "overlays": [(item["name"], weapon_category(item)) for item in overlays],
        "overlay_items": overlays,
        "recipe_details": recipe_details,
    }
