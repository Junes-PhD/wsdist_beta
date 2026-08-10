"""Character-scoped bridge and gear catalog for GearSetBuilder exports."""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path

import gear as gear_pyfile

try:
    from ffxiah_models import FFXIAH_MODELS
except ImportError:
    FFXIAH_MODELS = {}


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
BASE_PARAMETERS = ("STR", "DEX", "VIT", "AGI", "INT", "MND", "CHR")


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
    _canonicalize_item_stats(item_id, stats)
    result = deepcopy(stats)
    name = str(record.get("name") or "Unknown")
    name2 = _model_name(record)
    accessible_count = _accessible_count(record)
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
        "Eligible": bool(eligible and record.get("model_complete", False) and accessible_count > 0),
        "LAC": deepcopy(record.get("lac") or {"Name": name}),
        "Slots": slots,
        "Augments": deepcopy(record.get("augments") or []),
    })
    resource_flags = int(record.get("resource_flags") or record.get("Resource Flags") or 0)
    exclusive = bool(record.get("exclusive", record.get("Exclusive", False)))
    transferable = record.get("transferable", record.get("Transferable"))
    if transferable is None:
        # The SDK's no-delivery/no-trade bits are the conservative Ex test;
        # unknown legacy records remain non-transferable for cross-character
        # sharing, while the owning character can still use them normally.
        transferable = False
    result.update({
        "Resource Flags": resource_flags,
        "Exclusive": exclusive or bool(resource_flags & 0x6000),
        "Transferable": bool(transferable) and not bool(resource_flags & 0x6000),
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


def _with_builtin_model(record: dict, item: dict) -> dict:
    """Overlay the curated WSDist model without counting bridge stats twice."""
    item_id = int(record.get("item_id") or 0)
    # For these items the built-in rows are explicitly R30 models.  A
    # character-scoped bridge is authoritative for the base/current variant;
    # never overlay a max-rank row onto it.  This also repairs older bridge
    # files that were published before the base table was added.
    if item_id in BASE_ITEM_MODELS and record.get("model_complete"):
        return item
    name = str(record.get("name") or "").lower()
    candidates = [value for value in gear_pyfile.all_gear.values()
                  if str(value.get("Name") or "").lower() == name]
    rank = record.get("augment_rank")
    if rank is not None:
        ranked = [value for value in candidates if int(value.get("Rank", -1) or -1) == int(rank)]
        if len(ranked) == 1:
            candidates = ranked
        elif ranked:
            augment_text = " ".join(str(value).lower() for value in record.get("augments") or [])
            named = [value for value in ranked if augment_text and augment_text in str(value.get("Name2", "")).lower()]
            candidates = named if len(named) == 1 else []
    else:
        exact = [value for value in candidates if str(value.get("Name2") or "").lower() == name]
        candidates = exact or candidates
    if len(candidates) != 1:
        return item
    base = deepcopy(candidates[0])
    bridge_stats = item.copy()
    # Preserve the stable character identity and actual resource restrictions.
    base.update({key: value for key, value in bridge_stats.items()
                 if key not in {"Name", "Name2", "Type", "Skill Type", "Jobs", "Bridge Key",
                                "Accessible Count", "Total Count", "Model Complete", "Eligible",
                                "LAC", "Slots", "Augments"}})
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
