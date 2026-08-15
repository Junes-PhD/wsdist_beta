"""Character-scoped bridge and gear catalog for GearSetBuilder exports."""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from pathlib import Path

import gear as gear_pyfile

try:
    from ffxiah_models import FFXIAH_MODELS
except ImportError:
    FFXIAH_MODELS = {}

from curated_item_models import CURATED_ITEM_MODELS, UNVERIFIED_ITEM_MODELS


SLOTS = ["main", "sub", "ranged", "ammo", "head", "neck", "ear1", "ear2",
         "body", "hands", "ring1", "ring2", "back", "waist", "legs", "feet"]
RESOURCE_BITS = {
    "main": 0, "sub": 1, "ranged": 2, "ammo": 3, "head": 4, "body": 5,
    "hands": 6, "legs": 7, "feet": 8, "neck": 9, "waist": 10,
    "ear1": 11, "ear2": 12, "ring1": 13, "ring2": 14, "back": 15,
}
JOB_IDS = ["war", "mnk", "whm", "blm", "rdm", "thf", "pld", "drk", "bst",
           "brd", "rng", "sam", "nin", "drg", "smn", "blu", "cor", "pup",
           "dnc", "sch", "geo", "run"]

# These Limbus items are represented by rank-30 rows in gear.py.  A bridge
# record with no augment payload must use the live item description instead;
# otherwise the curated max-rank row leaks into a character's actual gear.
# Keep this small table in sync with gearsetbuilder/stats.lua's base_by_id.
BASE_ITEM_MODELS = {
    26119: {"Defense": 10, "HP": 100, "Gear Haste": 5, "DT": -5,
            "Pet:Accuracy": 15, "Ranged Accuracy": 15, "Magic Accuracy": 15},
    26234: {"Defense": 10, "MP": 30, "Spell interruption rate down": -3,
            "DT": -10, "Pet:Accuracy": 15, "Pet:Ranged Accuracy": 15,
            "Pet:Magic Accuracy": 15},
    26275: {"Defense": 20, "Weapon Skill Damage": 11},
    26276: {"Defense": 18, "Magic Damage": 33},
}
# Older GearSetBuilder exports classified Halasz Earring's magic critical hit
# rate as ordinary physical Crit Rate.  Keep loading those exports correctly
# while new scans use the canonical simulator stat name.
ITEM_STAT_ALIASES = {
    27535: {"Crit Rate": "Magic Crit Rate II"},
}
SKILL_NAMES = {
    1: "Hand-to-Hand", 2: "Dagger", 3: "Sword", 4: "Great Sword", 5: "Axe",
    6: "Great Axe", 7: "Scythe", 8: "Polearm", 9: "Katana", 10: "Great Katana",
    11: "Club", 12: "Staff", 13: "Archery", 14: "Marksmanship", 15: "Throwing",
}

# These imports can carry a rank without an explicit path in older bridge
# files. REMA/Ergon and Oboro JSE weapons have the single Path A augment;
# JSE necks and Unity accessories likewise have one path. Divergence weapons
# are *not* included because their missing path is genuinely ambiguous (A/B/C).
SINGLE_PATH_A_WEAPONS = {
    # REMA / Ergon (including ranged and instrument weapons represented by
    # the bridge as weapons rather than armor).
    "Aeneas", "Aettir", "Almace", "Amanomurakumo", "Annihilator",
    "Apocalypse", "Armageddon", "Bravura", "Burtgang", "Caladbolg",
    "Carnwenhan", "Chango", "Claustrum", "Conqueror", "Death Penalty",
    "Epeolatry", "Excalibur", "Fail-Not", "Fomalhaut", "Gandiva", "Gastraphetes",
    "Godhands", "Gungnir", "Guttler", "Heishi", "Hvergelmir", "Idris",
    "Kenkonken", "Kikoku", "Kogarasumaru", "Laevateinn", "Liberator",
    "Mandau", "Masamune", "Mjollnir", "Murgleis", "Nagi", "Nirvana",
    "Ragnarok", "Redemption", "Rhongomiant", "Ryunohige", "Sandung",
    "Sequence", "Spharai", "Terpsichore", "Tizona",
    "Tupsimati", "Twashtar", "Ukonvasara", "Vajra", "Verethragna",
    "Yagrush", "Yoichinoyumi",
    # Oboro JSE weapons.
    "Areadbhar", "Arktoi", "Coeus", "Cronus", "Deathlocke", "Dunna",
    "Egeking", "Gridarvor", "Kaladanda", "Kurikaranotachi", "Lionsquall",
    "Mimesis", "Minos", "Nyepel", "Ohtas", "Polyhymnia", "Priwen",
    "Shigi", "Sindri", "Terpander",
}
BASE_PARAMETERS = ("STR", "DEX", "VIT", "AGI", "INT", "MND", "CHR")

# Exact character observations from augment_report.md.  These are rank deltas,
# not full item totals: apply them only when a bridge export has not supplied a
# separate rank payload of its own.  Keep paths explicit, including armor's
# legitimate Path D, so one path can never fall through to a neighbouring one.
EXACT_RANK_AUGMENTS = {
    ("abyssal beads +1", "A", 1): {"STR": 1, "Store TP": 1, "PDL": 1},
    ("coiste bodhar", "A", 1): {"Attack": 1},
    ("coiste bodhar", "A", 3): {"Attack": 3},
    ("loxotic mace +1", "A", 5): {"DMG": 13},
    ("loxotic mace +1", "A", 11): {
        "DMG": 25, "Accuracy": 24, "Magic Accuracy": 24,
        "Weapon Skill Damage": 2,
    },
    ("samurai's nodowa +2", "A", 6): {
        "STR": 6, "Store TP": 2, "PDL": 2,
    },
    ("schere earring", "A", 1): {"Accuracy": 1},
    ("schere earring", "A", 2): {"Accuracy": 2},
    ("warrior's bead necklace +2", "A", 12): {
        "HP": 37, "STR": 7, "DEX": 7, "DA": 4,
    },
    ("argute stole +1", "A", 5): {
        "INT": 3, "MND": 3, "Magic Damage": 5,
    },
    ("assassin's gorget +1", "A", 1): {"DEX": 1, "AGI": 1, "Evasion": 1, "TA": 1},
    ("bard's charm +1", "A", 8): {
        "DEX": 8, "CHR": 8, "Store TP": 2, "PDL": 3,
    },
    ("cleric's torque +1", "A", 3): {"INT": 2, "MND": 2, "Fast Cast": 1},
    ("dragoon collar +1", "A", 1): {"STR": 1, "VIT": 1, "PDL": 1},
    ("mirage stole +1", "A", 3): {
        "STR": 3, "DEX": 3, "Store TP": 1, "Crit Rate": 1,
    },
    ("kikoku", "A", 2): {"DMG": 1},
    ("crocea mors", "C", 17): {
        "DMG": 5, "Elemental WS Damage%": 60, "EnSpell Damage%": 340,
    },
    ("akademos", "A", 15): {"MP": 80, "INT": 20, "Magic Attack": 20},
    ("bagua charm +1", "A", 1): {"MP": 1},
    ("cabal. sword", "C", 9): {"HP": 90, "Cure Potency": 9, "Refresh": 1},
    ("bihu knife", "C", 3): {"DA": 1},
    ("coiste bodhar", "A", 6): {"Attack": 6},
    ("dgn. collar +1", "A", 1): {"STR": 1, "VIT": 1, "PDL": 1, "Pet: DT": -1},
    ("emet harness +1", "A", 1): {"Evasion": 2},
    ("eschite greaves", "A", 15): {"HP": 80, "Enmity": 7, "PDT": -4},
    ("futhark torque +2", "A", 16): {"HP": 33, "STR": 10, "MND": 10, "DT": -5},
    ("hippo. socks +1", "A", 15): {"Resist Bind": 45, "Evasion": 20, "All Attributes": 10},
    ("kgt. beads +1", "A", 12): {"HP": 24, "VIT": 7, "MND": 7, "DT": -4},
    ("kali", "A", 15): {"DMG": 15, "CHR": 15, "Magic Accuracy": 15},
    ("lathi", "A", 15): {"MP": 80, "INT": 20, "Magic Attack": 20},
    ("nibiru harp", "D", 15): {"Magic Evasion": 20, "PDT": -3, "MDT": -3},
    ("queller rod", "B", 15): {"MND": 15, "Magic Accuracy": 15},
    ("ryuo sune-ate +1", "C", 15): {"HP": 65, "Store TP": 5, "Subtle Blow": 8},
    ("ryuo tekko +1", "D", 15): {"DEX": 12, "Accuracy": 25, "DA": 4},
    ("sailfi belt +1", "A", 15): {"STR": 15, "DA": 5},
    ("tatena. haidate +1", "A", 15): {
        "Accuracy": 60, "STR": 10, "DEX": 10, "VIT": 10, "AGI": 10,
        "INT": 10, "MND": 10, "CHR": 10, "TA": 3,
    },
    ("vanya hood", "D", 15): {"MP": 50, "Fast Cast": 10, "Gear Haste": 2},
    # Remaining reviewed Kroot observations.  These are rank additions and
    # are applied only when the imported record did not decode them already.
    ("adhemar bonnet +1", "A", 15): {"DEX": 12, "AGI": 12, "Accuracy": 20},
    ("adhemar bonnet +1", "B", 15): {"STR": 12, "DEX": 12, "Attack": 20},
    ("adhemar jacket +1", "A", 15): {"DEX": 12, "AGI": 12, "Accuracy": 20},
    ("adhemar jacket +1", "B", 15): {"STR": 12, "DEX": 12, "Attack": 20},
    ("adhemar wrist. +1", "A", 15): {"DEX": 12, "AGI": 12, "Accuracy": 20},
    ("carmine cuisses +1", "D", 15): {"Accuracy": 20, "Attack": 12, "Dual Wield": 6},
    ("carmine greaves +1", "B", 15): {"Accuracy": 12, "DEX": 12, "MND": 20},
    ("comm. charm +1", "A", 1): {"STR": 1, "AGI": 1, "Magic Damage": 1, "Magic Attack": 1},
    ("ichigohitofuri", "A", 15): {"DMG": 30, "STR": 20, "Attack": 20},
    ("kaykaus cuffs +1", "A", 15): {"MP": 80, "MND": 12, "Magic Accuracy": 20},
    ("lugra earring +1", "A", 1): {"Defense": 1},
    ("lustr. harness +1", "A", 15): {"Attack": 20, "STR": 8, "DA": 3},
    ("lustr. subligar +1", "A", 15): {"Attack": 20, "STR": 8, "DA": 3},
    ("lustra. leggings +1", "D", 15): {"HP": 65, "STR": 15, "DEX": 15},
    ("mnk. nodowa +1", "A", 8): {"DEX": 4, "MND": 4, "Kick Attacks": 8, "PDL": 3},
    ("montante +1", "A", 15): {"DMG": 20, "Accuracy": 40, "Magic Accuracy": 40, "HP": 100},
    ("priwen", "A", 15): {"HP": 50, "Magic Evasion": 50, "DT": -3},
    ("refined grip +1", "A", 15): {"Defense": 20, "Parrying Skill": 10},
    ("ninja nodowa +1", "A", 9): {"DEX": 5, "AGI": 5, "Daken": 9, "PDL": 3},
    ("psycloth lappas", "D", 15): {"MP": 80, "Magic Accuracy": 15, "Fast Cast": 7},
    ("pursuer's beret", "A", 11): {"AGI": 8, "Rapid Shot": 8, "Subtle Blow": 5},
    ("pursuer's cuffs", "A", 15): {"AGI": 10, "Rapid Shot": 10, "Subtle Blow": 7},
    ("pursuer's gaiters", "D", 15): {"Ranged Accuracy": 10, "Rapid Shot": 10, "Recycle": 15},
    ("rawhide vest", "D", 15): {"HP": 50, "Subtle Blow": 7, "TA": 2},
    ("seeth. bomblet +1", "A", 1): {"STR": 1},
    ("smn. collar +1", "A", 1): {"MP": 1, "All Attributes": 1, "Blood Pact Damage": 1},
    ("vanya cuffs", "B", 15): {"Healing Magic Skill": 20, "Fast Cast": 7, "MDT": -3},
    ("vanya slops", "C", 15): {"MND": 10, "SIRD": 15, "Conserve MP": 6},
    ("warder's charm +1", "A", 1): {"Skillchain Damage": 1},
    ("aettir", "A", 15): {"Accuracy": 70, "Magic Evasion": 50, "Weapon Skill Damage": 10},
    ("sagitta", "A", 23): {"Chance of Double Damage": 46, "Store TP": 23, "DMG": 11},
    ("warrior's bead necklace +1", "A", 8): {"HP": 21, "STR": 4, "DEX": 4, "DA": 2},
    ("samurai's nodowa +1", "A", 1): {"STR": 1, "Store TP": 1, "PDL": 1},
    ("tatena. gote +1", "A", 15): {"Accuracy": 40, "All Attributes": 10, "TA": 4},
    ("tatena. sune. +1", "A", 15): {"Accuracy": 60, "All Attributes": 10, "TA": 3},
    ("souv. cuirass +1", "C", 15): {"HP": 105, "Enmity": 9, "Potency of Cure Effect Received": 15},
    ("souv. diechlings +1", "C", 15): {"HP": 105, "Enmity": 9, "Potency of Cure Effect Received": 15},
    ("souv. hands ch. +1", "D", 15): {"HP": 65, "Shield Skill": 15, "PDT": -4},
    ("souv. schaller +1", "C", 15): {"HP": 105, "Enmity": 9, "Potency of Cure Effect Received": 15},
    ("souveran schuhs +1", "C", 15): {"HP": 105, "Enmity": 9, "Potency of Cure Effect Received": 15},
}

# GearSetBuilder keeps the game client's abbreviated display names for several
# JSE necks.  The report uses their expanded names where that is clearer, so
# canonicalize only these known spellings before looking up an exact delta.
EXACT_RANK_AUGMENT_NAME_ALIASES = {
    "asn. gorget +1": "assassin's gorget +1",
    "bard's charm +1": "bard's charm +1",
    "clr. torque +1": "cleric's torque +1",
    "dgn. collar +1": "dragoon collar +1",
    "mir. stole +1": "mirage stole +1",
    "sam. nodowa +2": "samurai's nodowa +2",
    "war. beads +2": "warrior's bead necklace +2",
    "comm. charm +1": "comm. charm +1",
    "seeth. bomblet +1": "seething bomblet +1",
    "war. beads +1": "warrior's bead necklace +1",
    "souv. handsch. +1": "souv. hands ch. +1",
}


def _exact_rank_augment(record: dict) -> dict:
    """Attach a reviewed character-observed rank delta when the scan lacks one."""
    if record.get("augment_rank_stats") or record.get("augment_rank_stats_in_total"):
        return record
    try:
        rank = int(record.get("augment_rank") or 0)
    except (TypeError, ValueError):
        return record
    path = _normalize_augment_path(record.get("augment_path"))
    if not path:
        path = _inferred_single_path(record, _slot_names(int(record.get("slots_mask") or 0)))
    name = str(record.get("name") or "").casefold()
    name = EXACT_RANK_AUGMENT_NAME_ALIASES.get(name, name)
    key = (name, path, rank)
    delta = EXACT_RANK_AUGMENTS.get(key)
    if not delta:
        return record
    # Character notes sometimes use the game's compact "All Attributes"
    # label. Expand it into the simulator's individual stat fields so the
    # copied value is not merely visible metadata.
    delta = deepcopy(delta)
    all_attributes = delta.pop("All Attributes", None)
    if all_attributes is not None:
        for parameter in BASE_PARAMETERS:
            delta.setdefault(parameter, all_attributes)
    enriched = deepcopy(record)
    enriched["augment_rank_stats"] = deepcopy(delta)
    enriched["augment_rank_stats_in_total"] = False
    enriched["model_warning"] = "; ".join(filter(None, (
        str(enriched.get("model_warning") or ""),
        "Exact rank delta from augment audit",
    )))
    return enriched


def hoxne_stat_bonus(mastery_rank: int) -> int:
    """Return Hoxne Earring's all-parameter bonus for Mastery Rank 1-10."""
    rank = max(1, min(10, int(mastery_rank)))
    return rank * 10 - 40 if rank <= 4 else (rank - 4) * 5


def _apply_hoxne_mastery_rank(item: dict, mastery_rank: int) -> dict:
    """Replace the bridge's static Hoxne estimate with the chosen character rank."""
    if str(item.get("Name") or "").casefold() != "hoxne earring":
        return item
    adjusted = item.copy()
    for parameter in BASE_PARAMETERS:
        adjusted[parameter] = hoxne_stat_bonus(mastery_rank)
    suffix = f"MR{max(1, min(10, int(mastery_rank))):02d}"
    adjusted["Name2"] = f"Hoxne Earring {suffix}"
    return adjusted


def bridge_hash(text: str) -> str:
    value = 5381
    for byte in text.encode("utf-8"):
        value = (value * 33 + byte) % 4294967291
    return f"{value:08x}"


def file_hash(path: Path) -> str:
    return bridge_hash(path.read_text(encoding="utf-8"))


def _copy_stats(record: dict) -> dict:
    stats = record.get("stats") or {}
    return {str(key): value for key, value in stats.items() if isinstance(value, (int, float))}


def _canonicalize_item_stats(item_id: int, stats: dict) -> dict:
    for source, target in ITEM_STAT_ALIASES.get(item_id, {}).items():
        if source in stats:
            stats[target] = stats.get(target, 0) + stats.pop(source)
    # Older bridge descriptions flattened the Steelflash/Bladeborn set bonus
    # onto Steelflash itself. Keep only each earring's individual stats here;
    # equipment_rules adds DA +7 once when both earrings are worn.
    if item_id == 28520:  # Steelflash Earring
        stats.pop("DA", None)
        stats.pop("Double Attack", None)
        stats.setdefault("Accuracy", 8)
        stats.setdefault("Store TP", 1)
    elif item_id == 28521:  # Bladeborn Earring
        stats.pop("DA", None)
        stats.pop("Double Attack", None)
        stats.setdefault("Attack", 8)
        stats.setdefault("Store TP", 1)
    return stats


def _record_is_unaugmented(record: dict) -> bool:
    """Return true when a scan has no current augment contribution."""
    augment_type = str(record.get("augment_type") or "").strip().lower()
    return (augment_type in {"", "unaugmented"}
            and not record.get("augments")
            and record.get("augment_path") in (None, "")
            and record.get("augment_rank") in (None, "", 0)
            and record.get("augment_trial") in (None, "", 0)
            and not record.get("augment_rank_stats"))


def _record_is_augmented(record: dict) -> bool:
    """Return whether an inventory/profile record has item-specific augments."""
    if record.get("augments") or record.get("augment_rank_stats") or record.get("augment_stats"):
        return True
    for key in ("augment_path", "augment_trial"):
        if record.get(key) not in (None, "", 0):
            return True
    try:
        if int(record.get("augment_rank") or 0) > 0:
            return True
    except (TypeError, ValueError):
        if str(record.get("augment_rank") or "").strip():
            return True
    augment_type = str(record.get("augment_type") or "").strip().casefold()
    if augment_type and augment_type not in {"none", "base", "unaugmented"}:
        return True
    lac = record.get("lac") or {}
    if not isinstance(lac, dict):
        lac = {}
    if lac.get("AugPath") not in (None, "") or lac.get("Augment"):
        return True
    try:
        return int(lac.get("AugRank") or 0) > 0 or int(lac.get("AugTrial") or 0) > 0
    except (TypeError, ValueError):
        return bool(lac.get("AugRank") or lac.get("AugTrial"))


def _slot_names(mask: int) -> list[str]:
    return [slot for slot, bit in RESOURCE_BITS.items() if mask & (1 << bit)]


def _jobs(mask: int) -> list[str]:
    return [job for index, job in enumerate(JOB_IDS, start=1) if mask & (1 << index)]


def _item_type(record: dict, slots: list[str]) -> str:
    name = str(record.get("name") or "").lower()
    skill = int(record.get("skill") or 0)
    # GearSetBuilder preserves the client resource weapon type. Type 5 is a
    # shield; unlike weapons and grips, shields have no skill value and many
    # names (Aegis, Ochain, etc.) do not contain the word "shield".
    if "sub" in slots and str(record.get("weapon_type") or "") == "5":
        return "Shield"
    if "sub" in slots and "shield" in name:
        return "Shield"
    if "grip" in name or "strap" in name:
        return "Grip"
    if skill and ("main" in slots or "sub" in slots):
        return "Weapon"
    if "ranged" in slots:
        return "Ranged"
    if "ammo" in slots:
        return "Ammo"
    return "Armor"


def _model_name(record: dict) -> str:
    augment = record.get("augments") or []
    suffix = []
    if record.get("augment_path") is not None:
        suffix.append(f"path={record['augment_path']}")
    if record.get("augment_rank") is not None:
        suffix.append(f"rank={record['augment_rank']}")
    suffix.extend(str(value) for value in augment)
    return str(record.get("name") or "Unknown") + (" [" + "; ".join(suffix) + "]" if suffix else "")


def _normalize_augment_path(value: object) -> str:
    """Return the stable A/B/C/D path token exported by GearSetBuilder."""
    text = str(value or "").strip().upper()
    match = re.search(r"(?:PATH\s*)?([ABCD])$", text)
    return match.group(1) if match else text


def _inferred_single_path(record: dict, slots: list[str]) -> str:
    """Infer Path A only for equipment whose game system has one path."""
    rank = record.get("augment_rank")
    try:
        if int(rank or 0) <= 0:
            return ""
    except (TypeError, ValueError):
        if rank in (None, ""):
            return ""
    if _normalize_augment_path(record.get("augment_path")):
        return ""
    name = str(record.get("name") or "").strip()
    if name in SINGLE_PATH_A_WEAPONS:
        return "A"
    augment_type = str(record.get("augment_type") or "").strip().casefold()
    if augment_type == "dynamis" and any(str(slot).casefold() == "neck" for slot in slots):
        return "A"
    if augment_type == "dynamis" and not any(
        str(slot).casefold() in {"main", "sub", "ranged"} for slot in slots
    ):
        # Divergence accessories and armor have one path.  Weapons are left
        # unresolved because their omitted path may be A, B, or C.  Use the
        # normalized slot list here instead of the inferred item type: imported
        # records often omit skill metadata even when the item is a weapon.
        return "A"
    if augment_type in {"unity", "unity accessory", "unity accessories"} and not any(
        str(slot).casefold() in {"main", "sub", "ranged"} for slot in slots
    ):
        # Unity +1 accessories are rank-15, single-path Odyssey augments.
        return "A"
    return ""


def _builtin_augment_path(item: dict) -> str:
    """Read explicit path metadata, with legacy Name2 labels as fallback."""
    explicit = _normalize_augment_path(item.get("Augment Path"))
    if explicit:
        return explicit
    label = str(item.get("Name2") or "").upper()
    match = re.search(r"(?:\bR\d+|\+\d+|\bPATH\s*|\s)([ABCD])(?:\s*\(SUB\))?$", label)
    return match.group(1) if match else ""


def _builtin_augment_rank(item: dict) -> int | None:
    """Read a built-in rank, including older rows that encode it only in Name2."""
    try:
        if item.get("Rank") not in (None, ""):
            return int(item["Rank"])
    except (TypeError, ValueError):
        pass
    match = re.search(r"\bR(\d+)\b", str(item.get("Name2") or ""), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _accessible_count(record: dict) -> int:
    declared = int(record.get("accessible_count") or 0)
    if declared > 0:
        return declared
    # Older scans stored the authoritative availability on each location but
    # left the aggregate counter at zero.  Preserve the safe location-based
    # count so accessible gear remains usable without admitting storage-only
    # records.
    return sum(int(location.get("count") or 1) for location in (record.get("locations") or [])
               if location.get("available") is True or location.get("source") == "accessible")


def _gear_record(record: dict, *, eligible: bool = True, hoxne_mastery_rank: int = 5) -> dict:
    slots = _slot_names(int(record.get("slots_mask") or 0))
    item_id = int(record.get("item_id") or 0)
    stats = (deepcopy(BASE_ITEM_MODELS[item_id])
             if item_id in BASE_ITEM_MODELS and _record_is_unaugmented(record)
             else _copy_stats(record))
    separate_rank_stats = record.get("augment_rank_stats")
    rank_stats_in_total = bool(record.get("augment_rank_stats_in_total"))
    if isinstance(separate_rank_stats, dict) and separate_rank_stats and not rank_stats_in_total:
        # Some bridge producers provide the rank delta separately from the
        # displayed item total. Apply it once so calculations and hover text
        # agree; scanner-produced totals explicitly set the flag above.
        for key, value in separate_rank_stats.items():
            if isinstance(value, (int, float)):
                stats[str(key)] = stats.get(str(key), 0) + value
    _canonicalize_item_stats(item_id, stats)
    result = deepcopy(stats)
    name = str(record.get("name") or "Unknown")
    name2 = _model_name(record)
    accessible_count = _accessible_count(record)
    has_stats = bool(stats)
    unknown_augments = tuple(str(value) for value in (record.get("unknown_augments") or ()) if str(value).strip())
    # A scan can know the complete base item and all normal stats while still
    # lacking one specialized augment effect.  That gear is useful for most
    # simulations, but must be visibly marked instead of silently pretending
    # the missing effect was modeled. Items with no stats remain ineligible.
    usable_model = bool(record.get("model_complete", False) or has_stats)
    result.update({
        "Name": name,
        "Name2": name2,
        "Type": _item_type(record, slots),
        "Skill Type": SKILL_NAMES.get(int(record.get("skill") or 0), "None"),
        "Jobs": _jobs(int(record.get("jobs_mask") or 0)),
        "Item ID": int(record.get("item_id") or 0),
        "Bridge Key": str(record.get("key") or ""),
        "Accessible Count": accessible_count,
        "Total Count": int(record.get("total_count") or record.get("count") or 0),
        "Model Complete": bool(record.get("model_complete", False)),
        "Eligible": bool(eligible and usable_model and accessible_count > 0),
        "LAC": deepcopy(record.get("lac") or {"Name": name}),
        "Slots": slots,
        "Augments": deepcopy(record.get("augments") or []),
    })
    augment_path = _normalize_augment_path(record.get("augment_path"))
    if not augment_path:
        augment_path = _inferred_single_path(record, slots)
    if augment_path:
        result["Augment Path"] = augment_path
    if record.get("augment_rank") not in (None, ""):
        result["Rank"] = int(record["augment_rank"])
    rank_stats = separate_rank_stats
    if isinstance(rank_stats, dict) and rank_stats:
        # Keep the separate contribution visible to the GUI and export tools.
        # The scanner may also mark these values as already included in stats;
        # this metadata is never added a second time by the bridge.
        result["Rank Stats"] = deepcopy(rank_stats)
        result["Rank Stats In Total"] = rank_stats_in_total
    if unknown_augments:
        result["Model Warning"] = "Unmodeled scan effect: " + ", ".join(unknown_augments)
        result["Unknown Augments"] = list(unknown_augments)
    if record.get("model_warning"):
        existing = str(result.get("Model Warning") or "")
        result["Model Warning"] = "; ".join(filter(None, (existing, str(record["model_warning"]))))
    if record.get("conditional_effects"):
        result["Conditional Effects"] = list(record["conditional_effects"])
    if item_id in (28520, 28521):
        result["Conditional Effects"] = [
            "Set: Steelflash Earring + Bladeborn Earring grants Double Attack +7%"
        ]
    if record.get("data_source"):
        result["Data Source"] = str(record["data_source"])
    resource_flags = int(record.get("resource_flags") or record.get("Resource Flags") or 0)
    exclusive = bool(record.get("exclusive", record.get("Exclusive", False)))
    augmented = _record_is_augmented(record)
    transferable = record.get("transferable", record.get("Transferable"))
    if transferable is None:
        # The SDK's no-delivery/no-trade bits are the conservative Ex test;
        # unknown legacy records remain non-transferable for cross-character
        # sharing, while the owning character can still use them normally.
        transferable = False
    result.update({
        "Resource Flags": resource_flags,
        "Rare": bool(resource_flags & 0x8000),
        "Exclusive": exclusive or bool(resource_flags & 0x6000),
        "Augmented": augmented,
        "Transferable": bool(transferable) and not augmented and not bool(resource_flags & 0x6000),
    })
    # Newer GearSetBuilder exports may include item-level metadata.  Preserve
    # it for optional optimizer filtering without feeding it into any damage
    # formulas.
    for key in ("item_level", "Item Level", "itemLevel", "ilvl", "ILvl"):
        if record.get(key) not in (None, ""):
            result["Item Level"] = record[key]
            break
    for key in ("description", "help_text", "help", "Description", "Help Text"):
        if record.get(key) not in (None, ""):
            result["Description"] = str(record[key])
            break
    return _apply_hoxne_mastery_rank(result, hoxne_mastery_rank)


def _with_ffxiah_model(record: dict) -> dict:
    """Fill an incomplete bridge item from the archived FFXIAH description."""
    item_id = int(record.get("item_id") or 0)
    model = FFXIAH_MODELS.get(str(item_id))
    if not model or record.get("model_complete") or record.get("stats"):
        return record
    enriched = deepcopy(record)
    enriched["name"] = enriched.get("name") or model.get("Name")
    enriched["slots_mask"] = enriched.get("slots_mask") or model.get("slots_mask", 0)
    enriched["jobs_mask"] = enriched.get("jobs_mask") or model.get("jobs_mask", 0)
    enriched["skill"] = enriched.get("skill") or model.get("skill", 0)
    enriched["stats"] = deepcopy(model.get("stats") or {})
    enriched["model_complete"] = bool(model.get("complete"))
    return enriched


def _with_curated_model(record: dict) -> dict:
    """Fill an incomplete record from a reviewed, source-attributed model."""
    item_id = int(record.get("item_id") or 0)
    model = CURATED_ITEM_MODELS.get(item_id)
    # Some GearSetBuilder exports mark an item model complete while emitting
    # an empty stats array (common for older ammo/storage records). A reviewed
    # ID model should still fill that hole; non-empty exported stats remain
    # authoritative and are never overwritten.
    if not model or record.get("stats"):
        return record
    enriched = deepcopy(record)
    enriched["stats"] = deepcopy(model["stats"])
    enriched["model_complete"] = True
    enriched["stat_source"] = "curated"
    enriched["data_source"] = model["source"]
    if model.get("effects"):
        enriched["conditional_effects"] = list(model["effects"])
    return enriched


def _with_unverified_warning(record: dict) -> dict:
    """Keep newly-added items visible in audits without guessing their stats."""
    warning = UNVERIFIED_ITEM_MODELS.get(int(record.get("item_id") or 0))
    if not warning:
        return record
    enriched = deepcopy(record)
    enriched["model_warning"] = warning
    return enriched


def _with_builtin_model(record: dict, item: dict) -> dict:
    """Overlay the curated WSDist model without counting bridge stats twice."""
    item_id = int(record.get("item_id") or 0)
    # For these items the built-in rows are explicitly R30 models.  A
    # character-scoped bridge is authoritative for the base/current variant;
    # never overlay a max-rank row onto it.  This also repairs older bridge
    # files that were published before the base table was added.
    if (item_id in BASE_ITEM_MODELS and record.get("model_complete")
            and record.get("augment_rank") in (None, "", 0)):
        return item
    name = str(record.get("name") or "").lower()
    candidates = [value for value in gear_pyfile.all_gear.values()
                  if str(value.get("Name") or "").lower() == name]
    path = _normalize_augment_path(record.get("augment_path"))
    if path:
        # Divergence paths share one item ID. The path is separate inventory
        # metadata, so resolve it before rank or free-form description text.
        candidates = [value for value in candidates if _builtin_augment_path(value) == path]
        main_hand = [value for value in candidates
                     if not str(value.get("Name2") or "").lower().endswith("(sub)")]
        candidates = main_hand or candidates
    rank = record.get("augment_rank")
    if rank is not None:
        ranked = [
            value for value in candidates
            if _builtin_augment_rank(value) == int(rank)
        ]
        if len(ranked) == 1:
            candidates = ranked
        elif ranked:
            augment_text = " ".join(str(value).lower() for value in record.get("augments") or [])
            named = [value for value in ranked if augment_text and augment_text in str(value.get("Name2", "")).lower()]
            candidates = named if len(named) == 1 else []
        elif path and int(rank) == 15 and len(candidates) == 1:
            # Several older fixed-path armor rows encode their path but not
            # their R15 cap in Name2.  A path-specific R15 import may use the
            # sole matching row; intermediate ranks must never inherit it.
            pass
        else:
            candidates = []
    else:
        exact = [value for value in candidates if str(value.get("Name2") or "").lower() == name]
        candidates = exact or candidates
    if len(candidates) != 1:
        return item
    base = deepcopy(candidates[0])
    bridge_stats = item.copy()
    # Preserve the stable character identity and actual resource restrictions.
    for key, value in bridge_stats.items():
        if key not in {"Name", "Name2", "Type", "Skill Type", "Jobs", "Bridge Key",
                       "Accessible Count", "Total Count", "Model Complete", "Eligible",
                       "LAC", "Slots", "Augments"} and key not in base:
            # The reviewed built-in variant owns stats it explicitly models.
            # Bridge-only stats still fill gaps, but cannot erase a selected
            # path bonus with the shared unaugmented resource value.
            base[key] = value
    base["Name"] = item["Name"]
    base["Name2"] = item["Name2"]
    for key in ("Type", "Skill Type", "Jobs", "Accessible Count", "Total Count", "Model Complete", "Eligible"):
        base[key] = item[key]
    base["Bridge Key"] = item["Bridge Key"]
    base["LAC"] = item["LAC"]
    base["Slots"] = item["Slots"]
    base["Augments"] = item["Augments"]
    # A ranked/explicit variant is already fully represented by gear.py. For a
    # base model, add only decoded augment deltas to avoid adding full totals twice.
    if not rank and item.get("Model Complete"):
        for key, value in (record.get("augment_stats") or {}).items():
            base[key] = base.get(key, 0) + value
    return base


class BridgeStore:
    """Loads character bridge files and exposes a WSDist-compatible catalog."""

    def __init__(self, ashita_root: str | os.PathLike | None = None):
        self.ashita_root = Path(ashita_root).expanduser() if ashita_root else None
        self.bridge_path: Path | None = None
        self.loaded_mtime = 0.0
        self.data: dict = {}
        self.catalog: dict[str, dict] = {}
        self.by_key: dict[str, dict] = {}
        self.by_slot: dict[str, list[dict]] = {slot: [] for slot in SLOTS}
        self.characters: list[dict] = []
        self.hoxne_mastery_rank = 5

    def set_hoxne_mastery_rank(self, mastery_rank: int) -> None:
        """Set the character's Hoxne rank and rebuild an already-loaded catalog."""
        self.hoxne_mastery_rank = max(1, min(10, int(mastery_rank)))
        if self.data:
            self._build_catalog()

    def set_root(self, root: str | os.PathLike) -> None:
        selected = Path(root).expanduser().resolve()
        parts = [part.lower() for part in selected.parts]
        if len(parts) >= 3 and parts[-3:] == ["config", "addons", "gearsetbuilder"]:
            selected = selected.parents[2]
        elif len(parts) >= 2 and parts[-2:] == ["config", "addons"]:
            selected = selected.parents[1]
        elif selected.name.lower() == "config":
            selected = selected.parent
        self.ashita_root = selected

    def _root(self) -> Path:
        if self.ashita_root is None:
            raise ValueError("Select the Ashita installation directory first.")
        return self.ashita_root / "config" / "addons" / "gearsetbuilder"

    def discover(self) -> list[Path]:
        root = self._root()
        if not root.exists():
            return []
        return sorted(root.glob("*/wsdist_bridge.json"))

    def discover_characters(self) -> list[tuple[str, Path]]:
        """Return readable character labels and their bridge files."""
        characters = []
        for path in self.discover():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                character = data.get("character") or {}
                name = str(character.get("name") or path.parent.name)
                key = str(character.get("key") or path.parent.name)
                characters.append((f"{name} ({key})", path))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return sorted(characters, key=lambda entry: entry[0].lower())

    def load(self, path: str | os.PathLike) -> dict:
        bridge = Path(path).resolve()
        if bridge.parent.parent != self._root().resolve():
            raise ValueError("Bridge file is outside the selected GearSetBuilder directory.")
        self.data = json.loads(bridge.read_text(encoding="utf-8"))
        if int(self.data.get("schema_version", 0)) not in (1, 2):
            raise ValueError("Unsupported WSDist bridge schema.")
        self.bridge_path = bridge
        self.loaded_mtime = bridge.stat().st_mtime
        self._build_catalog()
        return self.data

    def load_character(self, key: str) -> dict:
        for path in self.discover():
            data = json.loads(path.read_text(encoding="utf-8"))
            if str(data.get("character", {}).get("key", "")).lower() == str(key).lower():
                return self.load(path)
        raise FileNotFoundError(f"No WSDist bridge found for {key}.")

    def _build_catalog(self) -> None:
        self.by_key = {}
        self.catalog = {}
        self.by_slot = {slot: [] for slot in SLOTS}
        for record in self.data.get("items", []):
            record = _exact_rank_augment(
                _with_unverified_warning(_with_curated_model(record))
            )
            record = _with_ffxiah_model(record)
            item = _with_builtin_model(
                record, _gear_record(record, hoxne_mastery_rank=self.hoxne_mastery_rank)
            )
            if not item["Bridge Key"]:
                continue
            self.by_key[item["Bridge Key"]] = item
            self.catalog[item["Name2"]] = item
            if item["Eligible"]:
                for slot in item["Slots"]:
                    self.by_slot.setdefault(slot, []).append(item)
        for slot in self.by_slot:
            self.by_slot[slot].sort(key=lambda item: item["Name2"].lower())

    def equipment_dict(self, include_empty: bool = True) -> dict[str, list[dict]]:
        result = {slot: list(self.by_slot.get(slot, [])) for slot in SLOTS}
        if include_empty:
            empty = gear_pyfile.Empty
            for slot in result:
                if not any(item.get("Name2") == "Empty" for item in result[slot]):
                    result[slot].insert(0, empty)
        return result

    def resolve_profile_item(self, item: dict | None) -> dict | None:
        if not item:
            return None
        name = str(item.get("name") or item.get("Name") or "")
        item_id = int(item.get("item_id") or item.get("Item ID") or 0)
        for record in self.data.get("items", []):
            if int(record.get("item_id") or 0) != item_id and item_id:
                continue
            if str(record.get("name") or "").lower() != name.lower():
                continue
            if int(record.get("augment_rank") or 0) != int(item.get("augment_rank") or 0):
                continue
            if str(record.get("augment_path") or "") != str(item.get("augment_path") or ""):
                continue
            if int(record.get("augment_trial") or 0) != int(item.get("augment_trial") or 0):
                continue
            if sorted(map(str, record.get("augments") or [])) != sorted(map(str, item.get("augments") or [])):
                continue
            record = _exact_rank_augment(
                _with_unverified_warning(_with_curated_model(record))
            )
            record = _with_ffxiah_model(record)
            return _with_builtin_model(
                record,
                _gear_record(record, eligible=False, hoxne_mastery_rank=self.hoxne_mastery_rank),
            )
        # Schema v2 profile records carry the exact resolved stats and LAC
        # augment identity even when that item is no longer in accessible
        # inventory.  Prefer that record over a same-name curated fallback.
        if item.get("stats") or item.get("base_stats") or item.get("augment_stats"):
            embedded = deepcopy(item)
            embedded.setdefault("key", str(item.get("key") or f"profile|{name.casefold()}"))
            embedded.setdefault("name", name)
            embedded.setdefault("model_complete", bool(item.get("stats")))
            return _with_builtin_model(
                embedded,
                _gear_record(
                    embedded, eligible=False,
                    hoxne_mastery_rank=self.hoxne_mastery_rank,
                ),
            )
        # Profile-only gear can still use the curated WSDist model.
        for candidate in gear_pyfile.all_gear.values():
            if str(candidate.get("Name") or "").lower() == name.lower():
                return _apply_hoxne_mastery_rank(
                    deepcopy(candidate), self.hoxne_mastery_rank
                )
        return None

    def profile_records(self) -> list[dict]:
        return list(self.data.get("profiles", []))

    def profile_path(self, job: str) -> Path:
        character = self.data.get("character", {}).get("key", "")
        safe = Path(str(character)).name
        path = self.ashita_root / "config" / "addons" / "LuAshitacast" / safe / f"{job.upper()}.lua"
        resolved = path.resolve()
        allowed = (self.ashita_root / "config" / "addons" / "LuAshitacast" / safe).resolve()
        if allowed not in resolved.parents:
            raise ValueError("Profile path is outside the selected character directory.")
        return resolved

    def bridge_mtime(self) -> float:
        return self.loaded_mtime
