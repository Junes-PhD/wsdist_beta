"""Responsive PyQt6 interface for WSDist.

The permanent widget tree stays small. Large gear lists exist only while a
picker is open, avoiding the window-drag repaint cost of the legacy Tk UI.
The calculation and optimizer modules are reused without formula changes.
"""

from __future__ import annotations

import ast
import csv
import copy
import difflib
from html import escape
import json
import multiprocessing
import os
import re
import sys
import threading
import time
import zipfile
from pathlib import Path

from PyQt6.QtCore import QSettings, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFontMetrics, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QInputDialog, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QDoubleSpinBox, QSpinBox, QSplitter,
    QStatusBar, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

import actions
import buffs as buff_data
import create_player
import enemies
import fancy_plot
import gear
import wsdist
from wsdist_bridge import BridgeStore
from lac_profile import (
    prepare_managed_update, prepare_set_renames, write_managed_sets,
    write_reload_request, write_renamed_profile,
)


APP_DIR = Path(__file__).resolve().parent
SLOTS = (
    "main", "sub", "ranged", "ammo", "head", "neck", "ear1", "ear2",
    "body", "hands", "ring1", "ring2", "back", "waist", "legs", "feet",
)
OPTIMIZER_RUN_COLORS = (
    "#1769aa", "#8e44ad", "#00897b", "#d35400", "#c2185b",
    "#5d4037", "#546e7a", "#6d4c41", "#00796b", "#7b1fa2",
)
ARMOR_SLOTS = (
    "head", "neck", "ear1", "ear2", "body", "hands",
    "ring1", "ring2", "back", "waist", "legs", "feet",
)
JOBS = {
    "Bard": "brd", "Beastmaster": "bst", "Black Mage": "blm",
    "Blue Mage": "blu", "Corsair": "cor", "Dancer": "dnc",
    "Dark Knight": "drk", "Dragoon": "drg", "Geomancer": "geo",
    "Monk": "mnk", "Ninja": "nin", "Paladin": "pld",
    "Puppetmaster": "pup", "Ranger": "rng", "Red Mage": "rdm",
    "Rune Fencer": "run", "Samurai": "sam", "Scholar": "sch",
    "Summoner": "smn", "Thief": "thf", "Warrior": "war",
    "White Mage": "whm",
}

# The calculation engine consumes these exact ability names.  Keeping the
# small amount of presentation metadata here lets Quick Look expose the
# abilities that are valid for the selected main/sub job without changing
# any of the underlying formulas.
JOB_ABILITY_DEFINITIONS = (
    ("Aggressor", 45, ("war",), "Accuracy and attack support."),
    ("Barrage", 30, ("rng",), "Ranger multi-shot ability."),
    ("Berserk", 15, ("war",), "Attack boost with defense tradeoff."),
    ("Blood Rage", 87, ("*",), "Assumed party Warrior critical-rate buff."),
    ("Building Flourish", 50, ("dnc",), "Dancer flourish attack/accuracy boost."),
    ("Chainspell", 1, ("rdm",), "Red Mage two-hour ability."),
    ("Climactic Flourish", 80, ("dnc",), "Critical-hit flourish."),
    ("Closed Position", 99, ("dnc",), "Dancer stance and Store TP bonus."),
    ("Composure", 99, ("rdm",), "Red Mage accuracy and enspell support."),
    ("Conspirator", 40, ("thf",), "Thief enmity-list accuracy support."),
    ("Crimson Howl", 1, ("*",), "Assumed party attack buff."),
    ("Crystal Blessing", 1, ("*",), "Assumed party TP bonus."),
    ("Divine Emblem", 78, ("pld",), "Paladin magic-damage boost."),
    ("Double Shot", 79, ("rng",), "Ranger ranged multi-shot ability."),
    ("Ebullience", 55, ("sch",), "Scholar magic-damage boost."),
    ("Enlight II", 99, ("pld",), "Paladin enspell accuracy support."),
    ("Enlightenment", 99, ("sch",), "Scholar attribute boost."),
    ("Endark II", 99, ("drk",), "Dark Knight enspell support."),
    ("EnSpell", 27, ("rdm",), "Red Mage enspell mode."),
    ("Focus", 25, ("mnk",), "Monk accuracy and critical-rate boost."),
    ("Footwork", 65, ("mnk",), "Monk kick-attack stance."),
    ("Frenzied Rage", 99, ("bst",), "Beastmaster attack boost."),
    ("Futae", 99, ("nin",), "Ninja ninjutsu damage boost."),
    ("Hasso", 25, ("sam",), "Samurai two-handed haste stance."),
    ("Haste Samba", 1, ("dnc",), "Dancer haste samba."),
    ("Haste Samba (sub)", 35, ("dnc",), "Dancer subjob haste samba."),
    ("Hover Shot", 99, ("rng",), "Ranger ranged accuracy/damage stance."),
    ("Ifrit's Favor", 1, ("*",), "Assumed party Double Attack favor."),
    ("Impetus", 88, ("mnk",), "Monk attack and critical-rate stance."),
    ("Innin", 99, ("nin",), "Ninja front-facing attack stance."),
    ("Klimaform", 46, ("sch",), "Scholar weather magic support."),
    ("Last Resort", 15, ("drk",), "Dark Knight attack and haste stance."),
    ("Manafont", 1, ("blm",), "Black Mage magic-damage ability."),
    ("Manawell", 1, ("blm",), "Black Mage magic-damage ability."),
    ("Mighty Guard", 1, ("*",), "Assumed party haste/defense support."),
    ("Mighty Strikes", 1, ("war",), "Warrior guaranteed-critical ability."),
    ("Nature's Meditation", 1, ("*",), "Assumed party attack buff."),
    ("Overwhelm", 99, ("sam",), "Samurai weapon-skill damage trait."),
    ("Rage", 99, ("bst",), "Beastmaster attack boost."),
    ("Ramuh's Favor", 1, ("*",), "Assumed party critical-rate favor."),
    ("Saber Dance", 99, ("dnc",), "Dancer Double Attack stance."),
    ("Sange", 99, ("nin",), "Ninja shuriken stance."),
    ("Sharpshot", 1, ("rng",), "Ranged accuracy support."),
    ("Shiva's Favor", 1, ("*",), "Assumed party magic-attack favor."),
    ("Sneak Attack", 15, ("thf",), "Thief guaranteed-critical WS modifier."),
    ("Striking Flourish", 89, ("dnc",), "Dancer flourish WS modifier."),
    ("Swordplay", 20, ("run",), "Rune Fencer accuracy/evasion stance."),
    ("Temper", 99, ("run",), "Rune Fencer Double Attack support."),
    ("Temper II", 99, ("rdm",), "Red Mage Triple Attack support."),
    ("Ternary Flourish", 93, ("dnc",), "Dancer flourish WS modifier."),
    ("Theurgic Focus", 80, ("geo",), "Geomancer magic-attack support."),
    ("Trick Attack", 30, ("thf",), "Thief guaranteed-critical WS modifier."),
    ("Triple Shot", 87, ("cor",), "Corsair ranged multi-shot ability."),
    ("True Shot", 1, ("rng", "cor"), "Ranged accuracy/damage support."),
    ("Velocity Shot", 99, ("rng",), "Ranger ranged attack stance."),
    ("Warcry", 1, ("*",), "Assumed party attack and TP bonus."),
    ("Warcry (sub)", 35, ("war",), "Warrior subjob attack support."),
    ("Magic Burst", 1, ("*",), "Enable magic-burst calculations."),
)


def _legacy_literal(attribute: str, fallback):
    """Read data declarations from the legacy UI without importing Tk."""
    try:
        source = (APP_DIR / "gui_main.py").read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Attribute) and target.attr == attribute
                for target in node.targets
            ):
                return ast.literal_eval(node.value)
    except (OSError, SyntaxError, ValueError):
        pass
    return fallback


WS_BY_SKILL = _legacy_literal("ws_dict", {"None": ["None"]})
SPELLS_BY_JOB = _legacy_literal("spells_dict", {})
ALL_WS_NAMES = sorted(
    {name for names in WS_BY_SKILL.values() for name in names if name != "None"},
    key=len,
    reverse=True,
)
WS_SET_ALIASES = {
    "aedge": "Aeolian Edge",
    "laststand": "Last Stand", "trueflight": "Trueflight",
    "hotshot": "Hot Shot", "evis": "Evisceration",
    "dimi": "Dimidiation", "reso": "Resolution",
    "ukko": "Ukko's Fury", "chant": "Chant du Cygne",
    "hi": "Blade: Hi", "shun": "Blade: Shun", "metsu": "Blade: Metsu",
    "jinpu": "Tachi: Jinpu", "ageha": "Tachi: Ageha",
    "hf": "Howling Fist", "victory": "Victory Smite",
}
PROFILE_SLOT_MAP = {
    "main": "main", "sub": "sub", "range": "ranged", "ranged": "ranged",
    "ammo": "ammo", "head": "head", "body": "body", "hands": "hands",
    "legs": "legs", "feet": "feet", "neck": "neck", "waist": "waist",
    "ear1": "ear1", "ear2": "ear2", "ring1": "ring1", "ring2": "ring2",
    "back": "back",
}
WEAPON_SLOTS = ("main", "sub", "ranged", "ammo")
MAIN_WEAPON_SLOTS = ("main", "sub")
RANGED_WEAPON_SLOTS = ("ranged", "ammo")
ITEM_LEVEL_FILTER_SLOTS = ("main", "head", "body", "hands", "legs", "feet")
SUBSTAT_OPTIONS = (
    "None", "Magic Evasion", "Evasion", "Defense", "Magic Defense",
    "Subtle Blow", "Counter", "Store TP", "Accuracy", "Magic Accuracy",
    "Attack", "Magic Attack", "HP", "MP", "Enmity",
)


def _optimizer_check_gear(candidates: dict[str, set[str]], items_by_slot: dict[str, list[dict]], quick_gear: dict[str, dict]):
    """Build optimizer candidates without reintroducing unselected weapons."""
    check_gear = {}
    empty_weapon_slots = []
    for slot in SLOTS:
        lookup = {item_name(item): item for item in items_by_slot.get(slot, ())}
        selected = candidates.get(slot, set())
        check_gear[slot] = [lookup[name] for name in selected if name in lookup]
        if check_gear[slot]:
            continue
        if slot in WEAPON_SLOTS:
            empty_weapon_slots.append(slot)
        else:
            check_gear[slot] = [quick_gear[slot]]
    return check_gear, empty_weapon_slots


def _ranking_weapon_types(gearset: dict[str, dict]) -> list[str]:
    """Return modeled skill types from the selected melee/ranged weapons."""
    values = []
    for slot in ("main", "ranged", "ammo"):
        skill = str(gearset.get(slot, {}).get("Skill Type") or "None")
        if skill != "None" and skill in WS_BY_SKILL and skill not in values:
            values.append(skill)
    return values


def _lock_ranking_weapon_slots(check_gear: dict[str, list[dict]],
                               selected_gear: dict[str, dict]) -> dict[str, list[dict]]:
    """Freeze the current weapon setup so every ranked WS is comparable."""
    locked = {slot: list(items) for slot, items in check_gear.items()}
    for slot in WEAPON_SLOTS:
        locked[slot] = [selected_gear.get(slot, gear.Empty)]
    return locked


def _profile_ws_name(set_name: str) -> str | None:
    lowered = set_name.casefold()
    compact = re.sub(r"[^a-z0-9]+", "", lowered)
    matched = next(
        (name for name in ALL_WS_NAMES
         if re.sub(r"[^a-z0-9]+", "", name.casefold()) in compact),
        None,
    )
    if matched:
        return matched
    tokens = set(re.split(r"[^a-z0-9]+", lowered))
    for alias, name in WS_SET_ALIASES.items():
        if alias in tokens:
            return name
    # Many profiles shorten a WS set to its distinctive first word, such as
    # Savage_Default or Leaden_Acc. Use that only when it identifies one WS.
    candidates = {
        name for name in ALL_WS_NAMES
        if name.casefold().split()[0] in tokens
    }
    return next(iter(candidates)) if len(candidates) == 1 else None


def _profile_category(set_name: str) -> str | None:
    tokens = set(re.split(r"[^a-z0-9]+", set_name.casefold()))
    if tokens & {"tp", "tpmelee", "tpranged"}:
        return "TP"
    if tokens & {"dt", "pdt", "mdt", "damage", "taken"}:
        return "DT"
    return None


PROFILE_VARIANTS = {"default": "Default", "acc": "Acc", "hybrid": "Hybrid", "wsdist": "WSDist"}


def _profile_set_descriptor(set_name: str, metadata: dict | None = None) -> dict:
    """Classify one raw LAC set without treating partial layers as full sets."""
    supplied = metadata if isinstance(metadata, dict) else {}
    pieces = [piece for piece in re.split(r"_+", set_name) if piece]
    lowered = [piece.casefold() for piece in pieces]
    variant_index = next((i for i, value in enumerate(lowered) if value in PROFILE_VARIANTS), None)
    variant = PROFILE_VARIANTS.get(lowered[variant_index], "Default") if variant_index is not None else "Default"
    family = "_".join(pieces[:variant_index]) if variant_index is not None else (pieces[0] if pieces else set_name)
    modifiers = pieces[variant_index + 1:] if variant_index is not None else pieces[1:]
    first = lowered[0] if lowered else ""
    ws_name = str(supplied.get("ws_name") or "") or _profile_ws_name(set_name)
    role = str(supplied.get("role") or "")
    if not role:
        if first in {"weapon", "gun", "shield", "range", "ranged"}:
            role = "weapon"
        elif first in {"tp", "tpmelee", "tpranged"}:
            role = "tp"
        elif first == "ws":
            role = "ws_base"
        elif first in {"dt", "pdt", "mdt"}:
            role = "defense"
        elif first == "idle":
            role = "idle"
        elif ws_name:
            role = "ws"
        else:
            role = "other"
    return {
        "role": role,
        "family": str(supplied.get("family") or family),
        "variant": str(supplied.get("variant") or variant),
        "modifiers": list(supplied.get("modifiers") or modifiers),
        "ws_name": ws_name or None,
    }


def _merge_profile_layers(label: str, layers: list[dict], *, role: str,
                          ws_name: str | None = None) -> dict:
    gearset = {slot: gear.Empty for slot in SLOTS}
    specified, missing, incomplete, ineligible = set(), [], [], []
    for layer in layers:
        for slot in layer.get("specified_slots", set()):
            gearset[slot] = layer["gearset"][slot]
            specified.add(slot)
        missing.extend(f"{layer['name']}.{slot}" for slot in layer.get("missing", ()))
        incomplete.extend(f"{layer['name']}.{slot}" for slot in layer.get("incomplete", ()))
        ineligible.extend(f"{layer['name']}.{slot}" for slot in layer.get("ineligible", ()))
    descriptor = dict(layers[-1]["descriptor"])
    descriptor["role"] = role
    return {
        "name": label,
        "category": "TP" if role == "tp" else None,
        "ws_name": ws_name,
        "gearset": gearset,
        "specified_slots": specified,
        "missing": sorted(set(missing)),
        "incomplete": sorted(set(incomplete)),
        "ineligible": sorted(set(ineligible)),
        "layers": [layer["name"] for layer in layers],
        "descriptor": descriptor,
    }


def _compose_profile_payloads(raw_payloads: list[dict], defense: dict | None = None) -> list[dict]:
    """Compose the common LAC TP/WS layer order into effective configurations."""
    by_role = {}
    for payload in raw_payloads:
        descriptor = payload["descriptor"]
        by_role.setdefault(descriptor["role"], []).append(payload)
    tp_sets = by_role.get("tp", [])
    ws_bases = by_role.get("ws_base", [])
    ws_sets = [item for item in by_role.get("ws", []) if item["descriptor"].get("ws_name")]
    tp_default = next((item for item in tp_sets if item["descriptor"]["variant"] == "Default" and not item["descriptor"]["modifiers"]), None)
    ws_default = next((item for item in ws_bases if item["descriptor"]["variant"] == "Default"), None)
    effective = []
    for payload in tp_sets:
        layers = []
        if tp_default is not None and payload is not tp_default:
            layers.append(tp_default)
        layers.append(payload)
        if defense is not None:
            layers.append(defense)
        effective.append(_merge_profile_layers(payload["name"], layers, role="tp"))
    for payload in ws_sets:
        descriptor = payload["descriptor"]
        layers = []
        if ws_default is not None:
            layers.append(ws_default)
        if descriptor["variant"] != "Default":
            generic_variant = next((item for item in ws_bases if item["descriptor"]["variant"] == descriptor["variant"]), None)
            if generic_variant is not None:
                layers.append(generic_variant)
        family_default = next((item for item in ws_sets
                               if item["descriptor"]["family"].casefold() == descriptor["family"].casefold()
                               and item["descriptor"]["variant"] == "Default"), None)
        if family_default is not None and family_default is not payload:
            layers.append(family_default)
        layers.append(payload)
        effective.append(_merge_profile_layers(
            payload["name"], layers, role="ws", ws_name=descriptor["ws_name"]
        ))
    return effective


def _with_weapon_overlays(payload: dict, main_weapon: dict | None,
                          ranged_weapon: dict | None) -> dict:
    """Apply explicitly-selected weapon-set slots over a profile armor set.

    LuAshitacast profiles commonly equip a TP/WS armor set and then a separate
    main/sub or ranged/ammo set.  Only slots explicitly named by those sets are
    overlaid, so missing slots remain the armor set's values.
    """
    combined = dict(payload["gearset"])
    for overlay, allowed_slots in ((main_weapon, MAIN_WEAPON_SLOTS),
                                   (ranged_weapon, RANGED_WEAPON_SLOTS)):
        if not overlay:
            continue
        specified = overlay.get("specified_slots", set())
        for slot in allowed_slots:
            if slot in specified:
                combined[slot] = overlay["gearset"][slot]
    result = dict(payload)
    result["gearset"] = combined
    labels = [entry["name"] for entry in (main_weapon, ranged_weapon) if entry]
    provenance = list(payload.get("layers") or [payload.get("name", "As listed")])
    provenance.extend(labels)
    result["weapon_setup"] = " â†’ ".join(provenance)
    return result


def _base_equipment() -> dict[str, list[dict]]:
    return {
        "main": gear.mains, "sub": gear.subs + gear.grips,
        "ranged": gear.ranged, "ammo": gear.ammos, "head": gear.heads,
        "neck": gear.necks, "ear1": gear.ears, "ear2": gear.ears2,
        "body": gear.bodies, "hands": gear.hands, "ring1": gear.rings,
        "ring2": gear.rings2, "back": gear.capes, "waist": gear.waists,
        "legs": gear.legs, "feet": gear.feet,
    }


def item_name(item: dict) -> str:
    return str(item.get("Name2") or item.get("Name") or "Empty")


def _normalized_item_name(value) -> str:
    return str(value or "").strip().casefold()


def _blacklist_matches(item: dict, blacklist: set[str]) -> bool:
    if not blacklist or item_name(item) == "Empty":
        return False
    names = {
        _normalized_item_name(item.get("Name")),
        _normalized_item_name(item.get("Name2")),
    }
    return bool(names & blacklist)


def item_tooltip(item: dict) -> str:
    ignored = {"Name", "Name2", "Jobs", "Slots", "Bridge Key", "Eligible"}
    lines = [str(item.get("Name") or item_name(item))]
    for key, value in item.items():
        if key not in ignored and value not in (None, "", 0, False, [], {}):
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


class NumericTableWidgetItem(QTableWidgetItem):
    def __init__(self, text: str, value=None):
        super().__init__(text)
        self.numeric_value = None if value in (None, "") else float(value)

    def __lt__(self, other):
        if isinstance(other, NumericTableWidgetItem):
            if self.numeric_value is None:
                return other.numeric_value is not None
            if other.numeric_value is None:
                return False
            return self.numeric_value < other.numeric_value
        return super().__lt__(other)


class GearIconProvider:
    """Resolve and cache item icons without adding permanent image widgets."""

    def __init__(self):
        self._item_ids: dict[str, int] = {}
        self._icons: dict[tuple[int, tuple[str, ...]], QIcon] = {}
        self._bridge_icon_dir: Path | None = None
        self._icon_archive: zipfile.ZipFile | None = None
        self._icon_archive_names: set[str] | None = None
        try:
            with (APP_DIR / "item_list.csv").open(encoding="utf-8", newline="") as stream:
                for row in csv.DictReader(stream, delimiter=";"):
                    item_id = int(row.get("id") or 0)
                    for key in (row.get("name"), row.get("name2")):
                        if key:
                            self._item_ids[str(key).casefold()] = item_id
        except (OSError, ValueError):
            pass

    def set_bridge_icon_dir(self, directory: Path | None):
        directory = directory.resolve() if directory and directory.exists() else None
        if directory != self._bridge_icon_dir:
            self._bridge_icon_dir = directory
            self._icons.clear()

    def item_id(self, item: dict) -> int:
        direct = int(item.get("Item ID") or item.get("item_id") or 0)
        if direct:
            return direct
        for key in (item.get("Name"), item.get("Name2")):
            item_id = self._item_ids.get(str(key or "").casefold())
            if item_id:
                return item_id
        return 0

    def _roots(self) -> tuple[Path, ...]:
        roots = []
        if self._bridge_icon_dir is not None:
            roots.append(self._bridge_icon_dir)
        configured = os.environ.get("WSDIST_EQUIPVIEWER_ICONS")
        if configured:
            roots.append(Path(configured))
        roots.extend((
            APP_DIR / "icons32",
            APP_DIR / "equipviewer" / "icons",
            APP_DIR / "icons",
            APP_DIR.parents[2] / "Windower" / "Lua" / "addons" / "equipviewer" / "icons",
        ))
        return tuple(dict.fromkeys(root.resolve() for root in roots if root.exists()))

    def plot_icon_sources(self) -> tuple[Path, ...]:
        """Return icon sources for non-Qt plots, including the bundled ZIP."""
        return self._roots() + (APP_DIR / "icons32.zip",)

    def _archive_icon(self, item_id: int) -> QIcon:
        archive_path = APP_DIR / "icons32.zip"
        if not archive_path.is_file():
            return QIcon()
        try:
            if self._icon_archive is None:
                self._icon_archive = zipfile.ZipFile(archive_path)
                self._icon_archive_names = set(self._icon_archive.namelist())
            for extension in ("png", "bmp", "ico"):
                member = f"{item_id}.{extension}"
                if member not in (self._icon_archive_names or set()):
                    continue
                pixmap = QPixmap()
                if pixmap.loadFromData(self._icon_archive.read(member)):
                    return QIcon(pixmap)
        except (OSError, KeyError, RuntimeError, zipfile.BadZipFile):
            self._icon_archive = None
            self._icon_archive_names = None
        return QIcon()

    def icon(self, item: dict) -> QIcon:
        item_id = self.item_id(item)
        if not item_id:
            return QIcon()
        roots = self._roots()
        cache_key = (item_id, tuple(str(root) for root in roots))
        cached = self._icons.get(cache_key)
        if cached is not None:
            return cached
        icon = QIcon()
        for root in roots:
            for extension in ("png", "bmp", "ico"):
                path = root / f"{item_id}.{extension}"
                if path.is_file():
                    icon = QIcon(str(path))
                    break
            if not icon.isNull():
                break
        if icon.isNull():
            icon = self._archive_icon(item_id)
        self._icons[cache_key] = icon
        return icon


class GearPicker(QDialog):
    def __init__(self, slot: str, items: list[dict], selected: dict,
                 icons: GearIconProvider, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Choose {slot.title()}")
        self.resize(560, 620)
        self._items = sorted(items, key=lambda value: item_name(value).lower())
        self.selected_item = selected
        self.icons = icons
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by item name or stats...")
        self.list = QListWidget()
        self.list.setUniformItemSizes(True)
        self.list.setIconSize(QSize(32, 32))
        layout.addWidget(self.search)
        layout.addWidget(self.list, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self._populate)
        self.list.itemDoubleClicked.connect(lambda _item: self._accept_selection())
        self._populate()

    def _populate(self):
        query = self.search.text().strip().lower()
        self.list.clear()
        selected_row = -1
        for item in self._items:
            if query and query not in item_tooltip(item).lower():
                continue
            row = QListWidgetItem(item_name(item))
            row.setIcon(self.icons.icon(item))
            row.setData(Qt.ItemDataRole.UserRole, item)
            row.setToolTip(item_tooltip(item))
            self.list.addItem(row)
            if item_name(item) == item_name(self.selected_item):
                selected_row = self.list.count() - 1
        if selected_row >= 0:
            self.list.setCurrentRow(selected_row)

    def _accept_selection(self):
        current = self.list.currentItem()
        if current is not None:
            self.selected_item = current.data(Qt.ItemDataRole.UserRole)
            self.accept()


class CandidatePicker(QDialog):
    def __init__(self, slot: str, items: list[dict], selected: set[str],
                 icons: GearIconProvider, locked_name: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Optimizer candidates: {slot.title()}")
        self.resize(600, 680)
        self._items = sorted(items, key=lambda value: item_name(value).lower())
        self.selected_names = set(selected)
        self.locked_name = str(locked_name or "")
        self.icons = icons
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter candidates...")
        self.list = QListWidget()
        self.list.setUniformItemSizes(True)
        self.list.setIconSize(QSize(32, 32))
        layout.addWidget(self.search)
        lock_row = QHBoxLayout()
        lock_row.addWidget(QLabel("Lock this slot"))
        self.lock_combo = QComboBox()
        self.lock_combo.addItem("No lock", "")
        for item in self._items:
            self.lock_combo.addItem(item_name(item), item_name(item))
        lock_index = self.lock_combo.findData(self.locked_name)
        self.lock_combo.setCurrentIndex(max(0, lock_index))
        self.lock_combo.setToolTip(
            "The selected item is forced in this slot when the optimizer runs."
        )
        lock_row.addWidget(self.lock_combo, 1)
        layout.addLayout(lock_row)
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        select_all = QPushButton("Select visible")
        clear = QPushButton("Clear visible")
        select_all.clicked.connect(lambda: self._check_visible(True))
        clear.clicked.connect(lambda: self._check_visible(False))
        row.addWidget(select_all)
        row.addWidget(clear)
        row.addStretch(1)
        layout.addLayout(row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self._populate)
        self._populate()

    def _remember_visible(self):
        for index in range(self.list.count()):
            row = self.list.item(index)
            name = row.data(Qt.ItemDataRole.UserRole)
            if row.checkState() == Qt.CheckState.Checked:
                self.selected_names.add(name)
            else:
                self.selected_names.discard(name)

    def _populate(self):
        self._remember_visible()
        query = self.search.text().strip().lower()
        self.list.clear()
        for item in self._items:
            if query and query not in item_tooltip(item).lower():
                continue
            name = item_name(item)
            row = QListWidgetItem(name)
            row.setIcon(self.icons.icon(item))
            row.setData(Qt.ItemDataRole.UserRole, name)
            row.setToolTip(item_tooltip(item))
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(
                Qt.CheckState.Checked if name in self.selected_names
                else Qt.CheckState.Unchecked
            )
            self.list.addItem(row)

    def _check_visible(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for index in range(self.list.count()):
            self.list.item(index).setCheckState(state)

    def _accept_selection(self):
        self._remember_visible()
        self.locked_name = str(self.lock_combo.currentData() or "")
        self.accept()


class GearSetEditor(QWidget):
    changed = pyqtSignal()

    def __init__(self, title: str, owner: "MainWindow"):
        super().__init__()
        self.owner = owner
        self.items = {slot: gear.Empty for slot in SLOTS}
        self.buttons: dict[str, QPushButton] = {}
        layout = QVBoxLayout(self)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        grid = QGridLayout()
        for index, slot in enumerate(SLOTS):
            button = QPushButton("Empty")
            button.setMinimumHeight(42)
            button.setIconSize(QSize(32, 32))
            button.clicked.connect(lambda _checked=False, name=slot: self.choose(name))
            row, column = divmod(index, 4)
            cell = QVBoxLayout()
            label = QLabel(slot.upper())
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.addWidget(label)
            cell.addWidget(button)
            grid.addLayout(cell, row, column)
            self.buttons[slot] = button
        layout.addLayout(grid)
        layout.addStretch(1)

    def choose(self, slot: str):
        dialog = GearPicker(
            slot, self.owner.items_for_slot(slot), self.items[slot], self.owner.icons, self
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_item(slot, dialog.selected_item)

    def set_item(self, slot: str, item: dict, *, emit: bool = True):
        self.items[slot] = item
        self.buttons[slot].setText(item_name(item))
        self.buttons[slot].setIcon(self.owner.icons.icon(item))
        self.buttons[slot].setToolTip(item_tooltip(item))
        if emit:
            self.changed.emit()

    def refresh_icons(self):
        for slot, item in self.items.items():
            self.buttons[slot].setIcon(self.owner.icons.icon(item))

    def set_gearset(self, gearset: dict):
        for slot in SLOTS:
            self.set_item(slot, gearset.get(slot, gear.Empty), emit=False)
        self.changed.emit()


class OptimizeThread(QThread):
    progress = pyqtSignal(str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    stopped = pyqtSignal(object)

    def __init__(self, args: tuple, kwargs: dict, parent=None, *, target=None):
        super().__init__(parent)
        self.args = args
        self.kwargs = kwargs
        self.target = target or wsdist.optimize_set
        self._stop_requested = threading.Event()
        self._shared_stop_event = None
        self._manager = None

    def request_stop(self):
        self._stop_requested.set()
        if self._shared_stop_event is not None:
            self._shared_stop_event.set()

    def run(self):
        manager = None
        try:
            kwargs = dict(self.kwargs)
            kwargs["progress_callback"] = self.progress.emit
            # A Manager event is usable by both the QThread and restart
            # processes on Windows.  The optimizer checks it cooperatively at
            # iteration and candidate boundaries, so no worker is terminated
            # mid-calculation.
            manager = multiprocessing.Manager()
            self._manager = manager
            self._shared_stop_event = manager.Event()
            if self._stop_requested.is_set():
                self._shared_stop_event.set()
            kwargs["stop_event"] = self._shared_stop_event
            kwargs["progress_queue"] = manager.Queue()
            self.succeeded.emit(self.target(*self.args, **kwargs))
        except wsdist.OptimizerStopped as error:
            self.stopped.emit({"message": str(error), "top_results": error.results})
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            self._shared_stop_event = None
            self._manager = None
            if manager is not None:
                manager.shutdown()


class PlotThread(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, player, enemy, ws_name: str, tp_value: int, ws_type: str, samples=20000, parent=None):
        super().__init__(parent)
        self.player = player
        self.enemy = enemy
        self.ws_name = ws_name
        self.tp_value = tp_value
        self.ws_type = ws_type
        self.samples = samples
        self._stop_requested = threading.Event()

    def request_stop(self):
        self._stop_requested.set()

    def run(self):
        try:
            damage = []
            for _ in range(self.samples):
                if self._stop_requested.is_set():
                    return
                output = actions.average_ws(
                    self.player, self.enemy, self.ws_name, self.tp_value,
                    self.ws_type, "Damage dealt", simulation=True,
                    single=True, verbose=False,
                )
                damage.append(output[0])
            if self._stop_requested.is_set():
                return
            self.completed.emit(damage)
        except Exception as error:
            self.failed.emit(str(error))


class TopSetsDialog(QDialog):
    """Compare completed results and load a selected result into the GUI."""

    def __init__(self, results: list[dict], icons: GearIconProvider, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Best optimizer sets")
        self.resize(1050, 760)
        self.icons = icons
        self.results = list(results[:5])
        layout = QVBoxLayout(self)
        note = QLabel(
            "Combined TP + WS results show the TP and WS sets side by side. "
            "Other optimizer modes show one full equipment set."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Load set"))
        self.load_combo = QComboBox()
        for result in self.results:
            label = result.get("label") or f"Set {result.get('rank', '?')}"
            self.load_combo.addItem(str(label))
        controls.addWidget(self.load_combo)
        load_quick = QPushButton("Load into Quick Look")
        load_tpws = QPushButton("Load into TP / WS Sets")
        load_quick.setEnabled(parent is not None)
        load_tpws.setEnabled(parent is not None)
        if parent is not None:
            load_quick.clicked.connect(lambda: self._load_selected("quick"))
            load_tpws.clicked.connect(lambda: self._load_selected("tpws"))
        controls.addWidget(load_quick)
        controls.addWidget(load_tpws)
        controls.addStretch(1)
        layout.addLayout(controls)
        substat_results = [result for result in self.results if result.get("substats")]
        if substat_results:
            baseline = next(
                (result for result in substat_results
                 if result.get("label") == "Best damage set"),
                substat_results[0],
            )
            targets = []
            for result in substat_results:
                for target in result.get("substat_targets", result.get("substats", {})):
                    if target not in targets:
                        targets.append(target)
            comparison = QGroupBox("Secondary-stat comparison")
            comparison_layout = QVBoxLayout(comparison)
            comparison_note = QLabel(
                "Values are compared using the same modeled player stats as each set. "
                "Delta is relative to the best-damage set."
            )
            comparison_note.setWordWrap(True)
            comparison_layout.addWidget(comparison_note)
            table = QTableWidget(len(targets), 3)
            table.setHorizontalHeaderLabels([
                "Secondary stat", "Best damage set", "Sub-stat optimized / delta"
            ])
            table.verticalHeader().setVisible(False)
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            optimized = next(
                (result for result in substat_results
                 if result.get("label") == "Sub-stat optimized"),
                substat_results[-1],
            )
            baseline_values = baseline.get("substats", {})
            optimized_values = optimized.get("substats", {})
            for row, target in enumerate(targets):
                base_value = float(baseline_values.get(target, 0.0))
                optimized_value = float(optimized_values.get(target, 0.0))
                table.setItem(row, 0, QTableWidgetItem(str(target)))
                table.setItem(row, 1, QTableWidgetItem(f"{base_value:,.1f}"))
                table.setItem(
                    row, 2,
                    QTableWidgetItem(
                        f"{optimized_value:,.1f}  ({optimized_value - base_value:+,.1f})"
                    ),
                )
            table.resizeColumnsToContents()
            comparison_layout.addWidget(table)
            layout.addWidget(comparison)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        top_metric = max((float(result.get("metric") or 0) for result in self.results), default=0)
        for result in self.results:
            metric = float(result.get("metric") or 0)
            worse = 0.0 if top_metric <= 0 else max(0.0, (top_metric - metric) / abs(top_metric) * 100)
            label = result.get("label") or f"Set {result.get('rank', '?')}"
            group = QGroupBox(f"{label}  ·  {worse:.2f}% below top")
            pair_tp = result.get("tp_player")
            pair_ws = result.get("ws_player")
            if pair_tp is not None and pair_ws is not None:
                row = QHBoxLayout(group)
                row.addWidget(self._gear_panel("TP set", pair_tp.gearset))
                row.addWidget(self._gear_panel("WS set", pair_ws.gearset))
            else:
                grid = QGridLayout(group)
                player = result.get("player")
                gearset = getattr(player, "gearset", {}) if player is not None else {}
                self._add_gear_cells(grid, gearset, columns=4, cell_width=198)
            content_layout.addWidget(group)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignRight)

    def _load_selected(self, destination: str):
        """Load the selected result and return the user to the destination tab."""
        if self.parent() is not None:
            self.parent().load_optimizer_result(self.load_combo.currentIndex(), destination)
        self.accept()

    def _gear_panel(self, title: str, gearset: dict) -> QGroupBox:
        panel = QGroupBox(title)
        grid = QGridLayout(panel)
        self._add_gear_cells(grid, gearset, columns=2, cell_width=174)
        return panel

    def _add_gear_cells(self, grid: QGridLayout, gearset: dict, *, columns: int, cell_width: int):
        for index, slot in enumerate(SLOTS):
            item = gearset.get(slot, gear.Empty)
            cell = QFrame()
            cell.setFixedSize(cell_width, 68)
            cell.setFrameShape(QFrame.Shape.StyledPanel)
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(5, 4, 5, 4)
            icon_label = QLabel()
            icon_label.setFixedSize(36, 36)
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon = self.icons.icon(item)
            if not icon.isNull():
                icon_label.setPixmap(icon.pixmap(QSize(32, 32)))
            cell_layout.addWidget(icon_label)
            text_layout = QVBoxLayout()
            slot_label = QLabel(slot.upper())
            slot_label.setStyleSheet("font-size: 10px; color: #667085;")
            name = str(item.get("Name") or "Empty")
            name_label = QLabel()
            name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            name_label.setToolTip(name)
            name_label.setText(QFontMetrics(name_label.font()).elidedText(
                name, Qt.TextElideMode.ElideRight, max(90, cell_width - 58)
            ))
            text_layout.addWidget(slot_label)
            text_layout.addWidget(name_label)
            text_layout.addStretch(1)
            cell_layout.addLayout(text_layout, 1)
            row, column = divmod(index, columns)
            grid.addWidget(cell, row, column)


class GearBlacklistDialog(QDialog):
    """Edit the account-wide item-name blacklist used by every character."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Global gear blacklist")
        self.resize(560, 480)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Blacklisted base names are hidden from every character's gear pickers "
            "and optimizer candidates. Augmented variants are covered by their base name."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter available gear...")
        self.filter.textChanged.connect(self._filter_items)
        layout.addWidget(self.filter)
        self.items = QListWidget()
        self.items.setAlternatingRowColors(True)
        self.items.setToolTip("Check an item to hide it from every character and optimizer section.")
        available_items = parent.available_blacklist_items()
        for key in parent.gear_blacklist:
            available_items.setdefault(key, key)
        for key, label in available_items.items():
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if key in parent.gear_blacklist else Qt.CheckState.Unchecked
            )
            self.items.addItem(item)
        layout.addWidget(self.items, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _filter_items(self, text: str):
        needle = text.strip().casefold()
        for index in range(self.items.count()):
            item = self.items.item(index)
            item.setHidden(bool(needle) and needle not in item.text().casefold())

    def _save(self):
        values = {
            str(self.items.item(index).data(Qt.ItemDataRole.UserRole) or "")
            for index in range(self.items.count())
            if self.items.item(index).checkState() == Qt.CheckState.Checked
        }
        self.parent().set_gear_blacklist(values)
        self.accept()


class WeaponSkillRankingDialog(QDialog):
    """Three-column ranking of independently optimized WS sets."""

    def __init__(self, result: dict, icons: GearIconProvider, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Weapon-skill rankings")
        self.resize(980, 680)
        self.icons = icons
        self.cell_results = {}
        layout = QVBoxLayout(self)
        note = QLabel(
            f"{result.get('skill_type') or 'Selected weapon'}: each column is optimized "
            "and ranked independently for the current enemy, buffs, abilities, "
            "candidates, and locked weapon setup."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        tiers = tuple(result.get("tp_values") or (1000, 2000, 3000))
        rankings = result.get("rankings") or {}
        row_count = max((len(rankings.get(tp, ())) for tp in tiers), default=0)
        self.table = QTableWidget(row_count, len(tiers))
        self.table.setHorizontalHeaderLabels([f"{tp:,} TP ranking" for tp in tiers])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        for column, tp_value in enumerate(tiers):
            for row, entry in enumerate(rankings.get(tp_value, ())):
                item = QTableWidgetItem(
                    f"{entry.get('rank', row + 1)}. {entry['ws_name']}\n"
                    f"{float(entry['damage']):,.0f} average damage"
                )
                item.setData(Qt.ItemDataRole.UserRole, float(entry["damage"]))
                item.setToolTip(
                    f"Seed: {entry.get('seed')}\n"
                    "Select this cell to load its optimized set into the WS editor."
                )
                self.table.setItem(row, column, item)
                self.cell_results[(row, column)] = entry
        self.table.resizeColumnsToContents()
        self.table.resizeRowsToContents()
        self.table.currentCellChanged.connect(self._selection_changed)
        content = QHBoxLayout()
        content.addWidget(self.table, 1)
        preview_box = QGroupBox("Selected WS set")
        preview_box.setMinimumWidth(360)
        preview_box_layout = QVBoxLayout(preview_box)
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.preview_widget = QWidget()
        self.preview_layout = QGridLayout(self.preview_widget)
        self.preview_layout.setContentsMargins(2, 2, 2, 2)
        self.preview_scroll.setWidget(self.preview_widget)
        preview_box_layout.addWidget(self.preview_scroll, 1)
        content.addWidget(preview_box, 0)
        layout.addLayout(content, 1)
        errors = list(result.get("errors") or ())
        if errors:
            error_label = QLabel(
                f"{len(errors)} unsupported or infeasible WS/TP evaluations were skipped. "
                "Details are available in the optimizer log."
            )
            error_label.setWordWrap(True)
            layout.addWidget(error_label)
        controls = QHBoxLayout()
        load = QPushButton("Load selected optimized WS set")
        load.setEnabled(parent is not None)
        load.clicked.connect(self._load_selected)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        controls.addWidget(load)
        controls.addStretch(1)
        controls.addWidget(close)
        layout.addLayout(controls)
        for row in range(self.table.rowCount()):
            for column in range(self.table.columnCount()):
                if self.table.item(row, column) is not None:
                    self.table.setCurrentCell(row, column)
                    return

    def _selection_changed(self, *_args):
        indexes = self.table.selectedIndexes()
        if not indexes:
            self._render_preview(None)
            return
        index = indexes[0]
        self._render_preview(self.cell_results.get((index.row(), index.column())))

    def _render_preview(self, entry: dict | None):
        while self.preview_layout.count():
            item = self.preview_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        if entry is None:
            self.preview_layout.addWidget(QLabel("Select a WS ranking cell to preview its set."), 0, 0)
            return
        title = QLabel(
            f"{entry.get('ws_name', 'Weapon skill')} · {int(entry.get('tp') or 0):,} TP\n"
            f"{float(entry.get('damage') or 0):,.0f} average damage"
        )
        title.setObjectName("sectionTitle")
        title.setWordWrap(True)
        self.preview_layout.addWidget(title, 0, 0, 1, 2)
        player = entry.get("player")
        gearset = getattr(player, "gearset", {}) if player is not None else {}
        for index, slot in enumerate(SLOTS, start=1):
            item = gearset.get(slot, gear.Empty)
            cell = QFrame()
            cell.setMinimumHeight(58)
            cell.setFrameShape(QFrame.Shape.StyledPanel)
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(4, 3, 4, 3)
            icon_label = QLabel()
            icon_label.setFixedSize(34, 34)
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon = self.icons.icon(item)
            if not icon.isNull():
                icon_label.setPixmap(icon.pixmap(QSize(30, 30)))
            cell_layout.addWidget(icon_label)
            name = QLabel(f"{slot.upper()}\n{item.get('Name') or 'Empty'}")
            name.setWordWrap(True)
            name.setToolTip(item_tooltip(item))
            cell_layout.addWidget(name, 1)
            row, column = divmod(index - 1, 2)
            self.preview_layout.addWidget(cell, row + 1, column)
        self.preview_layout.setRowStretch((len(SLOTS) + 1) // 2 + 1, 1)

    def _load_selected(self):
        indexes = self.table.selectedIndexes()
        if not indexes:
            return
        index = indexes[0]
        entry = self.cell_results.get((index.row(), index.column()))
        if entry is not None and self.parent() is not None:
            self.parent().load_ws_ranking_result(entry)
            self.accept()


def _report_enemy(raw_enemy: dict, debuffs: dict | None = None):
    """Create an enemy after applying the same debuff model as the legacy UI."""
    source = dict(raw_enemy)
    base_defense = source["Defense"]
    for stat, value in (debuffs or {}).items():
        if stat == "Defense":
            source[stat] *= 1 - value
        else:
            source[stat] = source.get(stat, 0) - value
    enemy = create_player.create_enemy(source)
    enemy.stats["Base Defense"] = base_defense
    enemy.stats["Defense"] = max(1, enemy.stats["Defense"])
    enemy.stats["Magic Defense"] = max(-50, enemy.stats["Magic Defense"])
    enemy.stats["Magic Damage Taken"] = enemy.stats.pop("Magic DT%")
    return enemy


def _evaluate_profile_set(payload: dict, context: dict) -> dict:
    """Evaluate one profile set using the same player/action modules as the UI."""
    try:
        problems = []
        if payload.get("missing"):
            problems.append("unresolved slots: " + ", ".join(payload["missing"]))
        if payload.get("incomplete"):
            problems.append("incomplete item models: " + ", ".join(payload["incomplete"]))
        if payload.get("ineligible"):
            problems.append("job-ineligible slots: " + ", ".join(payload["ineligible"]))
        if problems:
            raise ValueError("; ".join(problems))
        player = create_player.create_player(
            context["main_job"], context["sub_job"], context["master_level"],
            gearset=payload["gearset"], buffs=context["buffs"], abilities=context["abilities"],
        )
        enemy = _report_enemy(context["enemy"], context.get("debuffs"))
        row = {
            "name": payload["name"], "category": payload["category"] or "WS",
            "ws_name": payload["ws_name"] or "", "tp_dps": "", "time_to_ws": "",
            "ws_damage": "", "total_dps": "", "weapon_setup": payload.get("weapon_setup", "As listed"),
            "error": "",
            "_tp_damage": None, "_tp_return": None, "_attack_time": None,
            "_player": player,
        }
        if payload["category"] is not None:
            attack = actions.average_attack_round(
                player, enemy, 0, context["tp_value"], "Time to WS"
            )
            row["tp_dps"] = attack[1][0] / max(attack[1][2], 1e-9)
            row["time_to_ws"] = attack[0]
            row["_tp_damage"] = attack[1][0]
            row["_tp_return"] = attack[1][1]
            row["_attack_time"] = attack[1][2]
        if payload["ws_name"]:
            ws_type = "ranged" if payload["ws_name"] in (
                WS_BY_SKILL.get("Marksmanship", []) + WS_BY_SKILL.get("Archery", [])
            ) else "melee"
            if payload["category"] is not None:
                row["total_dps"], cycle = actions.average_tp_ws_cycle(
                    player, player, enemy, row["ws_name"], context["tp_value"], ws_type
                )
                row["ws_damage"] = cycle[3]
            else:
                row["ws_damage"] = actions.average_ws(
                    player, enemy, payload["ws_name"], context["tp_value"],
                    ws_type, "Damage dealt",
                )[0]
        return row
    except Exception as error:
        return {
            "name": payload["name"], "category": payload["category"] or "WS",
            "ws_name": payload["ws_name"] or "", "tp_dps": "", "time_to_ws": "",
            "ws_damage": "", "total_dps": "", "weapon_setup": payload.get("weapon_setup", "As listed"),
            "error": str(error),
            "_tp_damage": None, "_tp_return": None, "_attack_time": None,
            "_player": None,
        }


class ProfileReportThread(QThread):
    progress = pyqtSignal(str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    stopped = pyqtSignal()

    def __init__(self, payloads: list[dict], context: dict, parent=None):
        super().__init__(parent)
        self.payloads = payloads
        self.context = context
        self._stop_requested = threading.Event()

    def request_stop(self):
        self._stop_requested.set()

    def run(self):
        try:
            rows = []
            cache = {}
            for index, payload in enumerate(self.payloads, 1):
                if self._stop_requested.is_set():
                    self.stopped.emit()
                    return
                self.progress.emit(f"Evaluating {index}/{len(self.payloads)}: {payload['name']}")
                key = (
                    payload.get("category"), payload.get("ws_name"),
                    tuple(str(payload["gearset"][slot].get("Bridge Key") or item_name(payload["gearset"][slot])) for slot in SLOTS),
                    tuple(payload.get("missing") or ()), tuple(payload.get("incomplete") or ()),
                    tuple(payload.get("ineligible") or ()),
                )
                if key in cache:
                    row = dict(cache[key])
                    row["name"] = payload["name"]
                    row["weapon_setup"] = payload.get("weapon_setup", row["weapon_setup"])
                else:
                    row = _evaluate_profile_set(payload, self.context)
                    cache[key] = row
                rows.append(row)
            if self._stop_requested.is_set():
                self.stopped.emit()
                return
            tp_rows = [row for row in rows if row["category"] == "TP" and not row.get("error")]
            ws_rows = [row for row in rows if row["category"] == "WS" and row.get("ws_name") and not row.get("error")]
            enemy = _report_enemy(self.context["enemy"], self.context.get("debuffs"))
            best_by_ws = {}
            total_pairs = len(tp_rows) * len(ws_rows)
            pair_index = 0
            for ws_row in ws_rows:
                ws_type = "ranged" if ws_row["ws_name"] in (
                    WS_BY_SKILL.get("Marksmanship", []) + WS_BY_SKILL.get("Archery", [])
                ) else "melee"
                for tp_row in tp_rows:
                    if self._stop_requested.is_set():
                        self.stopped.emit()
                        return
                    pair_index += 1
                    self.progress.emit(
                        f"Comparing TP/WS pair {pair_index}/{total_pairs}: "
                        f"{tp_row['name']} â†’ {ws_row['name']}"
                    )
                    try:
                        total_dps, cycle = actions.average_tp_ws_cycle(
                            tp_row["_player"], ws_row["_player"], enemy,
                            ws_row["ws_name"], self.context["tp_value"], ws_type,
                        )
                    except Exception:
                        continue
                    candidate = {
                        "name": f"{tp_row['name']} â†’ {ws_row['name']}",
                        "category": "Best Pair", "ws_name": ws_row["ws_name"],
                        "weapon_setup": ws_row["weapon_setup"],
                        "tp_dps": tp_row["tp_dps"], "time_to_ws": cycle[2],
                        "ws_damage": cycle[3], "total_dps": total_dps,
                        "error": "", "_player": None,
                    }
                    current = best_by_ws.get(ws_row["ws_name"])
                    if current is None or total_dps > current["total_dps"]:
                        best_by_ws[ws_row["ws_name"]] = candidate
            rows.extend(sorted(
                best_by_ws.values(), key=lambda row: row["total_dps"], reverse=True
            ))
            self.succeeded.emit(rows)
        except Exception as error:
            self.failed.emit(str(error))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WSDist — Qt")
        self.resize(1220, 820)
        self.setMinimumSize(QSize(900, 650))
        self.icons = GearIconProvider()
        window_icon = self.icons.icon({"Item ID": 23937})
        if not window_icon.isNull():
            self.setWindowIcon(window_icon)
        self.settings = QSettings("WSDist", "QtGui")
        self.bridge_store = BridgeStore()
        self.character_paths: dict[str, Path] = {}
        self._active_character_key = ""
        self.equipment = _base_equipment()
        self.optimizer_thread: OptimizeThread | None = None
        self.report_thread: ProfileReportThread | None = None
        self.best_player = None
        self.best_tp_player = None
        self.best_ws_player = None
        self._optimizer_action_in_progress = ""
        self._last_completed_optimizer_action = ""
        self._last_substat_summary = []
        self.optimizer_top_results: list[dict] = []
        self._ranking_skill_in_progress: str | None = None
        self._optimizer_run_state: dict[int, dict] = {}
        self._optimizer_started_at: float | None = None
        self._optimizer_status_timer = QTimer(self)
        self._optimizer_status_timer.setInterval(1000)
        self._optimizer_status_timer.timeout.connect(self._refresh_optimizer_status)
        self._optimizer_run_cards: dict[int, dict] = {}
        self.top_sets_dialog: TopSetsDialog | None = None
        self.gear_blacklist: set[str] = self._load_gear_blacklist()
        self.shared_catalog: dict[str, dict] = {}
        self.candidates = {slot: {"Empty"} for slot in SLOTS}
        self.locked_gear = {slot: "" for slot in SLOTS}
        self._build_ui()
        self._restore_settings()
        self._refresh_job_data()

    def _build_ui(self):
        self._build_menu()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_inputs())
        splitter.addWidget(self._build_workspace())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([310, 900])
        self.setCentralWidget(splitter)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready — no background polling")
        self.setStyleSheet("""
            QMainWindow { background: #f3f5f7; }
            QGroupBox { font-weight: 600; margin-top: 10px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
            QLabel#sectionTitle { font-size: 16px; font-weight: 700; padding: 3px; }
            QPushButton { padding: 5px 9px; }
            QPlainTextEdit { font-family: Consolas, monospace; }
        """)

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("File")
        select_root = QAction("Select Ashita folder...", self)
        select_root.triggered.connect(self.choose_bridge_root)
        refresh = QAction("Refresh selected character", self)
        refresh.triggered.connect(self.refresh_bridge)
        blacklist = QAction("Gear blacklist...", self)
        blacklist.triggered.connect(self.open_gear_blacklist)
        legacy = QAction("About legacy interface", self)
        legacy.triggered.connect(lambda: QMessageBox.information(
            self, "Legacy interface",
            "Run python gui_main.py to use the restored Tk interface."
        ))
        close = QAction("Exit", self)
        close.triggered.connect(self.close)
        file_menu.addActions([select_root, refresh, blacklist, legacy])
        file_menu.addSeparator()
        file_menu.addAction(close)

    def _load_gear_blacklist(self) -> set[str]:
        raw = self.settings.value("global_gear_blacklist", "")
        try:
            values = raw if isinstance(raw, list) else json.loads(str(raw or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            values = []
        return {
            _normalized_item_name(value) for value in values
            if _normalized_item_name(value)
        }

    def available_blacklist_items(self) -> dict[str, str]:
        """Return selectable base item names from every discovered inventory."""
        available: dict[str, str] = {}

        def add(item: dict):
            if item_name(item) == "Empty":
                return
            base = _normalized_item_name(item.get("Name") or item.get("Name2"))
            if base:
                available.setdefault(base, str(item.get("Name") or item.get("Name2")))

        for values in self.equipment.values():
            for item in values:
                add(item)
        for item in self.bridge_store.catalog.values():
            if item.get("Eligible"):
                add(item)
        for label, path in self.character_paths.items():
            if self.bridge_store.bridge_path and path.resolve() == self.bridge_store.bridge_path.resolve():
                continue
            try:
                store = BridgeStore(self.bridge_store.ashita_root)
                store.load(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            for item in store.catalog.values():
                if item.get("Eligible"):
                    add(item)
        return dict(sorted(available.items(), key=lambda entry: entry[1].casefold()))

    def set_gear_blacklist(self, values: set[str]) -> None:
        self.gear_blacklist = {
            _normalized_item_name(value) for value in values if _normalized_item_name(value)
        }
        self.settings.setValue("global_gear_blacklist", json.dumps(sorted(self.gear_blacklist)))
        self._reset_invalid_equipment()
        for slot in SLOTS:
            self._update_candidate_button(slot)
        self.statusBar().showMessage(
            f"Global gear blacklist updated ({len(self.gear_blacklist)} item names).", 5000
        )

    def open_gear_blacklist(self):
        dialog = GearBlacklistDialog(self)
        dialog.exec()

    def _build_inputs(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        bridge = QGroupBox("Character bridge")
        bridge_layout = QVBoxLayout(bridge)
        choose = QPushButton("Select Ashita folder...")
        choose.clicked.connect(self.choose_bridge_root)
        self.character_combo = QComboBox()
        self.character_combo.setEnabled(False)
        self.character_combo.currentTextChanged.connect(self._load_character)
        self.bridge_label = QLabel("No character loaded")
        self.bridge_label.setWordWrap(True)
        bridge_layout.addWidget(choose)
        bridge_layout.addWidget(self.character_combo)
        bridge_layout.addWidget(self.bridge_label)
        layout.addWidget(bridge)

        player = QGroupBox("Player")
        form = QFormLayout(player)
        self.main_job = QComboBox()
        self.main_job.addItems(sorted(JOBS))
        self.main_job.setCurrentText("Scholar")
        self.sub_job = QComboBox()
        self.sub_job.addItems(sorted(JOBS) + ["None"])
        self.sub_job.setCurrentText("Red Mage")
        self.master_level = QSpinBox()
        self.master_level.setRange(0, 50)
        self.master_level.setValue(30)
        self.master_level.setToolTip(
            "Loads from GearSetBuilder for the selected main job when that character bridge includes it."
        )
        self.hoxne_mastery_rank = QSpinBox()
        self.hoxne_mastery_rank.setRange(1, 10)
        self.hoxne_mastery_rank.setValue(5)
        self.hoxne_mastery_rank.setToolTip(
            "Hoxne Earring's account-wide Mastery Rank (MR1=-30 through MR10=+30 to all seven attributes). "
            "Ashita does not expose this account-wide rank, so it is saved per character here."
        )
        self.tp_value = QSpinBox()
        self.tp_value.setRange(1000, 3000)
        self.tp_value.setSingleStep(100)
        self.tp_value.setValue(1900)
        self.aftermath = QSpinBox()
        self.aftermath.setRange(0, 3)
        self.ws_combo = QComboBox()
        self.ws_combo.setEditable(True)
        self.spell_combo = QComboBox()
        self.spell_combo.setEditable(True)
        form.addRow("Main job", self.main_job)
        form.addRow("Sub job", self.sub_job)
        form.addRow("Master level", self.master_level)
        form.addRow("Hoxne mastery rank", self.hoxne_mastery_rank)
        form.addRow("TP", self.tp_value)
        form.addRow("Aftermath", self.aftermath)
        form.addRow("Weapon skill", self.ws_combo)
        form.addRow("Spell", self.spell_combo)
        self.main_job.currentTextChanged.connect(self._refresh_job_data)
        self.sub_job.currentTextChanged.connect(self._refresh_quick_ability_job)
        self.master_level.valueChanged.connect(self._refresh_quick_ability_job)
        self.hoxne_mastery_rank.valueChanged.connect(self._hoxne_mastery_rank_changed)
        self.aftermath.valueChanged.connect(self.refresh_quick_stats)
        layout.addWidget(player)

        enemy_box = QGroupBox("Enemy")
        enemy_form = QFormLayout(enemy_box)
        self.enemy_combo = QComboBox()
        self.enemy_combo.addItems(list(enemies.preset_enemies))
        self.enemy_combo.setCurrentText("BG Wiki sets")
        self.enemy_spins = {}
        enemy_form.addRow("Preset", self.enemy_combo)
        for stat in (
            "Defense", "Evasion", "VIT", "AGI", "INT", "MND", "CHR",
            "Magic Evasion", "Magic Defense", "Magic DT%",
        ):
            spin = QSpinBox()
            spin.setRange(-99999, 99999)
            self.enemy_spins[stat] = spin
            enemy_form.addRow(stat, spin)
        self.enemy_combo.currentTextChanged.connect(self._load_enemy)
        self._load_enemy(self.enemy_combo.currentText())
        layout.addWidget(enemy_box)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        scroll.setMinimumWidth(285)
        return scroll

    def _build_workspace(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        self.tabs = QTabWidget()
        self.quick_set = GearSetEditor("Quick Look equipment", self)
        self.tp_set = GearSetEditor("TP equipment", self)
        self.ws_set = GearSetEditor("Weapon-skill equipment", self)
        self.quick_set.changed.connect(self._gear_changed)
        self.tabs.addTab(self._quick_tab(), "Quick Look")
        self.tabs.addTab(self._quick_abilities_tab(), "JA")
        self.tabs.addTab(self._optimizer_tab(), "Optimizer")
        self.tabs.addTab(self._sets_tab(), "TP / WS Sets")
        self.tabs.addTab(self._buffs_tab(), "Buffs")
        self.tabs.addTab(self._profile_report_tab(), "LAC Report")
        layout.addWidget(self.tabs, 1)
        return container

    def _quick_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(self.quick_set, 1)
        buttons = QHBoxLayout()
        definitions = (
            ("Evaluate WS", lambda: self.evaluate("ws")),
            ("Evaluate attack round", lambda: self.evaluate("tp")),
            ("Evaluate spell", lambda: self.evaluate("spell")),
            ("Copy to TP set", lambda: self.tp_set.set_gearset(self.quick_set.items)),
            ("Copy to WS set", lambda: self.ws_set.set_gearset(self.quick_set.items)),
        )
        for label, callback in definitions:
            button = QPushButton(label)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.result_label = QLabel("Select equipment, then evaluate an action.")
        self.result_label.setObjectName("sectionTitle")
        layout.addWidget(self.result_label)
        totals = QGroupBox("Quick Look totals")
        totals_layout = QVBoxLayout(totals)
        totals_header = QHBoxLayout()
        totals_header.addWidget(QLabel(
            "Uses selected gear and Buffs settings. "
            "<span style='color:#9a6700'><b>Amber</b></span> = under cap, "
            "<span style='color:#137333'><b>green</b></span> = at cap, "
            "<span style='color:#b42318'><b>red</b></span> = over cap."
        ))
        totals_header.addStretch(1)
        refresh_totals = QPushButton("Refresh totals")
        refresh_totals.clicked.connect(self.refresh_quick_stats)
        totals_header.addWidget(refresh_totals)
        totals_layout.addLayout(totals_header)
        self.quick_stats_scroll = QScrollArea()
        self.quick_stats_scroll.setWidgetResizable(True)
        self.quick_stats_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.quick_stats_widget = QWidget()
        self.quick_stats_layout = QGridLayout(self.quick_stats_widget)
        self.quick_stats_layout.setContentsMargins(0, 0, 0, 0)
        self.quick_stats_scroll.setWidget(self.quick_stats_widget)
        self.quick_stats_scroll.setMinimumHeight(220)
        self.quick_stats_scroll.setMaximumHeight(390)
        totals_layout.addWidget(self.quick_stats_scroll)
        layout.addWidget(totals, 1)
        return tab

    def _quick_abilities_tab(self) -> QWidget:
        """Build the compact job-ability controls used by Quick Look.

        These checkboxes write the same ability names consumed by
        ``create_player``.  They are intentionally grouped by source job so
        the large unused area in Quick Look becomes useful without adding a
        second calculation path.
        """
        tab = QWidget()
        outer = QVBoxLayout(tab)
        note = QLabel(
            "Check abilities that are active for this calculation. Main- and "
            "sub-job availability follows the engine's level rules; party "
            "abilities are shown separately."
        )
        note.setWordWrap(True)
        outer.addWidget(note)
        self.quick_ability_status = QLabel()
        self.quick_ability_status.setObjectName("sectionTitle")
        outer.addWidget(self.quick_ability_status)

        content = QWidget()
        self.quick_ability_layout = QGridLayout(content)
        self.quick_ability_layout.setContentsMargins(4, 4, 4, 4)
        self.quick_ability_layout.setHorizontalSpacing(12)
        self.quick_ability_layout.setVerticalSpacing(8)
        self.quick_ability_controls: dict[str, QCheckBox] = {}
        self.quick_ability_metadata: dict[str, tuple[int, tuple[str, ...], str]] = {}
        for name, level, jobs, description in JOB_ABILITY_DEFINITIONS:
            self.quick_ability_metadata[name] = (level, jobs, description)

        self.quick_ability_scroll = QScrollArea()
        self.quick_ability_scroll.setWidgetResizable(True)
        self.quick_ability_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.quick_ability_scroll.setWidget(content)
        outer.addWidget(self.quick_ability_scroll, 1)

        # The JSON editor remains available for profile-specific abilities not
        # represented by the standard toggles.  It also gives saved Buff
        # presets a stable place to persist the selections.
        custom_box = QGroupBox("Custom ability values (optional JSON)")
        custom_layout = QVBoxLayout(custom_box)
        self.abilities_json = QPlainTextEdit("{}")
        self.abilities_json.setPlaceholderText('{"Ability name": true}')
        self.abilities_json.setMaximumHeight(70)
        self.abilities_json.setToolTip(
            "Optional JSON values for abilities not listed above. Standard "
            "checkboxes update this object automatically."
        )
        self.abilities_json.textChanged.connect(self.refresh_quick_stats)
        custom_layout.addWidget(self.abilities_json)
        outer.addWidget(custom_box)
        self._rebuild_quick_ability_controls()
        return tab

    def _rebuild_quick_ability_controls(self):
        """Show controls valid for the currently selected main/sub jobs."""
        if not hasattr(self, "quick_ability_layout"):
            return
        while self.quick_ability_layout.count():
            item = self.quick_ability_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.quick_ability_controls.clear()
        main = JOBS.get(self.main_job.currentText(), "")
        sub = JOBS.get(self.sub_job.currentText(), "")
        sub_level = 49 + self.master_level.value() / 5
        groups: list[tuple[str, list[tuple[str, int, tuple[str, ...], str]]]] = [
            (f"Main job: {self.main_job.currentText()}", []),
            (f"Sub job: {self.sub_job.currentText()}", []),
            ("Party / assumed abilities", []),
        ]
        for name, level, jobs, description in JOB_ABILITY_DEFINITIONS:
            if "*" in jobs:
                groups[2][1].append((name, level, jobs, description))
            elif main in jobs and level <= 99:
                groups[0][1].append((name, level, jobs, description))
            elif sub and sub in jobs and level <= sub_level:
                groups[1][1].append((name, level, jobs, description))

        for group_index, (title, entries) in enumerate(groups):
            box = QGroupBox(title)
            grid = QGridLayout(box)
            grid.setContentsMargins(8, 12, 8, 8)
            for index, (name, level, _jobs, description) in enumerate(entries):
                check = QCheckBox(name)
                check.setToolTip(f"{description} Available at level {level}.")
                check.toggled.connect(
                    lambda enabled, ability=name: self._quick_ability_toggled(ability, enabled)
                )
                self.quick_ability_controls[name] = check
                grid.addWidget(check, index // 3, index % 3)
            if not entries:
                grid.addWidget(QLabel("No abilities available for this job."), 0, 0)
            for column in range(3):
                grid.setColumnStretch(column, 1)
            self.quick_ability_layout.addWidget(box, group_index // 2, group_index % 2)
        self.quick_ability_layout.setRowStretch(2, 1)
        self._sync_quick_ability_controls()

    def _sync_quick_ability_controls(self, abilities: dict | None = None):
        if not hasattr(self, "quick_ability_controls"):
            return
        if abilities is None:
            try:
                abilities = self._json_object(self.abilities_json, "Abilities")
            except Exception:
                abilities = {}
        ignored = {"Enhancing Skill", "Storm spell", "Enemy Resist Rank", "99999"}
        active = [str(name) for name, value in abilities.items()
                  if name not in ignored and name != "Aftermath" and bool(value)]
        active.sort(key=str.casefold)
        aftermath = abilities.get("Aftermath", 0)
        if aftermath:
            active.insert(0, f"Aftermath Lv {aftermath}")
        for name, control in self.quick_ability_controls.items():
            control.blockSignals(True)
            control.setChecked(bool(abilities.get(name, False)))
            control.blockSignals(False)
        if hasattr(self, "quick_ability_status"):
            self.quick_ability_status.setText(
                f"{len(active)} active · {', '.join(active) if active else 'none'}"
            )

    def _quick_ability_toggled(self, name: str, enabled: bool):
        try:
            abilities = self._json_object(self.abilities_json, "Abilities")
        except Exception as error:
            self.statusBar().showMessage(f"Unable to update ability: {error}", 5000)
            return
        abilities[name] = bool(enabled)
        # These pairs represent the main-job and subjob versions of the same
        # stance.  Match the legacy UI by keeping them mutually exclusive.
        paired = {
            "Haste Samba": "Haste Samba (sub)",
            "Haste Samba (sub)": "Haste Samba",
            "Warcry": "Warcry (sub)",
            "Warcry (sub)": "Warcry",
        }
        if enabled and name in paired:
            abilities[paired[name]] = False
        self.abilities_json.blockSignals(True)
        self.abilities_json.setPlainText(json.dumps(abilities, indent=2, sort_keys=True))
        self.abilities_json.blockSignals(False)
        self.refresh_quick_stats()

    @staticmethod
    def _quick_stat_value(value, *, percent: bool = False) -> str:
        if percent:
            return f"{float(value) * 100:.1f}%"
        value = float(value)
        return f"{value:,.1f}" if value % 1 else f"{value:,.0f}"

    @staticmethod
    def _pdif_base_cap(skill_type: str, *, ranged: bool = False) -> float | None:
        if ranged:
            return 3.5 if skill_type == "Marksmanship" else 3.25 if skill_type == "Archery" else None
        if skill_type in {"Katana", "Dagger", "Sword", "Axe", "Club"}:
            return 3.25
        if skill_type in {"Great Katana", "Hand-to-Hand"}:
            return 3.5
        if skill_type in {"Great Sword", "Staff", "Great Axe", "Polearm"}:
            return 3.75
        if skill_type == "Scythe":
            return 4.0
        return None

    def _quick_stats_text(self, player, enemy) -> str:
        stats = player.stats
        gear_haste = min(float(stats.get("Gear Haste", 0)), 0.25)
        magic_haste = min(float(stats.get("Magic Haste", 0)), 448 / 1024)
        ja_haste = min(float(stats.get("JA Haste", 0)), 0.25)
        total_haste = gear_haste + magic_haste + ja_haste
        # Use the same capped calculation as the optimizer so DT contributes
        # to both physical and magical reduction (including PDT2/MDT2 terms).
        pdt_total, mdt_total = create_player.calculate_damage_taken(
            player.gearset, player.buffs, player.abilities
        )

        def number(name: str) -> str:
            return self._quick_stat_value(stats.get(name, 0))

        def percent(name: str) -> str:
            return self._quick_stat_value(stats.get(name, 0) / 100, percent=True)

        main_skill = player.gearset["main"].get("Skill Type", "None")
        sub_skill = player.gearset["sub"].get("Skill Type", "None")
        main_hit_cap = 0.99 if main_skill in {"Axe", "Club", "Dagger", "Sword", "Katana", "Hand-to-Hand"} else 0.95
        sub_hit_cap = 0.99 if sub_skill == "Hand-to-Hand" else 0.95
        main_hit = actions.get_hit_rate(stats.get("Accuracy1", 0), enemy.stats["Evasion"], main_hit_cap)
        dual_wield = player.gearset["sub"].get("Type") == "Weapon" or main_skill == "Hand-to-Hand"
        sub_hit = actions.get_hit_rate(stats.get("Accuracy2", 0), enemy.stats["Evasion"], sub_hit_cap) if dual_wield else 0
        ranged_accuracy = stats.get("Ranged Accuracy", 0) + 100 * (stats.get("Daken", 0) > 0)
        ranged_cap = 0.99 if player.abilities.get("Sharpshot", False) else 0.95
        ranged_hit = actions.get_hit_rate(ranged_accuracy, enemy.stats["Evasion"], ranged_cap)

        pdl_gear = stats.get("PDL", 0) / 100
        pdl_trait = stats.get("PDL Trait", 0) / 100
        main_base_cap = self._pdif_base_cap(main_skill)
        main_ratio = stats.get("Attack1", 0) / max(1, enemy.stats["Defense"])
        main_pdif_cap = (main_base_cap + pdl_trait) * (1 + pdl_gear) if main_base_cap else None
        ranged_skill = player.gearset["ranged"].get("Skill Type", "None")
        if ranged_skill == "None":
            ranged_skill = player.gearset["ammo"].get("Skill Type", "None")
        ranged_base_cap = self._pdif_base_cap(ranged_skill, ranged=True)
        ranged_ratio = stats.get("Ranged Attack", 0) / max(1, enemy.stats["Defense"])
        ranged_pdif_cap = (ranged_base_cap + pdl_trait) * (1 + pdl_gear) if ranged_base_cap else None

        def ratio_to_cap(ratio: float, cap: float | None) -> str:
            if cap is None:
                return "N/A"
            return f"{ratio:.2f} / {cap:.2f} ({ratio / cap * 100:.1f}% of cap)"

        return "\n".join((
            "HASTE / DELAY",
            f"Total haste (source capped): {self._quick_stat_value(total_haste, percent=True)}    "
            f"Gear: {self._quick_stat_value(gear_haste, percent=True)}    "
            f"Magic: {self._quick_stat_value(magic_haste, percent=True)}    "
            f"JA: {self._quick_stat_value(ja_haste, percent=True)}    "
            f"Delay reduction: {self._quick_stat_value(stats.get('Delay Reduction', 0), percent=True)}",
            "",
            "ATTRIBUTES",
            f"STR {number('STR'):>7}    DEX {number('DEX'):>7}    VIT {number('VIT'):>7}    "
            f"AGI {number('AGI'):>7}    INT {number('INT'):>7}    MND {number('MND'):>7}    CHR {number('CHR'):>7}",
            "",
            "OFFENSE",
            f"Main Acc {number('Accuracy1'):>7}    Off-hand Acc {number('Accuracy2'):>7}    "
            f"Main Atk {number('Attack1'):>7}    Off-hand Atk {number('Attack2'):>7}    "
            f"Rng Acc {number('Ranged Accuracy'):>7}    Rng Atk {number('Ranged Attack'):>7}",
            f"Magic Acc {number('Magic Accuracy'):>7}    Magic Atk {number('Magic Attack'):>7}    "
            f"Magic Dmg {number('Magic Damage'):>7}    Store TP {number('Store TP'):>7}    "
            f"Double Dmg {self._quick_stat_value(stats.get('Double Damage', 0) / 100, percent=True):>7}",
            f"Hit rate  Main {self._quick_stat_value(main_hit, percent=True):>7}    "
            f"Off-hand {self._quick_stat_value(sub_hit, percent=True):>7}    "
            f"Ranged/Daken {self._quick_stat_value(ranged_hit, percent=True):>7}",
            "",
            "pDIF / PDL",
            f"PDL gear {self._quick_stat_value(pdl_gear, percent=True):>7}    "
            f"PDL trait {self._quick_stat_value(pdl_trait, percent=True):>7}    "
            f"Main cRatio / pDIF cap: {ratio_to_cap(main_ratio, main_pdif_cap)}",
            f"Ranged cRatio / pDIF cap: {ratio_to_cap(ranged_ratio, ranged_pdif_cap)}",
            "",
            "DEFENSE / MULTI-ATTACK",
            f"DT {percent('DT'):>7}    PDT {percent('PDT'):>7}    MDT {percent('MDT'):>7}    "
            f"Total PDT {self._quick_stat_value(pdt_total / 100, percent=True):>7}    "
            f"Total MDT {self._quick_stat_value(mdt_total / 100, percent=True):>7}",
            f"Evasion {number('Evasion'):>7}    Magic Evasion {number('Magic Evasion'):>7}    "
            f"Magic Defense {number('Magic Defense'):>7}    DA {percent('DA'):>7}    "
            f"TA {percent('TA'):>7}    QA {percent('QA'):>7}",
        ))

    @staticmethod
    def _cap_state(value: float, cap: float, *, negative: bool = False) -> tuple[str, str]:
        if negative:
            used = max(0.0, -value)
            if used > cap + 0.05:
                return "over", f"Over {cap:.0f}% cap"
            if used >= cap - 0.05:
                return "at", f"At {cap:.0f}% cap"
            return "under", f"Under {cap:.0f}% cap"
        if value > cap + 0.0005:
            return "over", f"Over {cap * 100:.1f}% cap"
        if value >= cap - 0.0005:
            return "at", f"At {cap * 100:.1f}% cap"
        return "under", f"Under {cap * 100:.1f}% cap"

    def _quick_card(self, label: str, value: str, subtitle: str = "", state: str = "neutral") -> QFrame:
        colors = {
            "neutral": ("#263238", "#ffffff"),
            "under": ("#9a6700", "#fff8e1"),
            "at": ("#137333", "#e8f5e9"),
            "over": ("#b42318", "#ffebe9"),
        }
        foreground, background = colors.get(state, colors["neutral"])
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ border: 1px solid #c7d0d9; border-radius: 5px; background: {background}; }}"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(9, 6, 9, 6)
        name = QLabel(label)
        name.setStyleSheet("font-weight: 600; color: #344054; border: none; background: transparent;")
        amount = QLabel(value)
        amount.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {foreground}; border: none; background: transparent;")
        amount.setAlignment(Qt.AlignmentFlag.AlignLeft)
        card_layout.addWidget(name)
        card_layout.addWidget(amount)
        if subtitle:
            detail = QLabel(subtitle)
            detail.setStyleSheet(f"font-size: 10px; color: {foreground}; border: none; background: transparent;")
            card_layout.addWidget(detail)
        return card

    def _quick_section(self, title: str, cards: list[tuple[str, str, str, str]]) -> QGroupBox:
        section = QGroupBox(title)
        grid = QGridLayout(section)
        grid.setContentsMargins(8, 12, 8, 8)
        grid.setHorizontalSpacing(7)
        grid.setVerticalSpacing(7)
        for index, (label, value, subtitle, state) in enumerate(cards):
            grid.addWidget(self._quick_card(label, value, subtitle, state), index // 4, index % 4)
        for column in range(4):
            grid.setColumnStretch(column, 1)
        return section

    def _render_quick_stats(self, player, enemy):
        stats = player.stats
        while self.quick_stats_layout.count():
            item = self.quick_stats_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        def value(name: str) -> str:
            return self._quick_stat_value(stats.get(name, 0))

        def pct(name: str) -> str:
            return self._quick_stat_value(stats.get(name, 0) / 100, percent=True)

        def cap_card(label: str, raw: float, cap: float, *, negative: bool = False, percent: bool = True):
            state, detail = self._cap_state(raw, cap, negative=negative)
            shown = self._quick_stat_value(raw / 100 if percent and negative else raw, percent=percent)
            return label, shown, detail, state

        gear_haste_raw = float(stats.get("Gear Haste", 0))
        magic_haste_raw = float(stats.get("Magic Haste", 0))
        ja_haste_raw = float(stats.get("JA Haste", 0))
        source_haste = gear_haste_raw + magic_haste_raw + ja_haste_raw
        # Keep the cards aligned with the optimizer's exact mitigation gate;
        # DT contributes to both totals before the 50% caps are applied.
        total_pdt, total_mdt = create_player.calculate_damage_taken(
            player.gearset, player.buffs, player.abilities
        )
        main_skill = player.gearset["main"].get("Skill Type", "None")
        sub_skill = player.gearset["sub"].get("Skill Type", "None")
        main_cap = 0.99 if main_skill in {"Axe", "Club", "Dagger", "Sword", "Katana", "Hand-to-Hand"} else 0.95
        sub_cap = 0.99 if sub_skill == "Hand-to-Hand" else 0.95
        main_hit = actions.get_hit_rate(stats.get("Accuracy1", 0), enemy.stats["Evasion"], main_cap)
        dual_wield = player.gearset["sub"].get("Type") == "Weapon" or main_skill == "Hand-to-Hand"
        sub_hit = actions.get_hit_rate(stats.get("Accuracy2", 0), enemy.stats["Evasion"], sub_cap) if dual_wield else 0
        ranged_accuracy = stats.get("Ranged Accuracy", 0) + 100 * (stats.get("Daken", 0) > 0)
        ranged_cap = 0.99 if player.abilities.get("Sharpshot", False) else 0.95
        ranged_hit = actions.get_hit_rate(ranged_accuracy, enemy.stats["Evasion"], ranged_cap)
        pdl_gear = stats.get("PDL", 0) / 100
        pdl_trait = stats.get("PDL Trait", 0) / 100
        main_base = self._pdif_base_cap(main_skill)
        main_ratio = stats.get("Attack1", 0) / max(1, enemy.stats["Defense"])
        main_pdif_cap = (main_base + pdl_trait) * (1 + pdl_gear) if main_base else None
        ranged_skill = player.gearset["ranged"].get("Skill Type", "None") or player.gearset["ammo"].get("Skill Type", "None")
        ranged_base = self._pdif_base_cap(ranged_skill, ranged=True)
        ranged_ratio = stats.get("Ranged Attack", 0) / max(1, enemy.stats["Defense"])
        ranged_pdif_cap = (ranged_base + pdl_trait) * (1 + pdl_gear) if ranged_base else None

        sections = [
            self._quick_section("Haste and delay", [
                cap_card("Gear haste", gear_haste_raw, 0.25, percent=True),
                cap_card("Magic haste", magic_haste_raw, 448 / 1024, percent=True),
                cap_card("JA haste", ja_haste_raw, 0.25, percent=True),
                ("Total haste", self._quick_stat_value(min(source_haste, 0.25 + 0.25 + 448 / 1024), percent=True), "Combined source caps", "neutral"),
                cap_card("Delay reduction", stats.get("Delay Reduction", 0), 0.80, percent=True),
            ]),
            self._quick_section("Attributes", [(name, value(name), "", "neutral") for name in ("STR", "DEX", "VIT", "AGI", "INT", "MND", "CHR")]),
            self._quick_section("Offense", [(label, value(name), "", "neutral") for label, name in (
                ("Main accuracy", "Accuracy1"), ("Off-hand accuracy", "Accuracy2"),
                ("Main attack", "Attack1"), ("Off-hand attack", "Attack2"),
                ("Ranged accuracy", "Ranged Accuracy"), ("Ranged attack", "Ranged Attack"),
                ("Magic accuracy", "Magic Accuracy"), ("Magic attack", "Magic Attack"),
                ("Magic damage", "Magic Damage"), ("Store TP", "Store TP"),
                ("Double damage", "Double Damage"),
                ("TP Bonus", "TP Bonus"),
            )]),
            self._quick_section("Hit rate", [
                (*cap_card("Main hit rate", main_hit, main_cap, percent=True),),
                (*cap_card("Off-hand hit rate", sub_hit, sub_cap, percent=True),),
                (*cap_card("Ranged/Daken hit rate", ranged_hit, ranged_cap, percent=True),),
            ]),
            self._quick_section("pDIF / PDL", [
                ("PDL from gear", pct("PDL"), "Added to pDIF multiplier", "neutral"),
                ("PDL trait", pct("PDL Trait"), "Added to base cap", "neutral"),
                ("Main cRatio / cap", f"{main_ratio:.2f} / {main_pdif_cap:.2f}" if main_pdif_cap else "N/A", "Attack / defense; current cap", "neutral" if not main_pdif_cap else "over" if main_ratio > main_pdif_cap else "at" if main_ratio >= main_pdif_cap else "under"),
                ("Ranged cRatio / cap", f"{ranged_ratio:.2f} / {ranged_pdif_cap:.2f}" if ranged_pdif_cap else "N/A", "Attack / defense; current cap", "neutral" if not ranged_pdif_cap else "over" if ranged_ratio > ranged_pdif_cap else "at" if ranged_ratio >= ranged_pdif_cap else "under"),
            ]),
            self._quick_section("Defense", [
                cap_card("DT", stats.get("DT", 0), 50, negative=True),
                cap_card("PDT", stats.get("PDT", 0), 50, negative=True),
                cap_card("MDT", stats.get("MDT", 0), 50, negative=True),
                cap_card("Total PDT", total_pdt, 50, negative=True),
                cap_card("Total MDT", total_mdt, 50, negative=True),
                ("Evasion", value("Evasion"), "", "neutral"),
                ("Magic evasion", value("Magic Evasion"), "", "neutral"),
                ("Magic defense", value("Magic Defense"), "", "neutral"),
            ]),
            self._quick_section("Multi-attack", [
                cap_card("Double attack", stats.get("DA", 0) / 100, 1.0, percent=True),
                cap_card("Triple attack", stats.get("TA", 0) / 100, 1.0, percent=True),
                cap_card("Quadruple attack", stats.get("QA", 0) / 100, 1.0, percent=True),
            ]),
        ]
        for index, section in enumerate(sections):
            self.quick_stats_layout.addWidget(section, index, 0)
        self.quick_stats_layout.setRowStretch(len(sections), 1)

    def refresh_quick_stats(self, *_args):
        if not hasattr(self, "quick_stats_layout"):
            return
        try:
            player, enemy, _buffs, abilities = self._context()
            self._render_quick_stats(player, enemy)
            self._sync_quick_ability_controls(abilities)
        except Exception as error:
            while self.quick_stats_layout.count():
                item = self.quick_stats_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self.quick_stats_layout.addWidget(QLabel(f"Unable to calculate totals: {error}"), 0, 0)
            if hasattr(self, "quick_ability_status"):
                self.quick_ability_status.setText(f"Unable to load abilities: {error}")

    def _optimizer_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        top = QHBoxLayout()
        candidates = QGroupBox("Candidates by slot")
        grid = QGridLayout(candidates)
        self.candidate_buttons = {}
        self.candidate_detail_labels = {}
        for index, slot in enumerate(SLOTS):
            button = QPushButton("1 selected")
            button.clicked.connect(lambda _checked=False, name=slot: self.choose_candidates(name))
            row, column = divmod(index, 4)
            cell = QVBoxLayout()
            label = QLabel(slot.upper())
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.addWidget(label)
            detail = QLabel()
            detail.setWordWrap(True)
            detail.setMinimumHeight(34)
            detail.setStyleSheet("font-size: 10px; color: #667085;")
            cell.addWidget(detail)
            cell.addWidget(button)
            grid.addLayout(cell, row, column)
            self.candidate_buttons[slot] = button
            self.candidate_detail_labels[slot] = detail
        select_all = QPushButton("Select all gear in all slots")
        select_all.setToolTip("Include every available item for every optimizer slot.")
        select_all.clicked.connect(self.select_all_candidates)
        grid.addWidget(select_all, 4, 0, 1, 4)
        self.exclude_under_119 = QCheckBox("Remove items under item level 119")
        self.exclude_under_119.setToolTip(
            "Filter optimizer candidates in main, head, body, hands, legs, and feet. "
            "Items without item-level metadata are kept."
        )
        self.exclude_under_119.toggled.connect(self._candidate_filter_changed)
        grid.addWidget(self.exclude_under_119, 5, 0, 1, 4)
        self.include_shared_gear = QCheckBox("Include transferable gear from other characters")
        self.include_shared_gear.setToolTip(
            "Opt-in: add only non-Ex gear exported as transferable by GearSetBuilder "
            "from the other discovered characters."
        )
        self.include_shared_gear.toggled.connect(self._shared_gear_changed)
        grid.addWidget(self.include_shared_gear, 6, 0, 1, 4)
        preset_row = QHBoxLayout()
        self.candidate_preset_combo = QComboBox()
        self.candidate_preset_combo.setMinimumWidth(180)
        self.candidate_preset_combo.setToolTip("Save and load named optimizer candidate selections.")
        load_candidates = QPushButton("Load")
        save_candidates = QPushButton("Save")
        delete_candidates = QPushButton("Delete")
        load_candidates.clicked.connect(self.load_candidate_preset)
        save_candidates.clicked.connect(self.save_candidate_preset)
        delete_candidates.clicked.connect(self.delete_candidate_preset)
        preset_row.addWidget(QLabel("Candidate preset"))
        preset_row.addWidget(self.candidate_preset_combo, 1)
        preset_row.addWidget(load_candidates)
        preset_row.addWidget(save_candidates)
        preset_row.addWidget(delete_candidates)
        grid.addLayout(preset_row, 7, 0, 1, 4)
        candidate_column = QVBoxLayout()
        candidate_column.addWidget(candidates, 1)
        top.addLayout(candidate_column, 1)

        options = QGroupBox("Search")
        form = QFormLayout(options)
        self.optimize_action = QComboBox()
        self.optimize_action.addItems([
            "Weapon skill", "Rank weapon-type WS", "Attack round", "Spell",
            "Combined TP + WS", "Sub-stat optimization",
        ])
        self.optimize_action.setToolTip(
            "Combined TP + WS finds the best WS set first, then optimizes the TP set "
            "against that WS set using the same main/sub weapons and full-cycle DPS "
            "(TP-round damage plus WS damage, divided by TP time plus the 2-second WS delay)."
        )
        self.metric_combo = QComboBox()
        self.ranking_weapon_type = QComboBox()
        self.ranking_weapon_type.setToolTip(
            "Ranks every modeled weapon skill for this selected weapon type. "
            "The current Main/Sub/Range/Ammo setup is frozen for a fair comparison."
        )
        self.substat_base_action = QComboBox()
        self.substat_base_action.addItems([
            "Weapon skill", "Attack round", "Spell", "Combined TP + WS",
        ])
        self.substat_base_action.setToolTip(
            "Choose the damage calculation that establishes the primary result before secondary stats are optimized."
        )
        self.substat_loss_percent = QDoubleSpinBox()
        self.substat_loss_percent.setRange(0.0, 100.0)
        self.substat_loss_percent.setDecimals(1)
        self.substat_loss_percent.setSingleStep(0.5)
        self.substat_loss_percent.setValue(10.0)
        self.substat_loss_percent.setSuffix(" %")
        self.substat_loss_percent.setToolTip(
            "Maximum allowed damage loss from the best primary-damage set."
        )
        self.substat_combos = []
        for _index, default_stat in enumerate(("Magic Evasion", "Evasion", "Defense")):
            combo = QComboBox()
            combo.addItems(SUBSTAT_OPTIONS)
            combo.setCurrentText(default_stat)
            self.substat_combos.append(combo)
        self.optimize_action.currentTextChanged.connect(self._refresh_optimizer_metrics)
        self.substat_base_action.currentTextChanged.connect(
            lambda _text: self._refresh_optimizer_metrics(self.optimize_action.currentText())
        )
        self.optimize_action.currentTextChanged.connect(self._refresh_combined_options)
        self.pdt = QSpinBox()
        self.pdt.setRange(0, 50)
        self.pdt.setToolTip("Minimum physical damage reduction. 0% disables this requirement.")
        self.mdt = QSpinBox()
        self.mdt.setRange(0, 50)
        self.mdt.setToolTip("Minimum magic damage reduction. 0% disables this requirement.")
        self.dt = QSpinBox()
        self.dt.setRange(0, 50)
        self.dt.setToolTip("Minimum standalone Damage Taken reduction. 0% disables this requirement.")
        self.combined_defense_both = QCheckBox("Apply PDT / MDT / DT to WS set too")
        self.combined_defense_both.setChecked(True)
        self.combined_defense_both.setToolTip(
            "Combined TP + WS mode: when enabled, defensive minimums apply to both TP and WS sets. "
            "When disabled, only the TP set must meet them; the WS set is optimized for damage."
        )
        self.restarts = QSpinBox()
        self.restarts.setRange(1, 10)
        self.restarts.setValue(3)
        self.restarts.setToolTip(
            "Independent search runs from different starting points. More runs improve "
            "coverage but add work. Limited to 10 runs."
        )
        self.workers = QSpinBox()
        self.workers.setRange(0, max(1, os.cpu_count() or 1))
        self.workers.setToolTip("0 uses available CPU cores while leaving one free.")
        self.parallel_mode = QComboBox()
        self.parallel_mode.addItems(["Independent search runs", "Split one search run"])
        self.parallel_mode.setToolTip(
            "Independent runs use different seeded starting points. Split one run divides "
            "each seeded candidate pass among workers, then merges the best result."
        )
        self.parallel_mode.currentTextChanged.connect(self._refresh_parallel_mode)
        self.restarts.valueChanged.connect(
            lambda _value: self._refresh_parallel_mode(self.parallel_mode.currentText())
        )
        self.seed = QLineEdit()
        self.seed.setPlaceholderText("random")
        self.seed.setToolTip("Optional repeatable seed. Blank creates a new search sequence.")
        self.prune_candidates = QCheckBox("Prune dominated candidates")
        self.prune_candidates.setChecked(True)
        self.prune_candidates.setToolTip(
            "Before searching, remove only same-slot/type items that are no better "
            "on every modeled numeric combat stat. It does not change formulas; "
            "disable it for an exhaustive pass or unusual gear interactions."
        )
        form.addRow("Action", self.optimize_action)
        form.addRow("Metric", self.metric_combo)
        form.addRow("Ranking weapon type", self.ranking_weapon_type)
        form.addRow("Sub-stat damage action", self.substat_base_action)
        form.addRow("Max damage loss from best", self.substat_loss_percent)
        for index, combo in enumerate(self.substat_combos, start=1):
            form.addRow(f"Secondary stat priority {index}", combo)
        form.addRow("Minimum PDT reduction %", self.pdt)
        form.addRow("Minimum MDT reduction %", self.mdt)
        form.addRow("Minimum DT reduction %", self.dt)
        form.addRow(self.combined_defense_both)
        form.addRow("Worker mode", self.parallel_mode)
        form.addRow("Search runs", self.restarts)
        form.addRow("Parallel workers", self.workers)
        form.addRow("Optimizer seed", self.seed)
        form.addRow(self.prune_candidates)
        self.optimize_button = QPushButton("Run optimizer")
        self.optimize_button.setMinimumHeight(32)
        self.optimize_button.clicked.connect(self.run_optimizer)
        self.stop_optimizer_button = QPushButton("Stop optimizer")
        self.stop_optimizer_button.setMinimumHeight(32)
        self.stop_optimizer_button.setEnabled(False)
        self.stop_optimizer_button.setToolTip("Request a cooperative stop after the current candidate calculation.")
        self.stop_optimizer_button.clicked.connect(self.stop_optimizer)
        self.equip_best_button = QPushButton("Equip best set")
        self.equip_best_button.setMinimumHeight(32)
        self.equip_best_button.setEnabled(False)
        self.equip_best_button.clicked.connect(self.equip_best)
        run_controls = QHBoxLayout()
        run_controls.setContentsMargins(0, 0, 0, 0)
        run_controls.addWidget(self.optimize_button)
        run_controls.addWidget(self.stop_optimizer_button)
        form.addRow(run_controls)
        form.addRow(self.equip_best_button)
        top.addWidget(options)
        layout.addLayout(top)
        self.optimizer_log = QTextEdit()
        self.optimizer_log.setReadOnly(True)
        self.optimizer_log.setAcceptRichText(True)
        self.optimizer_log.setPlaceholderText("Optimizer progress appears here.")
        layout.addWidget(self.optimizer_log, 1)
        status_box = QGroupBox("Search status")
        status_grid = QGridLayout(status_box)
        status_grid.setContentsMargins(12, 12, 12, 12)
        status_grid.setHorizontalSpacing(24)
        status_grid.setVerticalSpacing(10)
        self.optimizer_progress_value = QLabel("Approx. progress: —")
        self.optimizer_eta_value = QLabel("Estimated time remaining: —")
        self.optimizer_best_value = QLabel("Best metric: —")
        self.optimizer_phase_value = QLabel("Current phase: —")
        for label in (
            self.optimizer_progress_value, self.optimizer_eta_value,
            self.optimizer_best_value, self.optimizer_phase_value,
        ):
            label.setFixedWidth(360)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        status_grid.addWidget(self.optimizer_progress_value, 0, 0)
        status_grid.addWidget(self.optimizer_eta_value, 0, 1)
        status_grid.addWidget(self.optimizer_best_value, 1, 0)
        status_grid.addWidget(self.optimizer_phase_value, 1, 1)
        layout.addWidget(status_box)
        self.optimizer_runs_box = QGroupBox("Per-run status")
        self.optimizer_runs_layout = QGridLayout(self.optimizer_runs_box)
        self.optimizer_runs_layout.setContentsMargins(8, 8, 8, 8)
        self.optimizer_runs_placeholder = QLabel(
            "Run the optimizer to show a fixed status section for each search run."
        )
        self.optimizer_runs_layout.addWidget(self.optimizer_runs_placeholder, 0, 0)
        layout.addWidget(self.optimizer_runs_box)
        self.optimizer_activity = QLabel("Idle")
        self.optimizer_activity.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.optimizer_activity)
        self.show_top_sets_button = QPushButton("Show best sets (up to 5)")
        self.show_top_sets_button.setEnabled(False)
        self.show_top_sets_button.clicked.connect(self.show_top_sets)
        layout.addWidget(self.show_top_sets_button, 0, Qt.AlignmentFlag.AlignRight)
        self._refresh_optimizer_metrics(self.optimize_action.currentText())
        self._refresh_ranking_weapon_types()
        self._refresh_combined_options(self.optimize_action.currentText())
        self._refresh_parallel_mode(self.parallel_mode.currentText())
        self._refresh_candidate_preset_names()
        for slot in SLOTS:
            self._update_candidate_button(slot)
        return tab

    def _refresh_optimizer_metrics(self, action: str):
        metrics = {
            "Weapon skill": ["Damage dealt", "TP return", "Magic accuracy"],
            "Rank weapon-type WS": ["Average damage at 1000 / 2000 / 3000 TP"],
            "Attack round": ["Time to WS", "Damage dealt", "TP return", "DPS"],
            "Spell": ["Damage dealt", "TP return"],
            "Combined TP + WS": ["Combined DPS"],
            "Sub-stat optimization": {
                "Weapon skill": ["Damage dealt", "TP return", "Magic accuracy"],
                "Attack round": ["Time to WS", "Damage dealt", "TP return", "DPS"],
                "Spell": ["Damage dealt", "TP return"],
                "Combined TP + WS": ["Combined DPS"],
            }.get(self.substat_base_action.currentText(), ["Damage dealt"]),
        }[action]
        current = self.metric_combo.currentText()
        self.metric_combo.clear()
        self.metric_combo.addItems(metrics)
        if current in metrics:
            self.metric_combo.setCurrentText(current)

    def _refresh_combined_options(self, action: str):
        self.combined_defense_both.setVisible(action == "Combined TP + WS")
        ranking = action == "Rank weapon-type WS"
        self.ranking_weapon_type.setVisible(ranking)
        label = self.ranking_weapon_type.parentWidget().layout().labelForField(
            self.ranking_weapon_type
        )
        if label is not None:
            label.setVisible(ranking)
        substats = action == "Sub-stat optimization"
        for widget in (self.substat_base_action, self.substat_loss_percent, *self.substat_combos):
            widget.setVisible(substats)
        for widget in (self.substat_base_action, self.substat_loss_percent, *self.substat_combos):
            label = self.substat_base_action.parentWidget().layout().labelForField(widget)
            if label is not None:
                label.setVisible(substats)

    def _refresh_ranking_weapon_types(self):
        if not hasattr(self, "ranking_weapon_type") or not hasattr(self, "quick_set"):
            return
        current = self.ranking_weapon_type.currentText()
        values = _ranking_weapon_types(self.quick_set.items)
        self.ranking_weapon_type.clear()
        self.ranking_weapon_type.addItems(values)
        if current in values:
            self.ranking_weapon_type.setCurrentText(current)

    def _refresh_parallel_mode(self, mode: str):
        split_one_run = mode == "Split one search run"
        self.restarts.setEnabled(not split_one_run)
        if split_one_run:
            self.restarts.setToolTip(
                "Split-worker mode uses one seed and balances slot-pair work across the "
                "requested workers. This is the mode to use when you want one search "
                "to use many cores."
            )
            self.workers.setToolTip(
                "Requested worker processes for one split search. Actual CPU use varies "
                "with candidate counts and formula cost; workers are load-balanced by "
                "estimated combinations. 0 leaves one CPU core free."
            )
        else:
            self.restarts.setToolTip(
                "Independent search runs from different starting points. More runs improve "
                "coverage but add work. At most one worker is used per run."
            )
            self.workers.setToolTip(
                f"Independent mode can use at most {self.restarts.value()} workers because "
                f"there are {self.restarts.value()} search runs. Increase Search runs or "
                "use Split one search run to use more cores. 0 leaves one core free."
            )

    def _sets_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        sets = QTabWidget()
        sets.addTab(self.tp_set, "TP set")
        sets.addTab(self.ws_set, "WS set")
        layout.addWidget(sets, 1)
        controls = QHBoxLayout()
        self.plot_dps_checkbox = QCheckBox("Plot DPS")
        self.plot_dps_checkbox.setToolTip("Show the legacy long-run DPS graph after the simulation.")
        simulate = QPushButton("Run DPS simulation")
        simulate.clicked.connect(self.run_simulation)
        distribution = QPushButton("Create WS damage distribution plot")
        distribution.setToolTip("Sample 20,000 weapon skills and show their damage distribution.")
        distribution.clicked.connect(self.plot_ws_distribution)
        controls.addWidget(self.plot_dps_checkbox)
        controls.addWidget(simulate)
        controls.addWidget(distribution)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.plot_status = QLabel("Plots use the selected TP/WS gear and enemy.")
        layout.addWidget(self.plot_status)
        return tab

    def _buffs_tab(self) -> QWidget:
        """Build the structured equivalent of the legacy Active Buffs pane."""
        tab = QWidget()
        outer = QVBoxLayout(tab)
        note = QLabel(
            "Enable only buffs currently active. These controls feed the existing "
            "calculation engine. Save named variations when you need to switch quickly."
        )
        note.setWordWrap(True)
        outer.addWidget(note)

        preset_box = QGroupBox("Buff presets")
        preset_layout = QHBoxLayout(preset_box)
        self.buff_preset_combo = QComboBox()
        self.buff_preset_combo.setMinimumWidth(220)
        self.buff_preset_combo.setToolTip(
            "Select a saved buff configuration. BG Wiki presets also set the "
            "custom test enemy values used for those sets."
        )
        load_preset = QPushButton("Load")
        save_preset = QPushButton("Save current")
        delete_preset = QPushButton("Delete")
        load_preset.clicked.connect(self.load_buff_preset)
        save_preset.clicked.connect(self.save_buff_preset)
        delete_preset.clicked.connect(self.delete_buff_preset)
        preset_layout.addWidget(QLabel("Variation"))
        preset_layout.addWidget(self.buff_preset_combo, 1)
        preset_layout.addWidget(load_preset)
        preset_layout.addWidget(save_preset)
        preset_layout.addWidget(delete_preset)
        outer.addWidget(preset_box)
        preset_note = QLabel(
            "BG Wiki presets: Mid-buff (standard songs/rolls) and High-buff "
            "(Marcato plus GEO Fury/Frailty). Both include the 1350 evasion / "
            "1500 defense / 340 VIT and AGI / 280 INT and MND test enemy."
        )
        preset_note.setWordWrap(True)
        outer.addWidget(preset_note)
        content = QWidget()
        grid = QGridLayout(content)

        whm = QGroupBox("White Magic and food")
        whm_form = QFormLayout(whm)
        self.whm_enabled = QCheckBox("Enable White Magic")
        self.shell_v = QCheckBox("Shell V")
        self.dia_combo = QComboBox()
        self.dia_combo.addItems(["None", "Dia", "Dia II", "Dia III"])
        self.haste_combo = QComboBox()
        self.haste_combo.addItems(["None", "Haste", "Haste II"])
        self.boost_combo = QComboBox()
        self.boost_combo.addItems(["None", *[f"Boost-{stat}" for stat in (
            "STR", "DEX", "VIT", "AGI", "MND", "INT", "CHR",
        )]])
        self.storm_combo = QComboBox()
        self.storm_combo.addItems(["None", *buff_data.storm_spells])
        self.enhancing_skill = QSpinBox()
        self.enhancing_skill.setRange(0, 999)
        self.enhancing_skill.setValue(500)
        self.food_combo = QComboBox()
        self.food_combo.addItems(["None", *sorted(gear.all_food)])
        whm_form.addRow(self.whm_enabled)
        whm_form.addRow(self.shell_v)
        whm_form.addRow("Dia", self.dia_combo)
        whm_form.addRow("Haste", self.haste_combo)
        whm_form.addRow("Boost", self.boost_combo)
        whm_form.addRow("Storm", self.storm_combo)
        whm_form.addRow("Enhancing skill", self.enhancing_skill)
        whm_form.addRow("Food", self.food_combo)
        grid.addWidget(whm, 0, 0)

        bard = QGroupBox("Bard songs")
        bard_form = QFormLayout(bard)
        self.bard_enabled = QCheckBox("Enable Bard songs")
        self.song_bonus = QSpinBox()
        self.song_bonus.setRange(0, 9)
        self.song_bonus.setPrefix("Songs +")
        self.song_combos = []
        bard_form.addRow(self.bard_enabled)
        bard_form.addRow("Instrument bonus", self.song_bonus)
        song_names = ["None", *buff_data.brd]
        for index in range(5):
            combo = QComboBox()
            combo.addItems(song_names)
            combo.currentTextChanged.connect(lambda _text, current=combo: self._clear_duplicate_combo(
                current, self.song_combos
            ))
            self.song_combos.append(combo)
            bard_form.addRow(f"Song {index + 1}", combo)
        self.marcato = QCheckBox("Marcato (song 1)")
        self.soul_voice = QCheckBox("Soul Voice")
        self.marcato.toggled.connect(lambda enabled: enabled and self.soul_voice.setChecked(False))
        self.soul_voice.toggled.connect(lambda enabled: enabled and self.marcato.setChecked(False))
        bard_form.addRow(self.marcato)
        bard_form.addRow(self.soul_voice)
        grid.addWidget(bard, 0, 1)

        corsair = QGroupBox("Corsair rolls")
        cor_form = QFormLayout(corsair)
        self.cor_enabled = QCheckBox("Enable Corsair rolls")
        self.roll_bonus = QSpinBox()
        self.roll_bonus.setRange(0, 8)
        self.roll_bonus.setPrefix("Rolls +")
        cor_form.addRow(self.cor_enabled)
        cor_form.addRow("Roll bonus", self.roll_bonus)
        self.roll_combos = []
        self.roll_potencies = []
        roll_names = ["None", *[f"{name} Roll" for name in buff_data.cor]]
        for index in range(4):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            potency = QComboBox()
            potency.addItems(["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI"])
            potency.setCurrentText("IX")
            combo = QComboBox()
            combo.addItems(roll_names)
            combo.currentTextChanged.connect(lambda _text, current=combo: self._clear_duplicate_combo(
                current, self.roll_combos
            ))
            row_layout.addWidget(potency)
            row_layout.addWidget(combo, 1)
            self.roll_potencies.append(potency)
            self.roll_combos.append(combo)
            cor_form.addRow(f"Roll {index + 1}", row)
        self.crooked_cards = QCheckBox("Crooked Cards (rolls 1 and 3)")
        self.cor_job_bonus = QCheckBox("COR job bonus")
        self.light_shot = QCheckBox("Light Shot on Dia")
        cor_form.addRow(self.crooked_cards)
        cor_form.addRow(self.cor_job_bonus)
        cor_form.addRow(self.light_shot)
        grid.addWidget(corsair, 1, 0)

        geo = QGroupBox("Geomancy bubbles")
        geo_form = QFormLayout(geo)
        self.geo_enabled = QCheckBox("Enable Geomancy")
        self.geo_bonus = QSpinBox()
        self.geo_bonus.setRange(0, 10)
        self.geo_bonus.setPrefix("Geomancy +")
        bubble_names = ["None", *sorted(set(buff_data.geo) | set(buff_data.geo_debuffs))]
        self.indi_combo = QComboBox()
        self.geo_combo = QComboBox()
        self.entrust_combo = QComboBox()
        for combo, prefix in ((self.indi_combo, "Indi-"), (self.geo_combo, "Geo-"),
                              (self.entrust_combo, "Entrust-")):
            combo.addItems(["None", *[prefix + name for name in bubble_names[1:]]])
            combo.currentTextChanged.connect(self._clear_duplicate_bubbles)
        self.geo_potency = QSpinBox()
        self.geo_potency.setRange(0, 100)
        self.geo_potency.setValue(100)
        self.geo_potency.setSuffix("%")
        self.blaze_of_glory = QCheckBox("Blaze of Glory (Geo only)")
        self.bolster = QCheckBox("Bolster (Indi and Geo)")
        self.blaze_of_glory.toggled.connect(lambda enabled: enabled and self.bolster.setChecked(False))
        self.bolster.toggled.connect(lambda enabled: enabled and self.blaze_of_glory.setChecked(False))
        geo_form.addRow(self.geo_enabled)
        geo_form.addRow("Geomancy bonus", self.geo_bonus)
        geo_form.addRow("Indi", self.indi_combo)
        geo_form.addRow("Geo", self.geo_combo)
        geo_form.addRow("Entrust", self.entrust_combo)
        geo_form.addRow("Debuff potency", self.geo_potency)
        geo_form.addRow(self.blaze_of_glory)
        geo_form.addRow(self.bolster)
        grid.addWidget(geo, 1, 1)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        for control in (
            self.whm_enabled, self.shell_v, self.dia_combo, self.haste_combo,
            self.boost_combo, self.storm_combo, self.enhancing_skill, self.food_combo,
            self.bard_enabled, self.song_bonus, *self.song_combos, self.marcato,
            self.soul_voice, self.cor_enabled, self.roll_bonus, *self.roll_combos,
            *self.roll_potencies, self.crooked_cards, self.cor_job_bonus,
            self.light_shot, self.geo_enabled, self.geo_bonus, self.indi_combo,
            self.geo_combo, self.entrust_combo, self.geo_potency,
            self.blaze_of_glory, self.bolster,
        ):
            if isinstance(control, QCheckBox):
                control.toggled.connect(self.refresh_quick_stats)
            elif isinstance(control, QSpinBox):
                control.valueChanged.connect(self.refresh_quick_stats)
            else:
                control.currentTextChanged.connect(self.refresh_quick_stats)
        self._refresh_buff_preset_names()
        return tab

    def _capture_buff_state(self) -> dict:
        """Return all user-editable Buffs and enemy controls as JSON data."""
        return {
            "whm_enabled": self.whm_enabled.isChecked(),
            "shell_v": self.shell_v.isChecked(),
            "dia": self.dia_combo.currentText(),
            "haste": self.haste_combo.currentText(),
            "boost": self.boost_combo.currentText(),
            "storm": self.storm_combo.currentText(),
            "enhancing_skill": self.enhancing_skill.value(),
            "food": self.food_combo.currentText(),
            "bard_enabled": self.bard_enabled.isChecked(),
            "song_bonus": self.song_bonus.value(),
            "songs": [combo.currentText() for combo in self.song_combos],
            "marcato": self.marcato.isChecked(),
            "soul_voice": self.soul_voice.isChecked(),
            "cor_enabled": self.cor_enabled.isChecked(),
            "roll_bonus": self.roll_bonus.value(),
            "rolls": [
                {"name": combo.currentText(), "potency": potency.currentText()}
                for combo, potency in zip(self.roll_combos, self.roll_potencies)
            ],
            "crooked_cards": self.crooked_cards.isChecked(),
            "cor_job_bonus": self.cor_job_bonus.isChecked(),
            "light_shot": self.light_shot.isChecked(),
            "geo_enabled": self.geo_enabled.isChecked(),
            "geo_bonus": self.geo_bonus.value(),
            "indi": self.indi_combo.currentText(),
            "geo": self.geo_combo.currentText(),
            "entrust": self.entrust_combo.currentText(),
            "geo_potency": self.geo_potency.value(),
            "blaze_of_glory": self.blaze_of_glory.isChecked(),
            "bolster": self.bolster.isChecked(),
            "additional_buffs_json": (
                self.buffs_json.toPlainText() if hasattr(self, "buffs_json") else "{}"
            ),
            "abilities_json": (
                self.abilities_json.toPlainText() if hasattr(self, "abilities_json") else "{}"
            ),
            "enemy_preset": self.enemy_combo.currentText(),
            "enemy": {name: spin.value() for name, spin in self.enemy_spins.items()},
        }

    def _builtin_buff_presets(self) -> dict[str, dict]:
        """Build the documented BG Wiki variations without changing engine rules."""
        base = self._capture_buff_state()
        enemy = dict(base["enemy"])
        bg_enemy = enemies.preset_enemies.get("BG Wiki sets", {})
        for stat in self.enemy_spins:
            if stat in bg_enemy:
                enemy[stat] = int(bg_enemy[stat])

        def preset(high_buff: bool) -> dict:
            value = copy.deepcopy(base)
            value.update({
                "whm_enabled": True,
                "shell_v": True,
                "dia": "Dia II",
                "haste": "Haste",
                "boost": "None",
                "storm": "None",
                "enhancing_skill": 500,
                "food": "Grape Daifuku",
                "bard_enabled": True,
                "song_bonus": 7,
                "songs": ["Honor March", "Victory March", "Minuet V", "Minuet IV", "None"],
                "marcato": high_buff,
                "soul_voice": False,
                "cor_enabled": True,
                "roll_bonus": 7,
                "rolls": [
                    {"name": "Chaos Roll", "potency": "X"},
                    {"name": "Samurai Roll", "potency": "IX"},
                    {"name": "None", "potency": "IX"},
                    {"name": "None", "potency": "IX"},
                ],
                "crooked_cards": True,
                "cor_job_bonus": True,
                "light_shot": True,
                "geo_enabled": high_buff,
                "geo_bonus": 10,
                "indi": "Indi-Fury" if high_buff else "None",
                "geo": "Geo-Frailty" if high_buff else "None",
                "entrust": "None",
                "geo_potency": 20,
                "blaze_of_glory": high_buff,
                "bolster": False,
                "additional_buffs_json": "{}",
                "abilities_json": "{}",
                "enemy_preset": "BG Wiki sets",
                "enemy": enemy,
            })
            return value

        return {
            "BG Wiki Mid-buff": preset(False),
            "BG Wiki High-buff": preset(True),
        }

    def _load_saved_buff_presets(self) -> dict[str, dict]:
        raw = self.settings.value("buff_presets", "")
        if isinstance(raw, dict):
            data = raw
        else:
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", errors="replace")
            try:
                data = json.loads(str(raw or "{}"))
            except (TypeError, ValueError):
                return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(name): value for name, value in data.items()
            if str(name).strip() and isinstance(value, dict)
        }

    def _all_buff_presets(self) -> dict[str, dict]:
        presets = self._builtin_buff_presets()
        builtins = set(presets)
        presets.update({
            name: value for name, value in self._load_saved_buff_presets().items()
            if name not in builtins
        })
        return presets

    def _refresh_buff_preset_names(self):
        if not hasattr(self, "buff_preset_combo"):
            return
        current = self.buff_preset_combo.currentText()
        presets = self._all_buff_presets()
        self.buff_preset_combo.blockSignals(True)
        self.buff_preset_combo.clear()
        self.buff_preset_combo.addItems(list(presets))
        if current in presets:
            self.buff_preset_combo.setCurrentText(current)
        self.buff_preset_combo.blockSignals(False)

    @staticmethod
    def _set_combo_value(combo: QComboBox, value, fallback: str = "None"):
        value = str(value) if value is not None else fallback
        index = combo.findText(value)
        combo.setCurrentIndex(index if index >= 0 else combo.findText(fallback))

    def _apply_buff_state(self, state: dict):
        """Apply a saved preset while suppressing intermediate recalculations."""
        controls = [
            self.whm_enabled, self.shell_v, self.dia_combo, self.haste_combo,
            self.boost_combo, self.storm_combo, self.enhancing_skill, self.food_combo,
            self.bard_enabled, self.song_bonus, *self.song_combos, self.marcato,
            self.soul_voice, self.cor_enabled, self.roll_bonus, *self.roll_combos,
            *self.roll_potencies, self.crooked_cards, self.cor_job_bonus,
            self.light_shot, self.geo_enabled, self.geo_bonus, self.indi_combo,
            self.geo_combo, self.entrust_combo, self.geo_potency,
            self.blaze_of_glory, self.bolster,
        ]
        for control in controls:
            control.blockSignals(True)
        self.enemy_combo.blockSignals(True)
        try:
            for control, key in (
                (self.whm_enabled, "whm_enabled"), (self.shell_v, "shell_v"),
                (self.bard_enabled, "bard_enabled"), (self.marcato, "marcato"),
                (self.soul_voice, "soul_voice"), (self.cor_enabled, "cor_enabled"),
                (self.crooked_cards, "crooked_cards"), (self.cor_job_bonus, "cor_job_bonus"),
                (self.light_shot, "light_shot"), (self.geo_enabled, "geo_enabled"),
                (self.blaze_of_glory, "blaze_of_glory"), (self.bolster, "bolster"),
            ):
                control.setChecked(bool(state.get(key, False)))
            for combo, key in (
                (self.dia_combo, "dia"), (self.haste_combo, "haste"),
                (self.boost_combo, "boost"), (self.storm_combo, "storm"),
                (self.food_combo, "food"), (self.indi_combo, "indi"),
                (self.geo_combo, "geo"), (self.entrust_combo, "entrust"),
            ):
                self._set_combo_value(combo, state.get(key))
            for spin, key in (
                (self.enhancing_skill, "enhancing_skill"), (self.song_bonus, "song_bonus"),
                (self.roll_bonus, "roll_bonus"), (self.geo_bonus, "geo_bonus"),
                (self.geo_potency, "geo_potency"),
            ):
                try:
                    spin.setValue(int(state.get(key, spin.value())))
                except (TypeError, ValueError):
                    pass
            songs = state.get("songs", [])
            for combo, value in zip(self.song_combos, songs):
                self._set_combo_value(combo, value)
            rolls = state.get("rolls", [])
            for index, (combo, potency) in enumerate(zip(self.roll_combos, self.roll_potencies)):
                entry = rolls[index] if index < len(rolls) and isinstance(rolls[index], dict) else {}
                self._set_combo_value(combo, entry.get("name"))
                self._set_combo_value(potency, entry.get("potency"), "IX")
            enemy_preset = str(state.get("enemy_preset", ""))
            if enemy_preset in enemies.preset_enemies:
                self.enemy_combo.setCurrentText(enemy_preset)
            enemy_values = state.get("enemy", {})
            if not isinstance(enemy_values, dict):
                enemy_values = {}
            for name, spin in self.enemy_spins.items():
                try:
                    spin.setValue(int(enemy_values.get(name, spin.value())))
                except (TypeError, ValueError):
                    pass
            if hasattr(self, "buffs_json"):
                self.buffs_json.setPlainText(str(state.get("additional_buffs_json", "{}")))
            if hasattr(self, "abilities_json"):
                self.abilities_json.setPlainText(str(state.get("abilities_json", "{}")))
        finally:
            self.enemy_combo.blockSignals(False)
            for control in controls:
                control.blockSignals(False)
        self.refresh_quick_stats()

    def load_buff_preset(self):
        name = self.buff_preset_combo.currentText().strip()
        state = self._all_buff_presets().get(name)
        if not state:
            return
        self._apply_buff_state(state)
        self.statusBar().showMessage(f"Loaded buff preset: {name}", 5000)

    def save_buff_preset(self):
        name, accepted = QInputDialog.getText(self, "Save buff preset", "Preset name:")
        name = name.strip()
        if not accepted or not name:
            return
        builtin_names = set(self._builtin_buff_presets())
        if name in builtin_names:
            QMessageBox.information(self, "Buff presets", "Built-in BG Wiki presets cannot be overwritten.")
            return
        saved = self._load_saved_buff_presets()
        if name in saved:
            answer = QMessageBox.question(
                self, "Overwrite buff preset", f"Replace saved preset '{name}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        saved[name] = self._capture_buff_state()
        self.settings.setValue("buff_presets", json.dumps(saved, sort_keys=True))
        self._refresh_buff_preset_names()
        self.buff_preset_combo.setCurrentText(name)
        self.statusBar().showMessage(f"Saved buff preset: {name}", 5000)

    def delete_buff_preset(self):
        name = self.buff_preset_combo.currentText().strip()
        if not name or name in self._builtin_buff_presets():
            QMessageBox.information(self, "Buff presets", "Built-in BG Wiki presets cannot be deleted.")
            return
        saved = self._load_saved_buff_presets()
        if name not in saved:
            return
        answer = QMessageBox.question(
            self, "Delete buff preset", f"Delete saved preset '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        del saved[name]
        self.settings.setValue("buff_presets", json.dumps(saved, sort_keys=True))
        self._refresh_buff_preset_names()
        self.statusBar().showMessage(f"Deleted buff preset: {name}", 5000)

    @staticmethod
    def _clear_duplicate_combo(current: QComboBox, combos: list[QComboBox]):
        value = current.currentText()
        if value == "None":
            return
        for combo in combos:
            if combo is not current and combo.currentText() == value:
                combo.setCurrentText("None")

    def _clear_duplicate_bubbles(self, _value: str):
        combos = (self.indi_combo, self.geo_combo, self.entrust_combo)
        selected: set[str] = set()
        for combo in combos:
            value = combo.currentText().split("-", 1)[-1]
            if value == "None":
                continue
            if value in selected:
                combo.setCurrentText("None")
            else:
                selected.add(value)

    def _profile_report_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        controls = QHBoxLayout()
        self.profile_job_combo = QComboBox()
        self.profile_job_combo.currentTextChanged.connect(self._populate_profile_report)
        refresh = QPushButton("Refresh profile sets")
        refresh.clicked.connect(self.refresh_bridge)
        run = QPushButton("Run profile report")
        run.clicked.connect(self.run_profile_report)
        self.cancel_profile_report_button = QPushButton("Cancel")
        self.cancel_profile_report_button.setEnabled(False)
        self.cancel_profile_report_button.clicked.connect(self.stop_profile_report)
        controls.addWidget(QLabel("LuAshitacast job:"))
        controls.addWidget(self.profile_job_combo)
        controls.addWidget(refresh)
        controls.addWidget(run)
        controls.addWidget(self.cancel_profile_report_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.profile_report_status = QLabel(
            "Load a character bridge to inspect its LuAshitacast profiles."
        )
        self.profile_report_status.setWordWrap(True)
        layout.addWidget(self.profile_report_status)
        weapon_sets = QGroupBox("Profile weapon overlays")
        weapon_form = QFormLayout(weapon_sets)
        self.report_main_weapon_combo = QComboBox()
        self.report_ranged_weapon_combo = QComboBox()
        self.report_defense_combo = QComboBox()
        self.report_main_weapon_combo.currentIndexChanged.connect(self._profile_weapon_changed)
        self.report_ranged_weapon_combo.currentIndexChanged.connect(self._profile_weapon_changed)
        self.report_defense_combo.currentIndexChanged.connect(self._profile_defense_changed)
        weapon_form.addRow("Main / sub weapon set", self.report_main_weapon_combo)
        weapon_form.addRow("Ranged / ammo set", self.report_ranged_weapon_combo)
        weapon_form.addRow("Optional TP defense overlay", self.report_defense_combo)
        weapon_note = QLabel(
            "Use these when the profile equips armor and weapons in separate sets. "
            "Only explicitly listed weapon slots replace the armor set."
        )
        weapon_note.setWordWrap(True)
        weapon_form.addRow(weapon_note)
        layout.addWidget(weapon_sets)

        self.profile_report_table = QTableWidget(0, 9)
        self.profile_report_table.setHorizontalHeaderLabels(
            ["Effective set", "Role", "Weapon skill", "Weapons / layers",
             "TP DPS", "TP time (s)", "WS damage", "Cycle DPS", "Status"]
        )
        self.profile_report_table.setAlternatingRowColors(True)
        self.profile_report_table.setSortingEnabled(True)
        self.profile_report_table.horizontalHeader().setStretchLastSection(True)
        self.profile_report_table.setMinimumHeight(230)
        self.profile_report_table.itemSelectionChanged.connect(self._profile_report_row_selected)
        self.profile_report_summary = QLabel(
            "Run the profile report to compare TP speed, WS damage, and full-cycle DPS."
        )
        self.profile_report_summary.setWordWrap(True)
        self.profile_report_summary.setObjectName("sectionTitle")
        layout.addWidget(self.profile_report_summary)
        report_note = QLabel(
            "TP DPS is damage during the TP phase. Cycle DPS includes TP time, WS damage, "
            "and the weapon-skill delay. Sort by a column to compare sets; blocked rows "
            "remain visible with their reason in Status."
        )
        report_note.setWordWrap(True)
        layout.addWidget(report_note)
        self.profile_diagnostic_table = QTableWidget(0, 6)
        self.profile_diagnostic_table.setHorizontalHeaderLabels(
            ["Raw set", "Role", "Variant", "Slots", "Model status", "Notes"]
        )
        self.profile_diagnostic_table.setAlternatingRowColors(True)
        self.profile_diagnostic_table.horizontalHeader().setStretchLastSection(True)
        report_views = QTabWidget()
        report_views.addTab(self.profile_report_table, "Combat results")
        report_views.addTab(self.profile_diagnostic_table, "Raw-set diagnostics")
        layout.addWidget(report_views, 1)

        selected = QGroupBox("Selected TP + WS total DPS")
        selected_form = QFormLayout(selected)
        self.report_tp_combo = QComboBox()
        self.report_ws_set_combo = QComboBox()
        self.report_ws_name_combo = QComboBox()
        self.report_ws_name_combo.setEditable(True)
        self.report_tp_combo.currentTextChanged.connect(self._refresh_selected_report_sets)
        self.report_ws_set_combo.currentTextChanged.connect(self._refresh_selected_report_sets)
        selected_form.addRow("Effective TP set", self.report_tp_combo)
        selected_form.addRow("WS set", self.report_ws_set_combo)
        selected_form.addRow("Weapon skill", self.report_ws_name_combo)
        selected_button = QPushButton("Calculate selected total DPS")
        selected_button.clicked.connect(self.run_selected_profile_report)
        selected_form.addRow(selected_button)
        self.publish_lac_button = QPushButton("Publish latest combined optimizer pair as WSDist mode")
        self.publish_lac_button.clicked.connect(self.publish_optimizer_pair_to_lac)
        selected_form.addRow(self.publish_lac_button)
        rename_button = QPushButton("Preview guided canonical set-name migration")
        rename_button.clicked.connect(self.preview_canonical_lac_migration)
        selected_form.addRow(rename_button)
        self.selected_report_result = QLabel("Choose a TP set and WS set, then calculate.")
        self.selected_report_result.setWordWrap(True)
        selected_form.addRow(self.selected_report_result)
        layout.addWidget(selected)
        return tab

    def _confirm_profile_diff(self, title: str, diff_text: str) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(900, 650)
        layout = QVBoxLayout(dialog)
        note = QLabel(
            "Review the exact LuAshitacast changes. Saving creates one timestamped backup "
            "and is refused if the profile changed after import."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        preview = QPlainTextEdit(diff_text)
        preview.setReadOnly(True)
        layout.addWidget(preview, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def publish_optimizer_pair_to_lac(self):
        profile = self._profile_for_job()
        if profile is None or self.bridge_store.bridge_path is None:
            QMessageBox.information(self, "LAC integration", "Load a character-specific LAC profile first.")
            return
        if (
            self._last_completed_optimizer_action != "Combined TP + WS"
            or self.best_tp_player is None
            or self.best_ws_player is None
        ):
            QMessageBox.information(
                self, "LAC integration",
                "Run Combined TP + WS optimization before publishing a managed pair.",
            )
            return
        ws_payload = self.report_ws_set_combo.currentData()
        ws_name = self.report_ws_name_combo.currentText().strip() or self.ws_combo.currentText().strip()
        if not ws_payload or not ws_name:
            QMessageBox.information(self, "LAC integration", "Select the matching LAC WS family first.")
            return
        descriptor = ws_payload.get("descriptor") or {}
        family = str(descriptor.get("family") or "").strip()
        if not family or descriptor.get("role") != "ws":
            QMessageBox.critical(
                self, "LAC integration",
                "The selected WS set has no safe dynamic family prefix for a WSDist variant.",
            )
            return
        managed = {
            "Tp_WSDist": {slot: self.best_tp_player.gearset[slot] for slot in ARMOR_SLOTS},
            f"{family}_WSDist": {slot: self.best_ws_player.gearset[slot] for slot in ARMOR_SLOTS},
        }
        try:
            job = str(profile.get("job") or "").upper()
            path = self.bridge_store.profile_path(job)
            source = path.read_text(encoding="utf-8")
            updated = prepare_managed_update(source, managed)
            diff = "".join(difflib.unified_diff(
                source.splitlines(keepends=True), updated.splitlines(keepends=True),
                fromfile=f"{path.name} (current)", tofile=f"{path.name} (WSDist)",
            ))
            if not self._confirm_profile_diff("Publish optimized LAC pair", diff):
                return
            backup, new_hash = write_managed_sets(
                path, managed, expected_hash=str(profile.get("source_hash") or "")
            )
            profile["source_hash"] = new_hash
            request = {
                "schema_version": 2,
                "character_key": (self.bridge_store.data.get("character") or {}).get("key"),
                "job": job, "profile": path.name, "profile_hash": new_hash,
                "set": f"Tp_WSDist + {family}_WSDist", "weapon_skill": ws_name,
            }
            write_reload_request(self.bridge_store.bridge_path.parent, request)
            QMessageBox.information(
                self, "LAC integration",
                f"Published Tp_WSDist and {family}_WSDist to {path.name}.\n"
                f"Backup: {backup.name}\nGearSetBuilder will validate and reload the active job.",
            )
        except Exception as error:
            QMessageBox.critical(self, "LAC integration", str(error))

    @staticmethod
    def _canonical_lac_set_name(payload: dict) -> str | None:
        descriptor = payload.get("descriptor") or {}
        role = descriptor.get("role")
        variant = str(descriptor.get("variant") or "Default")
        modifiers = [re.sub(r"[^A-Za-z0-9]+", "", str(value))
                     for value in descriptor.get("modifiers") or ()]
        if role == "tp":
            family = "Tp"
        elif role == "ws_base":
            family = "Ws"
        elif role == "ws" and descriptor.get("ws_name"):
            family = re.sub(r"[^A-Za-z0-9]+", "", str(descriptor["ws_name"]))
        else:
            return None
        return "_".join([family, variant, *filter(None, modifiers)])

    def preview_canonical_lac_migration(self):
        profile = self._profile_for_job()
        if profile is None or self.bridge_store.bridge_path is None:
            QMessageBox.information(self, "LAC naming", "Load a character-specific profile first.")
            return
        renames = {}
        for payload in self._profile_payloads():
            canonical = self._canonical_lac_set_name(payload)
            if canonical and canonical != payload["name"]:
                renames[payload["name"]] = canonical
        if not renames:
            QMessageBox.information(self, "LAC naming", "This profile already uses the canonical combat-set convention.")
            return
        try:
            job = str(profile.get("job") or "").upper()
            path = self.bridge_store.profile_path(job)
            source = path.read_text(encoding="utf-8")
            updated = prepare_set_renames(source, renames)
            diff = "".join(difflib.unified_diff(
                source.splitlines(keepends=True), updated.splitlines(keepends=True),
                fromfile=f"{path.name} (current)", tofile=f"{path.name} (canonical names)",
            ))
            if not self._confirm_profile_diff("Guided LAC naming migration", diff):
                return
            backup, new_hash = write_renamed_profile(
                path, renames, expected_hash=str(profile.get("source_hash") or "")
            )
            profile["source_hash"] = new_hash
            write_reload_request(self.bridge_store.bridge_path.parent, {
                "schema_version": 2,
                "character_key": (self.bridge_store.data.get("character") or {}).get("key"),
                "job": job, "profile": path.name, "profile_hash": new_hash,
                "set": "canonical naming migration",
            })
            QMessageBox.information(
                self, "LAC naming",
                f"Renamed {len(renames)} combat sets in {path.name}.\nBackup: {backup.name}",
            )
        except Exception as error:
            QMessageBox.critical(self, "LAC naming", str(error))

    def _profile_for_job(self) -> dict | None:
        selected = self.profile_job_combo.currentText().casefold()
        selected_code = JOBS.get(self.profile_job_combo.currentText(), self.profile_job_combo.currentText()).casefold()
        for profile in self.bridge_store.profile_records():
            job = str(profile.get("job", ""))
            if job.casefold() in {selected, selected_code}:
                return profile
        return None

    def _profile_payloads(self) -> list[dict]:
        profile = self._profile_for_job()
        if profile is None:
            return []
        profile_job = str(profile.get("job") or "").casefold()
        payloads = []
        for profile_set in profile.get("sets", []):
            name = str(profile_set.get("name") or "Unnamed")
            descriptor = _profile_set_descriptor(name, profile_set.get("descriptor"))
            category = "TP" if descriptor["role"] == "tp" else "DT" if descriptor["role"] == "defense" else None
            ws_name = descriptor["ws_name"]
            gearset = {slot: gear.Empty for slot in SLOTS}
            specified_slots = set()
            missing = []
            incomplete = []
            ineligible = []
            for profile_slot, item_ref in (profile_set.get("slots") or {}).items():
                slot = PROFILE_SLOT_MAP.get(str(profile_slot).casefold())
                if slot is None or not item_ref:
                    continue
                specified_slots.add(slot)
                item = self.bridge_store.resolve_profile_item(item_ref)
                if item is None:
                    item_name_text = str(item_ref.get("name") or item_ref.get("Name") or "")
                    item = next(
                        (candidate for candidate in gear.all_gear.values()
                         if str(candidate.get("Name") or "").casefold() == item_name_text.casefold()),
                        None,
                    )
                if item is None:
                    missing.append(str(profile_slot))
                elif _blacklist_matches(item, self.gear_blacklist):
                    missing.append(f"{profile_slot} (blacklisted)")
                else:
                    gearset[slot] = item
                    if item.get("Model Complete") is False:
                        incomplete.append(str(profile_slot))
                    jobs = {str(value).casefold() for value in item.get("Jobs", ())}
                    if jobs and profile_job not in jobs:
                        ineligible.append(str(profile_slot))
            payloads.append({
                "name": name, "category": category, "ws_name": ws_name,
                "gearset": gearset, "specified_slots": specified_slots,
                "missing": missing, "incomplete": incomplete, "ineligible": ineligible,
                "descriptor": descriptor,
                "unknown": list(profile_set.get("unknown") or ()),
                "cap_report": copy.deepcopy(profile_set.get("cap_report") or {}),
            })
        return payloads

    def _effective_profile_payloads(self) -> list[dict]:
        raw = self._profile_payloads()
        defense = self.report_defense_combo.currentData() if hasattr(self, "report_defense_combo") else None
        effective = _compose_profile_payloads(raw, defense)
        return [
            _with_weapon_overlays(
                payload, self.report_main_weapon_combo.currentData(),
                self.report_ranged_weapon_combo.currentData(),
            )
            for payload in effective
        ]

    def _populate_profile_report(self, *_args):
        if not hasattr(self, "profile_job_combo"):
            return
        profiles = self.bridge_store.profile_records() if self.bridge_store.data else []
        jobs = []
        for profile in profiles:
            code = str(profile.get("job", "")).upper()
            label = next((name for name, value in JOBS.items() if value.upper() == code), code)
            if label and label not in jobs:
                jobs.append(label)
        current = self.profile_job_combo.currentText()
        self.profile_job_combo.blockSignals(True)
        self.profile_job_combo.clear()
        self.profile_job_combo.addItems(sorted(jobs))
        if current in jobs:
            self.profile_job_combo.setCurrentText(current)
        elif self.main_job.currentText() in jobs:
            self.profile_job_combo.setCurrentText(self.main_job.currentText())
        self.profile_job_combo.blockSignals(False)
        payloads = self._profile_payloads()
        self.report_tp_combo.blockSignals(True)
        self.report_ws_set_combo.blockSignals(True)
        self.report_main_weapon_combo.blockSignals(True)
        self.report_ranged_weapon_combo.blockSignals(True)
        self.report_defense_combo.blockSignals(True)
        current_main_weapon = self.report_main_weapon_combo.currentText()
        current_ranged_weapon = self.report_ranged_weapon_combo.currentText()
        current_defense = self.report_defense_combo.currentText()
        self.report_tp_combo.clear()
        self.report_ws_set_combo.clear()
        self.report_main_weapon_combo.clear()
        self.report_ranged_weapon_combo.clear()
        self.report_defense_combo.clear()
        self.report_main_weapon_combo.addItem("None (use gear set as listed)", None)
        self.report_ranged_weapon_combo.addItem("None (use gear set as listed)", None)
        self.report_defense_combo.addItem("None", None)
        for payload in payloads:
            if payload["descriptor"]["role"] == "weapon" and set(payload["specified_slots"]) & set(MAIN_WEAPON_SLOTS):
                self.report_main_weapon_combo.addItem(payload["name"], payload)
            if payload["descriptor"]["role"] == "weapon" and set(payload["specified_slots"]) & set(RANGED_WEAPON_SLOTS):
                self.report_ranged_weapon_combo.addItem(payload["name"], payload)
            if payload["descriptor"]["role"] == "defense":
                self.report_defense_combo.addItem(payload["name"], payload)
        if current_main_weapon:
            self.report_main_weapon_combo.setCurrentText(current_main_weapon)
        elif self.report_main_weapon_combo.count() > 1:
            self.report_main_weapon_combo.setCurrentIndex(1)
        if current_ranged_weapon:
            self.report_ranged_weapon_combo.setCurrentText(current_ranged_weapon)
        elif self.report_ranged_weapon_combo.count() > 1:
            self.report_ranged_weapon_combo.setCurrentIndex(1)
        if current_defense:
            self.report_defense_combo.setCurrentText(current_defense)
        self.report_tp_combo.blockSignals(False)
        self.report_ws_set_combo.blockSignals(False)
        self.report_main_weapon_combo.blockSignals(False)
        self.report_ranged_weapon_combo.blockSignals(False)
        self.report_defense_combo.blockSignals(False)
        effective = self._effective_profile_payloads()
        for payload in effective:
            if payload["descriptor"]["role"] == "tp":
                self.report_tp_combo.addItem(payload["name"], payload)
            elif payload["descriptor"]["role"] == "ws":
                self.report_ws_set_combo.addItem(payload["name"], payload)
        self._populate_profile_diagnostics(payloads)
        self._refresh_selected_report_sets()
        reportable_count = len(effective)
        bridge_errors = list(self.bridge_store.data.get("profile_errors") or ())
        error_suffix = f" {len(bridge_errors)} exporter profile error(s) also require attention." if bridge_errors else ""
        if reportable_count:
            self.profile_report_status.setText(
                f"Built {reportable_count} effective TP/WS configurations from {len(payloads)} raw LAC sets."
                + error_suffix
            )
        else:
            self.profile_report_status.setText(
                "No readable TP or named WS configurations found for this profile." + error_suffix
            )

    def _populate_profile_diagnostics(self, payloads: list[dict]):
        if not hasattr(self, "profile_diagnostic_table"):
            return
        table = self.profile_diagnostic_table
        table.setRowCount(0)
        for payload in payloads:
            row = table.rowCount()
            table.insertRow(row)
            descriptor = payload["descriptor"]
            problems = []
            if payload["missing"]:
                problems.append("unresolved: " + ", ".join(payload["missing"]))
            if payload["incomplete"]:
                problems.append("incomplete model: " + ", ".join(payload["incomplete"]))
            if payload.get("ineligible"):
                problems.append("job-ineligible: " + ", ".join(payload["ineligible"]))
            if payload.get("unknown"):
                problems.append("unknown stats: " + ", ".join(map(str, payload["unknown"])))
            status = "Blocked" if problems else "Ready"
            notes = "; ".join(problems) or (
                "Composed layer" if descriptor["role"] in {"tp", "ws", "ws_base"}
                else "Diagnostic only"
            )
            values = [
                payload["name"], descriptor["role"], descriptor["variant"],
                str(len(payload["specified_slots"])), status, notes,
            ]
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        selected_job = self.profile_job_combo.currentText().upper()
        for bridge_error in self.bridge_store.data.get("profile_errors") or ():
            error_job = str(bridge_error.get("job") or "").upper()
            if error_job and selected_job and error_job != selected_job:
                continue
            row = table.rowCount()
            table.insertRow(row)
            values = [
                "<profile load>", "error", "", "0", "Blocked",
                str(bridge_error.get("error") or "Profile export failed"),
            ]
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))

    def _refresh_selected_report_sets(self, *_args):
        payload = self.report_ws_set_combo.currentData()
        self.report_ws_name_combo.blockSignals(True)
        self.report_ws_name_combo.clear()
        self.report_ws_name_combo.addItems(ALL_WS_NAMES)
        if payload and payload.get("ws_name"):
            self.report_ws_name_combo.setCurrentText(payload["ws_name"])
        self.report_ws_name_combo.blockSignals(False)

    def _effective_profile_payload(self, payload: dict | None) -> dict | None:
        if not payload:
            return None
        return _with_weapon_overlays(
            payload, self.report_main_weapon_combo.currentData(),
            self.report_ranged_weapon_combo.currentData(),
        )

    def _profile_weapon_changed(self, *_args):
        self._populate_profile_report()
        if self.profile_job_combo.currentText():
            self.profile_report_status.setText(
                "Weapon overlay changed. Run the report again to evaluate the selected setup."
            )

    def _profile_defense_changed(self, *_args):
        self._populate_profile_report()
        if self.profile_job_combo.currentText():
            self.profile_report_status.setText(
                "Defense overlay changed. Effective TP configurations were rebuilt."
            )

    @staticmethod
    def _add_stats(target: dict, values: dict, multiplier: float = 1.0):
        for stat, value in values.items():
            target[stat] = target.get(stat, 0) + multiplier * value

    def _structured_buffs(self) -> tuple[dict, dict]:
        """Translate the Buffs tab to the legacy engine's source dictionaries."""
        sources = {"brd": {}, "cor": {}, "geo": {}, "whm": {}, "food": {}}
        debuffs: dict[str, float] = {}

        if self.bard_enabled.isChecked():
            bonus = self.song_bonus.value()
            soul_voice = 2.0 if self.soul_voice.isChecked() else 1.0
            for index, combo in enumerate(self.song_combos):
                name = combo.currentText()
                if name not in buff_data.brd:
                    continue
                marcato = 1.5 if index == 0 and self.marcato.isChecked() else 1.0
                limit = buff_data.brd_song_limits[name]
                for stat, values in buff_data.brd[name].items():
                    amount = soul_voice * marcato * (values[0] + min(limit, bonus) * values[1])
                    if "minuet" in name.casefold() and stat in {"Attack", "Ranged Attack"}:
                        amount += 20
                    sources["brd"][stat] = sources["brd"].get(stat, 0) + amount
            for stat in ("Attack", "Ranged Attack"):
                if stat in sources["brd"]:
                    sources["brd"][stat] = int(sources["brd"][stat])

        if self.cor_enabled.isChecked():
            bonus = self.roll_bonus.value()
            for index, (combo, potency) in enumerate(zip(self.roll_combos, self.roll_potencies)):
                name = combo.currentText().removesuffix(" Roll")
                if name not in buff_data.cor:
                    continue
                crooked = 1.2 if index in (0, 2) and self.crooked_cards.isChecked() else 1.0
                for stat, values in buff_data.cor[name].items():
                    job_bonus = values[2] if self.cor_job_bonus.isChecked() else 0
                    amount = crooked * (values[0][potency.currentText()] + bonus * values[1] + job_bonus)
                    sources["cor"][stat] = sources["cor"].get(stat, 0) + amount

        if self.geo_enabled.isChecked():
            for combo, slot in ((self.indi_combo, "indi"), (self.geo_combo, "geo"),
                                (self.entrust_combo, "entrust")):
                name = combo.currentText().split("-", 1)[-1]
                if name == "None":
                    continue
                bonus = self.geo_bonus.value() if slot != "entrust" else 0
                bolster = 2.0 if slot != "entrust" and self.bolster.isChecked() else 1.0
                blaze = 1.5 if slot == "geo" and self.blaze_of_glory.isChecked() else 1.0
                multiplier = bolster * blaze
                if name in buff_data.geo:
                    for stat, values in buff_data.geo[name].items():
                        amount = multiplier * (values[0] + bonus * values[1])
                        sources["geo"][stat] = sources["geo"].get(stat, 0) + amount
                if name in buff_data.geo_debuffs:
                    for stat, values in buff_data.geo_debuffs[name].items():
                        amount = multiplier * (values[0] + bonus * values[1])
                        amount *= self.geo_potency.value() / 100
                        debuffs[stat] = debuffs.get(stat, 0) + amount

        if self.whm_enabled.isChecked():
            for name in (self.dia_combo.currentText(), self.haste_combo.currentText(),
                         self.boost_combo.currentText(), self.storm_combo.currentText()):
                if name in buff_data.whm:
                    self._add_stats(sources["whm"], buff_data.whm[name])
                if name in buff_data.whm_debuffs:
                    self._add_stats(debuffs, buff_data.whm_debuffs[name])
            if self.shell_v.isChecked():
                self._add_stats(sources["whm"], buff_data.whm["Shell V"])
            if self.cor_enabled.isChecked() and self.light_shot.isChecked() and self.dia_combo.currentText() in buff_data.whm_debuffs:
                self._add_stats(debuffs, buff_data.cor_debuffs["Light Shot"])

        food_name = self.food_combo.currentText()
        if food_name in gear.all_food:
            for stat, value in gear.all_food[food_name].items():
                if stat not in {"Name", "Name2", "Type"}:
                    key = "Food Attack" if stat == "Attack" else "Food Ranged Attack" if stat == "Ranged Attack" else stat
                    sources["food"][key] = sources["food"].get(key, 0) + value
        return sources, debuffs

    @staticmethod
    def _merge_buff_sources(structured: dict, custom: dict) -> dict:
        merged = {source: dict(values) for source, values in structured.items()}
        for source, values in custom.items():
            if not isinstance(values, dict):
                raise ValueError("Each additional buff source must be a JSON object of stats.")
            target = merged.setdefault(str(source), {})
            for stat, value in values.items():
                if not isinstance(value, (int, float)):
                    raise ValueError(f"Additional buff {source}.{stat} must be numeric.")
                target[stat] = target.get(stat, 0) + value
        return merged

    def _report_context(self, use_profile_job: bool = False) -> dict:
        structured, debuffs = self._structured_buffs()
        custom_buffs = self._json_object(self.buffs_json, "Additional buffs") if hasattr(self, "buffs_json") else {}
        abilities = self._json_object(self.abilities_json, "Abilities") if hasattr(self, "abilities_json") else {}
        buffs = self._merge_buff_sources(structured, custom_buffs)
        abilities["Aftermath"] = self.aftermath.value()
        abilities.setdefault("Enhancing Skill", self.enhancing_skill.value())
        abilities.setdefault("Storm spell", self.storm_combo.currentText() if self.whm_enabled.isChecked() else "None")
        abilities.setdefault("Enemy Resist Rank", "100%")
        abilities.setdefault("99999", False)
        main_job_name = self.profile_job_combo.currentText() if use_profile_job else self.main_job.currentText()
        main_job = JOBS[main_job_name]
        master_level = self.master_level.value()
        if use_profile_job:
            levels = ((self.bridge_store.data.get("character") or {}).get("metadata") or {}).get("master_levels") or {}
            try:
                master_level = max(0, min(50, int(levels.get(main_job, master_level))))
            except (TypeError, ValueError):
                pass
        return {
            "main_job": main_job,
            "sub_job": JOBS.get(self.sub_job.currentText(), "None"),
            "master_level": master_level, "buffs": buffs,
            "abilities": abilities,
            "enemy": {name: spin.value() for name, spin in self.enemy_spins.items()},
            "debuffs": debuffs, "tp_value": self.tp_value.value(),
        }

    def run_profile_report(self):
        if self.report_thread and self.report_thread.isRunning():
            return
        payloads = self._effective_profile_payloads()
        if not payloads:
            QMessageBox.information(self, "LAC report", "No reportable sets were found for this profile.")
            return
        try:
            context = self._report_context(use_profile_job=True)
        except Exception as error:
            QMessageBox.critical(self, "LAC report", str(error))
            return
        self.profile_report_table.setRowCount(0)
        self.profile_report_status.setText("Running profile report…")
        self.cancel_profile_report_button.setEnabled(True)
        self.character_combo.setEnabled(False)
        self.report_thread = ProfileReportThread(payloads, context, self)
        self.report_thread.progress.connect(self.profile_report_status.setText)
        self.report_thread.succeeded.connect(self._profile_report_done)
        self.report_thread.failed.connect(self._profile_report_failed)
        self.report_thread.stopped.connect(self._profile_report_stopped)
        self.report_thread.start()

    def stop_profile_report(self):
        if self.report_thread and self.report_thread.isRunning():
            self.report_thread.request_stop()
            self.cancel_profile_report_button.setEnabled(False)
            self.profile_report_status.setText("Stopping profile report after the current calculation...")

    @staticmethod
    def _report_value(value, decimals=1) -> str:
        if value in (None, ""):
            return "—"
        return f"{float(value):,.{decimals}f}"

    def _profile_report_done(self, rows: list[dict]):
        self.cancel_profile_report_button.setEnabled(False)
        self.character_combo.setEnabled(bool(self.character_paths))
        self._profile_report_rows = list(rows)
        self.profile_report_table.setSortingEnabled(False)
        self.profile_report_table.setRowCount(0)
        for row in rows:
            index = self.profile_report_table.rowCount()
            self.profile_report_table.insertRow(index)
            status = row.get("error") or "Ready"
            values = [
                row["name"], row["category"], row["ws_name"] or "—",
                row["weapon_setup"], self._report_value(row["tp_dps"]),
                self._report_value(row["time_to_ws"]),
                self._report_value(row["ws_damage"], 0), self._report_value(row["total_dps"]), status,
            ]
            for column, value in enumerate(values):
                numeric = (
                    row["tp_dps"], row["time_to_ws"], row["ws_damage"], row["total_dps"]
                )[column - 4] if 4 <= column <= 7 else None
                cell = NumericTableWidgetItem(value, numeric) if 4 <= column <= 7 else QTableWidgetItem(value)
                if row.get("error"):
                    cell.setToolTip(row["error"])
                    cell.setForeground(QColor("#b42318"))
                elif column == 8:
                    cell.setForeground(QColor("#137333"))
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, row)
                self.profile_report_table.setItem(index, column, cell)
        self.profile_report_table.setSortingEnabled(True)
        errors = sum(1 for row in rows if row.get("error"))
        self.profile_report_status.setText(
            f"Report complete: {len(rows) - errors}/{len(rows)} sets evaluated" +
            (f"; {errors} blocked/error rows are listed in Status." if errors else ".")
        )
        valid = [row for row in rows if not row.get("error")]
        best_cycle = max(valid, key=lambda row: float(row.get("total_dps") or 0), default=None)
        best_ws = max(valid, key=lambda row: float(row.get("ws_damage") or 0), default=None)
        if best_cycle is None:
            self.profile_report_summary.setText(
                "No complete result rows are available. Open Raw-set diagnostics for the blocking reason."
            )
        else:
            summary = (
                f"Best cycle DPS: {best_cycle['name']} ({float(best_cycle['total_dps']):,.1f})"
            )
            if best_ws is not None:
                summary += (
                    f"  ·  Best WS damage: {best_ws['name']} "
                    f"({float(best_ws['ws_damage']):,.0f})"
                )
            self.profile_report_summary.setText(summary)

    def _profile_report_row_selected(self):
        indexes = self.profile_report_table.selectedIndexes()
        if not indexes:
            return
        row_index = indexes[0].row()
        item = self.profile_report_table.item(row_index, 0)
        row = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(row, dict):
            return
        if row.get("error"):
            self.profile_report_summary.setText(
                f"{row['name']} is blocked: {row['error']}"
            )
            return
        self.profile_report_summary.setText(
            f"Selected: {row['name']}  ·  {row['category']}  ·  "
            f"TP DPS {float(row.get('tp_dps') or 0):,.1f}  ·  "
            f"TP time {float(row.get('time_to_ws') or 0):,.2f}s  ·  "
            f"WS damage {float(row.get('ws_damage') or 0):,.0f}  ·  "
            f"Cycle DPS {float(row.get('total_dps') or 0):,.1f}"
        )

    def _profile_report_failed(self, message: str):
        self.cancel_profile_report_button.setEnabled(False)
        self.character_combo.setEnabled(bool(self.character_paths))
        self.profile_report_status.setText(f"Report failed: {message}")
        QMessageBox.critical(self, "LAC report", message)

    def _profile_report_stopped(self):
        self.cancel_profile_report_button.setEnabled(False)
        self.character_combo.setEnabled(bool(self.character_paths))
        self.profile_report_status.setText("Profile report stopped.")

    def run_selected_profile_report(self):
        tp_payload = self.report_tp_combo.currentData()
        ws_payload = self.report_ws_set_combo.currentData()
        ws_name = self.report_ws_name_combo.currentText().strip()
        if not tp_payload or not ws_payload or not ws_name:
            QMessageBox.information(self, "LAC report", "Select a TP/DT/hybrid set, WS set, and weapon skill.")
            return
        try:
            context = self._report_context(use_profile_job=True)
            problems = [
                *tp_payload.get("missing", ()), *tp_payload.get("incomplete", ()),
                *tp_payload.get("ineligible", ()),
                *ws_payload.get("missing", ()), *ws_payload.get("incomplete", ()),
                *ws_payload.get("ineligible", ()),
            ]
            if problems:
                raise ValueError("Selected effective sets contain unresolved data: " + ", ".join(problems))
            ws_type = "ranged" if ws_name in (
                WS_BY_SKILL.get("Marksmanship", []) + WS_BY_SKILL.get("Archery", [])
            ) else "melee"
            enemy = _report_enemy(context["enemy"], context.get("debuffs"))
            tp_player = create_player.create_player(
                context["main_job"], context["sub_job"], context["master_level"],
                gearset=tp_payload["gearset"], buffs=context["buffs"], abilities=context["abilities"],
            )
            ws_player = create_player.create_player(
                context["main_job"], context["sub_job"], context["master_level"],
                gearset=ws_payload["gearset"], buffs=context["buffs"], abilities=context["abilities"],
            )
            total_dps, cycle = actions.average_tp_ws_cycle(
                tp_player, ws_player, enemy, ws_name,
                context["tp_value"], ws_type,
            )
            self.selected_report_result.setText(
                f"{tp_payload['name']} → {ws_payload['name']} using {ws_name}: "
                f"TP time {cycle[2]:,.2f}s, WS damage {cycle[3]:,.0f}, "
                f"total DPS {total_dps:,.1f}."
            )
        except Exception as error:
            QMessageBox.critical(self, "Selected report", str(error))

    def items_for_slot(self, slot: str) -> list[dict]:
        job = JOBS[self.main_job.currentText()]
        result, seen = [], set()
        for item in [gear.Empty, *self.equipment.get(slot, [])]:
            jobs = [str(value).lower() for value in item.get("Jobs", gear.all_jobs)]
            name = item_name(item)
            if job not in jobs or name in seen or _blacklist_matches(item, self.gear_blacklist):
                continue
            seen.add(name)
            result.append(item)
        return result

    @staticmethod
    def _item_level(item: dict) -> int | None:
        """Read item-level metadata supplied by GearSetBuilder/bridge exports."""
        for key in ("Item Level", "ItemLevel", "item_level", "ILvl", "ilvl"):
            value = item.get(key)
            if isinstance(value, (int, float)):
                return int(value)
            if value not in (None, ""):
                match = re.search(r"\d{2,3}", str(value))
                if match:
                    return int(match.group())
        text = " ".join(str(item.get(key, "")) for key in ("Name", "Name2"))
        match = re.search(r"\b(?:item\s*level|ilevel|ilvl)\s*[:=]?\s*(\d{2,3})\b", text, re.I)
        return int(match.group(1)) if match else None

    def optimizer_items_for_slot(self, slot: str) -> list[dict]:
        items = self.items_for_slot(slot)
        if not getattr(self, "exclude_under_119", None) or not self.exclude_under_119.isChecked():
            return items
        if slot not in ITEM_LEVEL_FILTER_SLOTS:
            return items
        filtered = []
        for item in items:
            level = self._item_level(item)
            if level is None or level >= 119:
                filtered.append(item)
        return filtered

    def _refresh_locked_gear_options(self):
        for slot, locked_name in self.locked_gear.items():
            if locked_name and locked_name not in {
                item_name(item) for item in self.optimizer_items_for_slot(slot)
            }:
                self.locked_gear[slot] = ""

    def _apply_locked_gear(self, check_gear: dict[str, list[dict]]) -> None:
        for slot, locked_name in self.locked_gear.items():
            if not locked_name:
                continue
            lookup = {item_name(item): item for item in self.optimizer_items_for_slot(slot)}
            item = lookup.get(str(locked_name))
            if item is not None:
                check_gear[slot] = [item]

    def _candidate_filter_changed(self, enabled: bool):
        if not enabled:
            self._refresh_locked_gear_options()
            for slot in SLOTS:
                self._update_candidate_button(slot)
            return
        for slot in SLOTS:
            valid = {item_name(item) for item in self.optimizer_items_for_slot(slot)}
            self.candidates[slot].intersection_update(valid)
            if not self.candidates[slot]:
                if slot in WEAPON_SLOTS:
                    # An empty weapon selection must remain empty; silently
                    # restoring the Quick Look weapon makes an unselected
                    # weapon participate in optimization.
                    self.candidates[slot].add("Empty")
                else:
                    fallback = item_name(self.quick_set.items[slot])
                    self.candidates[slot].add(fallback if fallback in valid else next(iter(valid), "Empty"))
            self._update_candidate_button(slot)
        self._refresh_locked_gear_options()
        self.statusBar().showMessage("Removed optimizer candidates under item level 119 where metadata was available.", 5000)

    def _shared_gear_changed(self, enabled: bool):
        self._refresh_shared_gear()
        self._refresh_locked_gear_options()
        if enabled:
            # The mode is an optimizer opt-in, so shared pieces participate
            # immediately instead of requiring a second "Select candidates"
            # pass.  They remain individually removable from each picker.
            for slot in SLOTS:
                self.candidates[slot].update(
                    item_name(item) for item in self.equipment.get(slot, ())
                    if item.get("Shared Only")
                )
        self._reset_invalid_equipment()
        for editor in (self.quick_set, self.tp_set, self.ws_set):
            editor.refresh_icons()
        self.statusBar().showMessage(
            "Transferable gear sharing enabled." if enabled else "Transferable gear sharing disabled.",
            4000,
        )

    def _refresh_shared_gear(self):
        """Merge transferable inventory from other bridge characters when enabled."""
        self.shared_catalog = {}
        if not getattr(self, "include_shared_gear", None) or not self.include_shared_gear.isChecked():
            if self.bridge_store.data:
                self.equipment = self.bridge_store.equipment_dict()
            return
        if not self.bridge_store.bridge_path or not self.bridge_store.ashita_root:
            return
        current_path = self.bridge_store.bridge_path.resolve()
        combined = self.bridge_store.equipment_dict()
        known = {item_name(item) for values in combined.values() for item in values}
        for label, path in self.character_paths.items():
            if path.resolve() == current_path:
                continue
            try:
                store = BridgeStore(self.bridge_store.ashita_root)
                store.set_hoxne_mastery_rank(self.hoxne_mastery_rank.value())
                store.load(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            for item in store.catalog.values():
                if not item.get("Eligible") or not item.get("Transferable", False):
                    continue
                name = item_name(item)
                if name in known or _blacklist_matches(item, self.gear_blacklist):
                    continue
                shared = deepcopy(item)
                shared["Shared Only"] = True
                shared["Shared Characters"] = [label]
                self.shared_catalog[name] = shared
                known.add(name)
                for slot in shared.get("Slots", ()):
                    if slot in combined:
                        combined[slot].append(shared)
        for slot in combined:
            combined[slot].sort(key=lambda item: item_name(item).casefold())
        self.equipment = combined

    def _capture_candidate_state(self) -> dict:
        return {
            "exclude_under_119": bool(self.exclude_under_119.isChecked()),
            "include_shared_gear": bool(self.include_shared_gear.isChecked()),
            "locks": {
                slot: str(value or "")
                for slot, value in self.locked_gear.items()
            },
            "candidates": {slot: sorted(values) for slot, values in self.candidates.items()},
        }

    def _load_saved_candidate_presets(self) -> dict[str, dict]:
        raw = self.settings.value("candidate_presets", "")
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        try:
            data = raw if isinstance(raw, dict) else json.loads(str(raw or "{}"))
        except (TypeError, ValueError):
            return {}
        return {
            str(name): value for name, value in data.items()
            if str(name).strip() and isinstance(value, dict)
        } if isinstance(data, dict) else {}

    def _refresh_candidate_preset_names(self):
        if not hasattr(self, "candidate_preset_combo"):
            return
        current = self.candidate_preset_combo.currentText()
        names = list(self._load_saved_candidate_presets())
        self.candidate_preset_combo.blockSignals(True)
        self.candidate_preset_combo.clear()
        self.candidate_preset_combo.addItems(names)
        if current in names:
            self.candidate_preset_combo.setCurrentText(current)
        self.candidate_preset_combo.blockSignals(False)

    def load_candidate_preset(self):
        name = self.candidate_preset_combo.currentText().strip()
        state = self._load_saved_candidate_presets().get(name)
        if not state:
            return
        candidates = state.get("candidates", {})
        if isinstance(candidates, dict):
            for slot in SLOTS:
                values = candidates.get(slot, [])
                self.candidates[slot] = set(values) if isinstance(values, list) else {"Empty"}
        self.exclude_under_119.blockSignals(True)
        self.exclude_under_119.setChecked(bool(state.get("exclude_under_119", False)))
        self.exclude_under_119.blockSignals(False)
        self.include_shared_gear.blockSignals(True)
        self.include_shared_gear.setChecked(bool(state.get("include_shared_gear", False)))
        self.include_shared_gear.blockSignals(False)
        self._refresh_shared_gear()
        self._refresh_locked_gear_options()
        locks = state.get("locks") if isinstance(state.get("locks"), dict) else {}
        for slot, value in locks.items():
            if slot in self.locked_gear:
                self.locked_gear[slot] = str(value or "")
        self._candidate_filter_changed(self.exclude_under_119.isChecked())
        for slot in SLOTS:
            self._update_candidate_button(slot)
        self.statusBar().showMessage(f"Loaded candidate preset: {name}", 5000)

    def save_candidate_preset(self):
        name, accepted = QInputDialog.getText(self, "Save candidate preset", "Preset name:")
        name = name.strip()
        if not accepted or not name:
            return
        saved = self._load_saved_candidate_presets()
        if name in saved:
            answer = QMessageBox.question(
                self, "Overwrite candidate preset", f"Replace saved preset '{name}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        saved[name] = self._capture_candidate_state()
        self.settings.setValue("candidate_presets", json.dumps(saved, sort_keys=True))
        self._refresh_candidate_preset_names()
        self.candidate_preset_combo.setCurrentText(name)
        self.statusBar().showMessage(f"Saved candidate preset: {name}", 5000)

    def delete_candidate_preset(self):
        name = self.candidate_preset_combo.currentText().strip()
        saved = self._load_saved_candidate_presets()
        if not name or name not in saved:
            return
        answer = QMessageBox.question(
            self, "Delete candidate preset", f"Delete saved preset '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        del saved[name]
        self.settings.setValue("candidate_presets", json.dumps(saved, sort_keys=True))
        self._refresh_candidate_preset_names()
        self.statusBar().showMessage(f"Deleted candidate preset: {name}", 5000)

    def choose_candidates(self, slot: str):
        dialog = CandidatePicker(
            slot, self.optimizer_items_for_slot(slot), self.candidates[slot], self.icons,
            self.locked_gear.get(slot, ""), self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.candidates[slot] = dialog.selected_names
            self.locked_gear[slot] = dialog.locked_name
            self._update_candidate_button(slot)

    def select_all_candidates(self):
        for slot in SLOTS:
            self.candidates[slot] = {item_name(item) for item in self.optimizer_items_for_slot(slot)}
            self._update_candidate_button(slot)
        self.statusBar().showMessage("All available gear selected for every optimizer slot.", 5000)

    def _update_candidate_button(self, slot: str):
        self.candidate_buttons[slot].setText(f"{len(self.candidates[slot])} selected")
        if hasattr(self, "candidate_detail_labels"):
            player_item = self.quick_set.items.get(slot, gear.Empty)
            shared = [
                item_name(item) for item in self.equipment.get(slot, ())
                if item.get("Shared Only")
            ]
            detail = f"Player: {item_name(player_item)}"
            if shared:
                detail += "\nTransferable: " + ", ".join(shared[:2])
                if len(shared) > 2:
                    detail += f" (+{len(shared) - 2})"
            if self.locked_gear.get(slot):
                detail += f"\nLocked: {self.locked_gear[slot]}"
            self.candidate_detail_labels[slot].setText(detail)

    def choose_bridge_root(self):
        initial = self.settings.value("ashita_root", "", str)
        directory = QFileDialog.getExistingDirectory(
            self, "Select Ashita installation directory", initial
        )
        if directory:
            self.settings.setValue("ashita_root", directory)
            self._discover_characters(directory)

    def _discover_characters(self, directory: str):
        try:
            self.bridge_store.set_root(directory)
            characters = self.bridge_store.discover_characters()
        except Exception as error:
            QMessageBox.critical(self, "Character bridge", str(error))
            return
        self.character_paths = dict(characters)
        self.character_combo.blockSignals(True)
        self.character_combo.clear()
        self.character_combo.addItems(self.character_paths)
        self.character_combo.setEnabled(bool(characters))
        previous = self.settings.value("character", "", str)
        if previous in self.character_paths:
            self.character_combo.setCurrentText(previous)
        self.character_combo.blockSignals(False)
        if characters:
            self._load_character(self.character_combo.currentText())
        else:
            self.bridge_label.setText("No GearSetBuilder bridge files found")

    def _load_character(self, label: str):
        path = self.character_paths.get(label)
        if path is None:
            return
        try:
            # The combo changes before this slot runs, so preserve the bridge
            # character that was actually loaded, not the newly selected label.
            self._save_current_character_state()
            data = self.bridge_store.load(path)
            character_data = data.get("character") or {}
            character_key = str(character_data.get("key") or path.parent.name)
            saved_rank = self.settings.value(f"hoxne_mastery_rank/{character_key}", 5, int)
            self.hoxne_mastery_rank.blockSignals(True)
            self.hoxne_mastery_rank.setValue(max(1, min(10, saved_rank)))
            self.hoxne_mastery_rank.blockSignals(False)
            self.bridge_store.set_hoxne_mastery_rank(self.hoxne_mastery_rank.value())
            self.best_player = None
            self.best_tp_player = None
            self.best_ws_player = None
            self._last_completed_optimizer_action = ""
            self.icons.set_bridge_icon_dir(path.parent / "icons32")
            gear.all_gear.update(self.bridge_store.catalog)
            self.equipment = self.bridge_store.equipment_dict()
            self._refresh_shared_gear()
            self.settings.setValue("character", label)
            eligible = sum(1 for item in self.bridge_store.catalog.values() if item.get("Eligible"))
            character = data.get("character", {}).get("name", label)
            self.bridge_label.setText(
                f"{character}\n{len(self.bridge_store.catalog)} variants · "
                f"{eligible} modeled candidates"
            )
            self._reset_invalid_equipment()
            for editor in (self.quick_set, self.tp_set, self.ws_set):
                editor.refresh_icons()
            self._populate_profile_report()
            self._apply_bridge_master_level()
            self._active_character_key = character_key
            self._load_character_state(character_key)
            self.refresh_quick_stats()
            self.statusBar().showMessage(f"Loaded {label}", 5000)
        except Exception as error:
            QMessageBox.critical(self, "Character bridge", str(error))

    def refresh_bridge(self):
        if self.character_combo.currentText():
            self._load_character(self.character_combo.currentText())
        else:
            self.choose_bridge_root()

    def _restore_settings(self):
        root = self.settings.value("ashita_root", "", str)
        if root and Path(root).exists():
            self._discover_characters(root)

    @staticmethod
    def _saved_item_reference(item: dict) -> dict:
        """Keep a compact, character-safe reference to one equipped item."""
        return {
            "bridge_key": str(item.get("Bridge Key") or ""),
            "name2": str(item.get("Name2") or ""),
            "name": str(item.get("Name") or ""),
        }

    def _resolve_saved_item(self, slot: str, reference) -> dict:
        if not isinstance(reference, dict):
            return gear.Empty
        bridge_key = str(reference.get("bridge_key") or "")
        if bridge_key and bridge_key in self.bridge_store.by_key:
            return self.bridge_store.by_key[bridge_key]
        name2 = str(reference.get("name2") or "")
        name = str(reference.get("name") or "")
        for item in self.equipment.get(slot, []):
            if name2 and str(item.get("Name2") or "") == name2:
                return item
        for item in self.equipment.get(slot, []):
            if name and str(item.get("Name") or "") == name:
                return item
        return gear.Empty

    def _character_state_key(self, character_key: str | None = None) -> str:
        key = str(character_key or self._active_character_key or "").strip()
        return f"character_state/{key}" if key else ""

    def _save_current_character_state(self):
        """Persist the user-facing state before switching away or closing."""
        storage_key = self._character_state_key()
        if not storage_key:
            return
        state = {
            "version": 1,
            "player": {
                "main_job": self.main_job.currentText(),
                "sub_job": self.sub_job.currentText(),
                "master_level": self.master_level.value(),
                "hoxne_mastery_rank": self.hoxne_mastery_rank.value(),
                "tp_value": self.tp_value.value(),
                "aftermath": self.aftermath.value(),
                "weapon_skill": self.ws_combo.currentText(),
                "spell": self.spell_combo.currentText(),
            },
            "gearsets": {
                name: {
                    slot: self._saved_item_reference(editor.items[slot]) for slot in SLOTS
                }
                for name, editor in (
                    ("quick", self.quick_set), ("tp", self.tp_set), ("ws", self.ws_set),
                )
            },
            "buffs": self._capture_buff_state(),
            "optimizer": {
                "action": self.optimize_action.currentText(),
                "metric": self.metric_combo.currentText(),
                "substat_base_action": self.substat_base_action.currentText(),
                "substat_loss_percent": self.substat_loss_percent.value(),
                "substat_stats": [combo.currentText() for combo in self.substat_combos],
                "pdt": self.pdt.value(), "mdt": self.mdt.value(), "dt": self.dt.value(),
                "combined_defense_both": self.combined_defense_both.isChecked(),
                "restarts": self.restarts.value(), "workers": self.workers.value(),
                "parallel_mode": self.parallel_mode.currentText(), "seed": self.seed.text(),
                "prune_candidates": self.prune_candidates.isChecked(),
                "candidates": self._capture_candidate_state(),
            },
            "simulation": {"plot_dps": self.plot_dps_checkbox.isChecked()},
            "report": {
                "job": self.profile_job_combo.currentText(),
                "main_weapon": self.report_main_weapon_combo.currentText(),
                "ranged_weapon": self.report_ranged_weapon_combo.currentText(),
                "defense": self.report_defense_combo.currentText(),
                "tp_set": self.report_tp_combo.currentText(),
                "ws_set": self.report_ws_set_combo.currentText(),
                "weapon_skill": self.report_ws_name_combo.currentText(),
            },
            "tab": self.tabs.currentIndex(),
        }
        self.settings.setValue(storage_key, json.dumps(state, separators=(",", ":")))

    def _load_character_state(self, character_key: str):
        """Restore settings only after the selected character's gear is available."""
        raw = self.settings.value(self._character_state_key(character_key), "", str)
        try:
            state = json.loads(raw) if raw else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        if not isinstance(state, dict):
            return
        player = state.get("player") if isinstance(state.get("player"), dict) else {}
        self._set_combo_value(self.main_job, player.get("main_job"), self.main_job.currentText())
        self._set_combo_value(self.sub_job, player.get("sub_job"), self.sub_job.currentText())
        for control, key in (
            (self.master_level, "master_level"), (self.hoxne_mastery_rank, "hoxne_mastery_rank"),
            (self.tp_value, "tp_value"), (self.aftermath, "aftermath"),
        ):
            try:
                control.setValue(int(player.get(key, control.value())))
            except (TypeError, ValueError):
                pass
        buffs = state.get("buffs")
        if isinstance(buffs, dict):
            self._apply_buff_state(buffs)
        gearsets = state.get("gearsets") if isinstance(state.get("gearsets"), dict) else {}
        for name, editor in (("quick", self.quick_set), ("tp", self.tp_set), ("ws", self.ws_set)):
            saved_set = gearsets.get(name)
            if not isinstance(saved_set, dict):
                continue
            editor.set_gearset({
                slot: self._resolve_saved_item(slot, saved_set.get(slot))
                for slot in SLOTS
            })
        self._reset_invalid_equipment()
        self._refresh_locked_gear_options()
        self._refresh_ws_choices()
        self._set_combo_value(self.ws_combo, player.get("weapon_skill"), "None")
        self._set_combo_value(self.spell_combo, player.get("spell"), "None")

        optimizer = state.get("optimizer") if isinstance(state.get("optimizer"), dict) else {}
        self._set_combo_value(self.optimize_action, optimizer.get("action"), self.optimize_action.currentText())
        self._set_combo_value(self.metric_combo, optimizer.get("metric"), self.metric_combo.currentText())
        self._set_combo_value(
            self.substat_base_action, optimizer.get("substat_base_action"),
            self.substat_base_action.currentText(),
        )
        try:
            self.substat_loss_percent.setValue(float(
                optimizer.get("substat_loss_percent", self.substat_loss_percent.value())
            ))
        except (TypeError, ValueError):
            pass
        saved_substats = optimizer.get("substat_stats")
        if isinstance(saved_substats, list):
            for combo, value in zip(self.substat_combos, saved_substats):
                self._set_combo_value(combo, value, "None")
        self._set_combo_value(self.parallel_mode, optimizer.get("parallel_mode"), self.parallel_mode.currentText())
        for control, key in ((self.pdt, "pdt"), (self.mdt, "mdt"), (self.dt, "dt"), (self.restarts, "restarts"), (self.workers, "workers")):
            try:
                control.setValue(int(optimizer.get(key, control.value())))
            except (TypeError, ValueError):
                pass
        self.combined_defense_both.setChecked(bool(optimizer.get("combined_defense_both", True)))
        self.seed.setText(str(optimizer.get("seed", "")))
        self.prune_candidates.setChecked(bool(optimizer.get("prune_candidates", True)))
        candidate_state = optimizer.get("candidates")
        if isinstance(candidate_state, dict):
            saved_candidates = candidate_state.get("candidates")
            if isinstance(saved_candidates, dict):
                for slot in SLOTS:
                    values = saved_candidates.get(slot, [])
                    self.candidates[slot] = set(values) if isinstance(values, list) else {"Empty"}
            self.exclude_under_119.setChecked(bool(candidate_state.get("exclude_under_119", False)))
            self.include_shared_gear.setChecked(bool(candidate_state.get("include_shared_gear", False)))
            self._refresh_shared_gear()
            self._refresh_locked_gear_options()
            locks = candidate_state.get("locks") if isinstance(candidate_state.get("locks"), dict) else {}
            for slot, value in locks.items():
                if slot in self.locked_gear:
                    self.locked_gear[slot] = str(value or "")
            self._candidate_filter_changed(self.exclude_under_119.isChecked())
        for slot in SLOTS:
            self._update_candidate_button(slot)
        simulation = state.get("simulation") if isinstance(state.get("simulation"), dict) else {}
        self.plot_dps_checkbox.setChecked(bool(simulation.get("plot_dps", False)))

        report = state.get("report") if isinstance(state.get("report"), dict) else {}
        self._set_combo_value(self.profile_job_combo, report.get("job"), self.profile_job_combo.currentText())
        self._populate_profile_report()
        self._set_combo_value(self.report_main_weapon_combo, report.get("main_weapon"), self.report_main_weapon_combo.currentText())
        self._set_combo_value(self.report_ranged_weapon_combo, report.get("ranged_weapon"), self.report_ranged_weapon_combo.currentText())
        self._set_combo_value(self.report_defense_combo, report.get("defense"), self.report_defense_combo.currentText())
        self._set_combo_value(self.report_tp_combo, report.get("tp_set"), self.report_tp_combo.currentText())
        self._set_combo_value(self.report_ws_set_combo, report.get("ws_set"), self.report_ws_set_combo.currentText())
        self._set_combo_value(self.report_ws_name_combo, report.get("weapon_skill"), "None")
        try:
            self.tabs.setCurrentIndex(max(0, min(self.tabs.count() - 1, int(state.get("tab", 0)))))
        except (TypeError, ValueError):
            pass

    def closeEvent(self, event):
        running_threads = [
            thread for thread in (
                self.optimizer_thread, self.report_thread,
                getattr(self, "plot_thread", None),
            )
            if thread is not None and thread.isRunning()
        ]
        for thread in running_threads:
            request_stop = getattr(thread, "request_stop", None)
            if request_stop is not None:
                request_stop()
        stop_deadline = time.monotonic() + 5.0
        stopped_cleanly = []
        for thread in running_threads:
            remaining_ms = max(0, int((stop_deadline - time.monotonic()) * 1000))
            stopped_cleanly.append(thread.wait(remaining_ms))
        if not all(stopped_cleanly):
            event.ignore()
            QMessageBox.information(
                self, "Background work is stopping",
                "A calculation is still stopping safely. Try closing the window again in a moment.",
            )
            return
        self._save_current_character_state()
        self.settings.sync()
        super().closeEvent(event)

    def _refresh_job_data(self, *_args):
        job = JOBS.get(self.main_job.currentText(), "sch")
        spells = ["None", *SPELLS_BY_JOB.get(job, [])]
        current = self.spell_combo.currentText()
        self.spell_combo.clear()
        self.spell_combo.addItems(dict.fromkeys(spells))
        if current in spells:
            self.spell_combo.setCurrentText(current)
        self._reset_invalid_equipment()
        self._refresh_ws_choices()
        self._apply_bridge_master_level()
        self._rebuild_quick_ability_controls()
        self.refresh_quick_stats()

    def _refresh_quick_ability_job(self, *_args):
        """Refresh ability availability when subjob or master level changes."""
        self._rebuild_quick_ability_controls()
        self.refresh_quick_stats()

    def _apply_bridge_master_level(self):
        """Use GearSetBuilder's exported Master Level for the selected main job."""
        character = (self.bridge_store.data.get("character") or {})
        metadata = character.get("metadata") or {}
        levels = metadata.get("master_levels") or {}
        job = JOBS.get(self.main_job.currentText())
        if job is None or job not in levels:
            return
        try:
            level = max(0, min(50, int(levels[job])))
        except (TypeError, ValueError):
            return
        if self.master_level.value() != level:
            self.master_level.setValue(level)

    def _hoxne_mastery_rank_changed(self, rank: int):
        """Rebind bridge-owned Hoxne items at the selected character's rank."""
        if not self.bridge_store.data:
            return
        character = self.bridge_store.data.get("character") or {}
        character_key = str(character.get("key") or "default")
        self.settings.setValue(f"hoxne_mastery_rank/{character_key}", int(rank))
        self.bridge_store.set_hoxne_mastery_rank(rank)
        gear.all_gear.update(self.bridge_store.catalog)
        self.equipment = self.bridge_store.equipment_dict()
        self._refresh_shared_gear()
        for editor in (self.quick_set, self.tp_set, self.ws_set):
            changed = False
            for slot in SLOTS:
                bridge_key = str(editor.items[slot].get("Bridge Key") or "")
                if bridge_key in self.bridge_store.by_key:
                    editor.set_item(slot, self.bridge_store.by_key[bridge_key], emit=False)
                    changed = True
            if changed:
                editor.changed.emit()
            editor.refresh_icons()
        self._reset_invalid_equipment()
        self._refresh_locked_gear_options()
        self._refresh_ws_choices()
        self._refresh_ranking_weapon_types()
        self.refresh_quick_stats()

    def _gear_changed(self):
        for slot in SLOTS:
            name = item_name(self.quick_set.items[slot])
            allowed = {item_name(item) for item in self.optimizer_items_for_slot(slot)}
            if name != "Empty" and name in allowed:
                self.candidates[slot].add(name)
            self._update_candidate_button(slot)
        self._refresh_ws_choices()
        self._refresh_ranking_weapon_types()
        self.refresh_quick_stats()

    def _refresh_ws_choices(self):
        current = self.ws_combo.currentText()
        skills = []
        for slot in ("main", "ranged"):
            skill = self.quick_set.items[slot].get("Skill Type", "None")
            skills.extend(WS_BY_SKILL.get(skill, []))
        if not skills:
            skills = [value for values in WS_BY_SKILL.values() for value in values]
        values = list(dict.fromkeys(["None", *skills]))
        self.ws_combo.clear()
        self.ws_combo.addItems(values)
        if current in values:
            self.ws_combo.setCurrentText(current)

    def _reset_invalid_equipment(self):
        if not hasattr(self, "quick_set"):
            return
        valid = {slot: {item_name(item) for item in self.items_for_slot(slot)} for slot in SLOTS}
        for editor in (self.quick_set, self.tp_set, self.ws_set):
            changed = False
            for slot in SLOTS:
                if item_name(editor.items[slot]) not in valid[slot]:
                    editor.set_item(slot, gear.Empty, emit=False)
                    changed = True
            if changed:
                editor.changed.emit()
        for slot in SLOTS:
            candidate_valid = {
                item_name(item) for item in self.optimizer_items_for_slot(slot)
            }
            self.candidates[slot].intersection_update(candidate_valid)
            if not self.candidates[slot]:
                if slot in WEAPON_SLOTS:
                    self.candidates[slot].add("Empty")
                else:
                    fallback = item_name(self.quick_set.items[slot])
                    self.candidates[slot].add(
                        fallback if fallback in candidate_valid else next(iter(candidate_valid), "Empty")
                    )
            self._update_candidate_button(slot)

    def _json_object(self, editor: QPlainTextEdit, label: str) -> dict:
        value = json.loads(editor.toPlainText() or "{}")
        if not isinstance(value, dict):
            raise ValueError(f"{label} must be a JSON object.")
        return value

    def _context(self, gearset: dict | None = None):
        context = self._report_context()
        buffs = context["buffs"]
        abilities = context["abilities"]
        player = create_player.create_player(
            JOBS[self.main_job.currentText()],
            JOBS.get(self.sub_job.currentText(), "None"),
            self.master_level.value(), gearset=gearset or self.quick_set.items,
            buffs=buffs, abilities=abilities,
        )
        enemy = _report_enemy(context["enemy"], context["debuffs"])
        return player, enemy, buffs, abilities

    def _ws_type(self) -> str:
        ranged = set(WS_BY_SKILL.get("Marksmanship", []) + WS_BY_SKILL.get("Archery", []))
        return "ranged" if self.ws_combo.currentText() in ranged else "melee"

    @staticmethod
    def _spell_type(name: str) -> str:
        if "ton: " in name.lower():
            return "Ninjutsu"
        if name and name.split()[-1].lower() == "shot":
            return "Quick Draw"
        if "Banish" in name or "Holy" in name:
            return "Divine Magic"
        if name in ("Ranged Attack", "Barrage"):
            return "Ranged Attack"
        return "Elemental Magic"

    def evaluate(self, action: str):
        try:
            player, enemy, _buffs, _abilities = self._context()
            if action == "ws":
                output = actions.average_ws(
                    player, enemy, self.ws_combo.currentText(), self.tp_value.value(),
                    self._ws_type(), "Damage dealt",
                )
                text = f"Average damage: {output[1][0]:,.0f}    TP return: {output[1][1]:,.1f}"
            elif action == "spell":
                name = self.spell_combo.currentText()
                output = actions.cast_spell(
                    player, enemy, name, self._spell_type(name), "Damage dealt"
                )
                text = f"Average damage: {output[1][0]:,.0f}    TP return: {output[1][1]:,.1f}"
            else:
                output = actions.average_attack_round(
                    player, enemy, 0, self.tp_value.value(), "Time to WS"
                )
                text = f"Time per WS: {output[0]:,.3f}s    TP per round: {output[1][1]:,.1f}"
            self.result_label.setText(text)
            self._render_quick_stats(player, enemy)
        except Exception as error:
            QMessageBox.critical(self, "Evaluation failed", str(error))

    def run_optimizer(self):
        if self.optimizer_thread and self.optimizer_thread.isRunning():
            return
        try:
            _player, enemy, buffs, abilities = self._context()
            check_gear, empty_weapon_slots = _optimizer_check_gear(
                self.candidates,
                {slot: self.optimizer_items_for_slot(slot) for slot in SLOTS},
                self.quick_set.items,
            )
            self._apply_locked_gear(check_gear)
            if empty_weapon_slots:
                labels = ", ".join(slot.upper() for slot in empty_weapon_slots)
                raise ValueError(
                    f"No optimizer candidates are selected for {labels}. "
                    "Select a weapon or explicitly select Empty; the current Quick Look weapon will not be added automatically."
                )
            action_label = self.optimize_action.currentText()
            ranking_mode = action_label == "Rank weapon-type WS"
            substat_mode = action_label == "Sub-stat optimization"
            ranking_skill = self.ranking_weapon_type.currentText().strip() if ranking_mode else ""
            if ranking_mode:
                if ranking_skill not in WS_BY_SKILL:
                    raise ValueError(
                        "Equip a modeled melee or ranged weapon before running WS rankings."
                    )
                check_gear = _lock_ranking_weapon_slots(check_gear, self.quick_set.items)
            optimizer_ws_type = (
                "ranged" if ranking_skill in {"Archery", "Marksmanship"}
                else "melee" if ranking_mode else self._ws_type()
            )
            if not any(len(values) > 1 for values in check_gear.values()):
                raise ValueError("Select at least two candidates in one slot.")
            raw_checks = wsdist.estimate_candidate_checks(
                check_gear, JOBS[self.main_job.currentText()], optimizer_ws_type
            )
            pruned_count = 0
            if self.prune_candidates.isChecked():
                check_gear, pruned_count = wsdist.prune_dominated_candidates(check_gear)
            seed_text = self.seed.text().strip()
            seed = int(seed_text) if seed_text else None
            action_type = {
                "Weapon skill": "weapon skill", "Attack round": "attack round",
                "Spell": "spell cast",
                "Combined TP + WS": "combined tp/ws",
                "Rank weapon-type WS": "rank weapon skills",
            }.get(action_label)
            if substat_mode:
                action_type = {
                    "Weapon skill": "weapon skill", "Attack round": "attack round",
                    "Spell": "spell cast", "Combined TP + WS": "combined tp/ws",
                }.get(self.substat_base_action.currentText())
                selected_substats = [
                    combo.currentText() for combo in self.substat_combos
                    if combo.currentText() != "None"
                ]
                if not selected_substats:
                    raise ValueError("Select at least one secondary stat to optimize.")
            else:
                selected_substats = []
            if action_type in {"weapon skill", "combined tp/ws"} and self.ws_combo.currentText() in {"", "None"}:
                raise ValueError("Select a weapon skill for this optimizer mode.")
            if action_type == "spell cast" and self.spell_combo.currentText() in {"", "None"}:
                raise ValueError("Select a spell for this optimizer mode.")
            # The original optimizer uses negative damage-taken values.  Its
            # initial pass sentinel is 200, so 199 means an explicitly disabled
            # requirement while still ensuring the search performs one pass.
            pdt_requirement = -self.pdt.value() if self.pdt.value() else 199
            mdt_requirement = -self.mdt.value() if self.mdt.value() else 199
            dt_requirement = -self.dt.value() if self.dt.value() else 199
            parallel_mode = (
                "single_run" if self.parallel_mode.currentText() == "Split one search run"
                else "search_runs"
            )
            common = (
                JOBS[self.main_job.currentText()], JOBS.get(self.sub_job.currentText(), "None"),
                self.master_level.value(), buffs, abilities, enemy,
            )
            if ranking_mode:
                args = common + (
                    list(WS_BY_SKILL[ranking_skill]), optimizer_ws_type, check_gear,
                    dict(self.quick_set.items), pdt_requirement, mdt_requirement,
                )
                kwargs = {
                    "dt_requirement": dt_requirement,
                    "tp_values": (1000, 2000, 3000),
                    "restarts": self.restarts.value(), "workers": self.workers.value(),
                    "seed": seed, "parallel_mode": parallel_mode,
                }
            elif substat_mode:
                args = common + (
                    self.ws_combo.currentText(), self.spell_combo.currentText(), action_type,
                    self.tp_value.value(), check_gear, dict(self.quick_set.items),
                    pdt_requirement, mdt_requirement, self.metric_combo.currentText(), False, 2,
                    [
                        {"target": stat, "loss_percent": self.substat_loss_percent.value()}
                        for stat in selected_substats
                    ],
                )
                kwargs = {
                    "restarts": self.restarts.value(), "workers": self.workers.value(),
                    "seed": seed, "return_details": True, "return_top_results": True,
                    "dt_requirement": dt_requirement, "parallel_mode": parallel_mode,
                }
            else:
                args = common + (
                    self.ws_combo.currentText(), self.spell_combo.currentText(), action_type,
                    self.tp_value.value(), check_gear, dict(self.quick_set.items),
                    pdt_requirement, mdt_requirement, self.metric_combo.currentText(), False, 2,
                )
                kwargs = {
                    "restarts": self.restarts.value(), "workers": self.workers.value(),
                    "seed": seed, "return_details": True, "return_top_results": True,
                    "dt_requirement": dt_requirement,
                    "combined_defense_both": self.combined_defense_both.isChecked(),
                    "tp_starting_gearset": dict(self.tp_set.items),
                    "ws_starting_gearset": dict(self.ws_set.items),
                    "parallel_mode": parallel_mode,
                }
            checks = wsdist.estimate_candidate_checks(
                check_gear, JOBS[self.main_job.currentText()], optimizer_ws_type
            )
            self.optimizer_log.clear()
            self.optimizer_top_results = []
            self._optimizer_run_state = {}
            self._optimizer_started_at = time.monotonic()
            run_count = 1 if ranking_mode or kwargs["parallel_mode"] == "single_run" else self.restarts.value()
            self._initialize_optimizer_run_cards(run_count)
            self._optimizer_status_timer.start()
            self.show_top_sets_button.setEnabled(False)
            self.optimizer_progress_value.setText("Approx. progress: 0.0%")
            self.optimizer_eta_value.setText("Estimated time remaining: calculating…")
            self.optimizer_best_value.setText("Best metric: —")
            self.optimizer_phase_value.setText("Current phase: starting")
            self._append_optimizer_log(
                f"Starting optimizer · ~{checks:,} candidates per pass"
            )
            self.optimizer_activity.setText("Starting…")
            if pruned_count:
                reduction = (1 - checks / raw_checks) * 100 if raw_checks else 0
                self._append_optimizer_log(
                    f"Candidate pre-pass removed {pruned_count:,} clearly dominated items "
                    f"({raw_checks:,} → {checks:,} estimated checks, {reduction:.1f}% fewer)."
                )
            self.optimize_button.setEnabled(False)
            self.stop_optimizer_button.setEnabled(True)
            self.equip_best_button.setEnabled(False)
            self._ranking_skill_in_progress = ranking_skill if ranking_mode else None
            self._optimizer_action_in_progress = action_label
            self._last_completed_optimizer_action = ""
            self._last_substat_summary = []
            self.optimizer_thread = OptimizeThread(
                args, kwargs, self,
                target=(
                    wsdist.rank_weapon_skills if ranking_mode
                    else wsdist.optimize_substats if substat_mode
                    else None
                ),
            )
            self.optimizer_thread.progress.connect(self._optimizer_progress)
            self.optimizer_thread.succeeded.connect(
                self._ws_ranking_done if ranking_mode else self._optimizer_done
            )
            self.optimizer_thread.failed.connect(self._optimizer_failed)
            self.optimizer_thread.stopped.connect(self._optimizer_stopped)
            self.optimizer_thread.start()
        except Exception as error:
            QMessageBox.critical(self, "Optimizer", str(error))

    def _append_optimizer_log(self, message: str):
        """Append a readable, color-coded optimizer status line."""
        match = re.search(r"Search run (\d+)", message)
        lowered = message.casefold()
        if match:
            color = OPTIMIZER_RUN_COLORS[(int(match.group(1)) - 1) % len(OPTIMIZER_RUN_COLORS)]
            if "failed" in lowered or "error" in lowered:
                color = "#b42318"
            elif "stopped" in lowered or "stop requested" in lowered:
                color = "#9a6700"
        elif "failed" in lowered or "error" in lowered:
            color = "#b42318"
        elif "stopped" in lowered or "stop requested" in lowered:
            color = "#9a6700"
        elif "completed" in lowered or "selected search run" in lowered:
            color = "#137333"
        else:
            color = "#344054"
        self.optimizer_log.append(f"<span style='color:{color}'>{escape(message)}</span>")

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = max(0, int(seconds))
        if seconds < 60:
            return f"{seconds}s"
        minutes, remaining = divmod(seconds, 60)
        return f"{minutes}m {remaining:02d}s"

    def _initialize_optimizer_run_cards(self, run_count: int):
        """Create stable per-run display areas before any worker reports progress."""
        while self.optimizer_runs_layout.count():
            item = self.optimizer_runs_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._optimizer_run_cards = {}
        started_at = self._optimizer_started_at or time.monotonic()
        for index in range(1, run_count + 1):
            state = {
                "index": index,
                "total": run_count,
                "phase": "queued",
                "fraction": 0.0,
                "started_at": started_at,
                "updated": started_at,
                "improvement": "none yet",
            }
            self._optimizer_run_state[index] = state
            card = QFrame()
            card.setFrameShape(QFrame.Shape.StyledPanel)
            card.setStyleSheet(
                "QFrame { border: 1px solid #c7d0d9; border-radius: 4px; "
                "background: #f8fafc; }"
            )
            grid = QGridLayout(card)
            grid.setContentsMargins(10, 8, 10, 8)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(7)
            title = QLabel(f"Search run {index}/{run_count}")
            title.setStyleSheet(
                f"font-weight: 700; color: {OPTIMIZER_RUN_COLORS[(index - 1) % len(OPTIMIZER_RUN_COLORS)]};"
            )
            phase = QLabel("Queued")
            elapsed = QLabel("Elapsed: 0s")
            last = QLabel("Last upgrade: none yet")
            detail = QLabel("Waiting for a worker update.")
            results = QLabel("Current results: waiting for a valid set.")
            detail.setStyleSheet("color: #475467;")
            results.setStyleSheet("color: #344054;")
            for label in (phase, elapsed, last, detail, results):
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(title, 0, 0)
            grid.addWidget(phase, 0, 1)
            grid.addWidget(elapsed, 1, 0)
            grid.addWidget(last, 1, 1)
            grid.addWidget(detail, 2, 0, 1, 2)
            grid.addWidget(results, 3, 0, 1, 2)
            self.optimizer_runs_layout.addWidget(card, (index - 1) // 2, (index - 1) % 2)
            self._optimizer_run_cards[index] = {
                "card": card, "title": title, "phase": phase, "elapsed": elapsed,
                "last": last, "detail": detail, "results": results,
            }

    def _refresh_optimizer_run_cards(self):
        now = time.monotonic()
        for index, card in self._optimizer_run_cards.items():
            state = self._optimizer_run_state.get(index)
            if not state:
                continue
            phase = str(state.get("phase", "queued")).replace("_", " ").title()
            elapsed = now - state.get("started_at", self._optimizer_started_at or now)
            card["phase"].setText(f"State: {phase}")
            card["elapsed"].setText(f"Elapsed: {self._format_duration(elapsed)}")
            improvement = state.get("improvement") or "none yet"
            card["last"].setText(f"Last upgrade: {improvement}")
            details = []
            if state.get("iteration") and state.get("iterations"):
                details.append(f"Pass {state['iteration']}/{state['iterations']}")
            if state.get("tested") is not None and state.get("planned"):
                details.append(f"{state['tested']:,}/{state['planned']:,} combinations")
            if state.get("best") is not None:
                details.append(f"Best: {state['best']:,.4f}")
            card["detail"].setText("  ·  ".join(details) or "Waiting for a worker update.")
            card["results"].setText(
                str(state.get("results") or "Current results: waiting for a valid set.")
            )
            if state.get("phase") == "completed":
                color, background = "#137333", "#f1faf3"
            elif state.get("phase") in {"stopping", "stopped"}:
                color, background = "#9a6700", "#fff8e7"
            elif state.get("phase") == "failed":
                color, background = "#b42318", "#fff5f4"
            else:
                color = OPTIMIZER_RUN_COLORS[(index - 1) % len(OPTIMIZER_RUN_COLORS)]
                background = "#f8fafc"
            card["card"].setStyleSheet(
                "QFrame { border: 1px solid #c7d0d9; border-radius: 4px; "
                f"background: {background}; }}"
            )
            card["title"].setStyleSheet(f"font-weight: 700; color: {color};")
            card["phase"].setStyleSheet(f"font-weight: 600; color: {color};")

    def _refresh_optimizer_status(self):
        states = list(self._optimizer_run_state.values())
        if not states:
            return
        self._refresh_optimizer_run_cards()
        total_runs = max((state.get("total") or 0 for state in states), default=len(states))
        fractions = [
            float(state.get("fraction", 0.0))
            if state.get("phase") == "completed"
            else min(0.98, float(state.get("fraction", 0.0)))
            for state in states
        ]
        progress = sum(fractions) / max(1, len(fractions))
        self.optimizer_progress_value.setText(f"Approx. progress: {progress * 100:.1f}%")
        elapsed = time.monotonic() - (self._optimizer_started_at or time.monotonic())
        ranking_states = [state for state in states if state.get("ranking_total")]
        ranking_state = ranking_states[0] if ranking_states else None
        ranking_done = int(ranking_state.get("ranking_completed", 0)) if ranking_state else 0
        ranking_total = int(ranking_state.get("ranking_total", 0)) if ranking_state else 0
        if ranking_state and ranking_done > 0 and elapsed > 1:
            # Ranking work is intentionally sequential (one optimizer call per
            # WS/TP cell), so use the measured mean cell time rather than the
            # generic optimizer fraction, which assumes equal search runs.
            remaining = elapsed / ranking_done * max(0, ranking_total - ranking_done)
            self.optimizer_eta_value.setText(
                f"Estimated time remaining: {self._format_duration(remaining)}"
            )
        elif progress > 0.001 and elapsed > 1:
            remaining = elapsed * (1 - progress) / progress
            self.optimizer_eta_value.setText(
                f"Estimated time remaining: {self._format_duration(remaining)}"
            )
        else:
            self.optimizer_eta_value.setText("Estimated time remaining: calculating…")
        best_values = [state.get("best") for state in states if state.get("best") is not None]
        if best_values:
            best_state = max(
                (state for state in states if state.get("best") is not None),
                key=lambda value: value["best"],
            )
            improvement = best_state.get("improvement")
            suffix = f" · last: {improvement}" if improvement and improvement != "none" else ""
            self.optimizer_best_value.setText(
                f"Best metric: {max(best_values):,.4f}{suffix}"
            )
        active = [state for state in states if state.get("phase")]
        if active:
            state = sorted(active, key=lambda value: value.get("updated", 0), reverse=True)[0]
            run_label = f"Run {state['index']}/{state.get('total', total_runs)}"
            phase = state.get("phase", "evaluating")
            tested = state.get("tested")
            planned = state.get("planned")
            if tested is not None and planned:
                phase = f"{phase} · {tested:,}/{planned:,} tested"
            self.optimizer_phase_value.setText(f"Current phase: {run_label} · {phase}")

    def _update_optimizer_run_state(self, message: str):
        ranking = re.search(r"WS ranking (\d+)/(\d+):\s*(.+)", message, re.I)
        if ranking:
            current, total = map(int, ranking.groups()[:2])
            state = self._optimizer_run_state.setdefault(
                1,
                {
                    "index": 1,
                    "total": 1,
                    "started_at": self._optimizer_started_at or time.monotonic(),
                    "improvement": "none yet",
                },
            )
            state["ranking_completed"] = max(0, current - 1)
            state["ranking_total"] = max(1, total)
            state["fraction"] = min(1.0, (current - 1) / max(1, total))
            state["phase"] = f"ranking WS {current - 1}/{total}"
            state["updated"] = time.monotonic()
            self._refresh_optimizer_status()
            return
        match = re.search(r"Search run (\d+)(?:/(\d+))?", message)
        if not match:
            return
        index = int(match.group(1))
        total = int(match.group(2) or 0)
        state = self._optimizer_run_state.setdefault(
            index,
            {
                "index": index,
                "total": total or 1,
                "started_at": self._optimizer_started_at or time.monotonic(),
                "improvement": "none yet",
            },
        )
        if total:
            state["total"] = total
        state["updated"] = time.monotonic()
        lowered = message.casefold()
        if "started" in lowered:
            state["phase"] = "starting"
        if "stopping" in lowered:
            state["phase"] = "stopping"
        if "completed" in lowered:
            state["phase"] = "completed"
            state["fraction"] = 1.0
        split_pass = re.search(r"split pass (\d+)/(\d+)", message, re.I)
        if split_pass:
            current_pass, total_passes = map(int, split_pass.groups())
            state["iteration"] = current_pass
            state["iterations"] = total_passes
            state["fraction"] = max(
                state.get("fraction", 0.0),
                (current_pass - 1) / max(1, total_passes),
            )
            state["phase"] = "merging worker chunks"
            if "merged" in lowered:
                state["fraction"] = max(
                    state["fraction"], current_pass / max(1, total_passes)
                )
        iteration = re.search(r"Iteration (\d+)/(\d+)", message)
        if iteration:
            current, total_iterations = map(int, iteration.groups())
            state["iteration"] = current
            state["iterations"] = total_iterations
            state["fraction"] = max(state.get("fraction", 0.0), (current - 1) / max(1, total_iterations))
            state["phase"] = "evaluating gear combinations"
        planned = re.search(r"planned ~([\d,]+)", message)
        if planned:
            state["planned"] = int(planned.group(1).replace(",", ""))
        tested = re.search(r"tested ([\d,]+)/([\d,]+)", message)
        if tested:
            state["tested"] = int(tested.group(1).replace(",", ""))
            state["planned"] = int(tested.group(2).replace(",", ""))
            iteration_total = state.get("iterations", 1)
            iteration = state.get("iteration", 1)
            within_iteration = min(1.0, state["tested"] / max(1, state["planned"]))
            state["fraction"] = max(
                state.get("fraction", 0.0),
                ((iteration - 1) + within_iteration) / max(1, iteration_total),
            )
        best = re.search(r"best (\d+(?:\.\d+)?)", message)
        if best:
            state["best"] = float(best.group(1))
        improvement = re.search(r"last improvement (.+?); best", message)
        if improvement:
            state["improvement"] = improvement.group(1).strip()
        results = re.search(r"current results:\s*(.+?)(?:\.\s*)?$", message, re.I)
        if results:
            state["results"] = f"Current results: {results.group(1).strip()}"
        phase = re.search(r"phase ([^;]+)", message)
        if phase:
            state["phase"] = phase.group(1).strip().rstrip(".")
        self._refresh_optimizer_status()

    def _ws_ranking_done(self, result: dict):
        self._optimizer_status_timer.stop()
        for state in self._optimizer_run_state.values():
            state["phase"] = "completed"
            state["fraction"] = 1.0
        self._refresh_optimizer_status()
        result = dict(result)
        result["skill_type"] = self._ranking_skill_in_progress or "Selected weapon"
        self._ranking_skill_in_progress = None
        errors = list(result.get("errors") or ())
        for error in errors:
            self._append_optimizer_log(
                f"Skipped {error.get('ws_name')} at {int(error.get('tp') or 0):,} TP: "
                f"{error.get('error')}"
            )
        success_count = sum(
            len(rows) for rows in (result.get("rankings") or {}).values()
        )
        self._append_optimizer_log(
            f"Weapon-skill ranking complete Â· {success_count} optimized results Â· "
            f"{len(errors)} skipped"
        )
        self.optimize_button.setEnabled(True)
        self.stop_optimizer_button.setEnabled(False)
        self.equip_best_button.setEnabled(False)
        self.show_top_sets_button.setEnabled(False)
        self.optimizer_activity.setText("Completed")
        self.optimizer_progress_value.setText("Approx. progress: 100.0%")
        self.optimizer_eta_value.setText("Estimated time remaining: complete")
        self.optimizer_best_value.setText("Best metric: see three-column ranking")
        self.optimizer_phase_value.setText("Current phase: finished")
        self.ws_ranking_dialog = WeaponSkillRankingDialog(result, self.icons, self)
        self.ws_ranking_dialog.show()
        self.statusBar().showMessage("Weapon-skill ranking completed", 5000)

    def _optimizer_done(self, result):
        self._optimizer_status_timer.stop()
        for state in self._optimizer_run_state.values():
            state["phase"] = "completed"
            state["fraction"] = 1.0
        self._refresh_optimizer_status()
        substat_summary = result[5] if isinstance(result, (tuple, list)) and len(result) > 5 else []
        self.best_player, _output, metric, winning_seed, self.optimizer_top_results = result[:5]
        self.best_tp_player = getattr(self.best_player, "tp_player", self.best_player)
        self.best_ws_player = getattr(self.best_player, "ws_player", self.best_player)
        self._last_completed_optimizer_action = self._optimizer_action_in_progress
        self._last_substat_summary = list(substat_summary or [])
        self.optimizer_top_results = list(self.optimizer_top_results or [])
        self._append_optimizer_log(
            f"Completed · metric {metric:.6f} · seed {winning_seed}"
        )
        self.optimize_button.setEnabled(True)
        self.stop_optimizer_button.setEnabled(False)
        self.equip_best_button.setEnabled(True)
        self.show_top_sets_button.setEnabled(bool(self.optimizer_top_results))
        self.optimizer_activity.setText("Completed")
        self.optimizer_progress_value.setText("Approx. progress: 100.0%")
        self.optimizer_eta_value.setText("Estimated time remaining: complete")
        self.optimizer_best_value.setText(f"Best metric: {metric:,.4f}")
        if self._last_substat_summary:
            for row in self._last_substat_summary:
                self._append_optimizer_log(
                    f"Priority {row['stat']}: {row['value']:,.1f} "
                    f"(damage {row['damage']:,.4f}; floor {row['damage_floor']:,.4f})"
                )
            self.optimizer_best_value.setText(
                f"Damage floor: {self._last_substat_summary[-1]['damage_floor']:,.4f}"
            )
        self.optimizer_phase_value.setText("Current phase: finished")
        self.statusBar().showMessage("Optimizer completed", 5000)

    def _optimizer_failed(self, message: str):
        self._optimizer_status_timer.stop()
        self._ranking_skill_in_progress = None
        for state in self._optimizer_run_state.values():
            if state.get("phase") not in {"completed", "stopped"}:
                state["phase"] = "failed"
        self._refresh_optimizer_status()
        self._append_optimizer_log(f"Failed: {message}")
        self.optimize_button.setEnabled(True)
        self.stop_optimizer_button.setEnabled(False)
        self.optimizer_activity.setText("Failed")
        self.optimizer_eta_value.setText("Estimated time remaining: unavailable")
        QMessageBox.critical(self, "Optimizer failed", message)

    def _optimizer_stopped(self, payload):
        self._optimizer_status_timer.stop()
        self._ranking_skill_in_progress = None
        for state in self._optimizer_run_state.values():
            if state.get("phase") != "completed":
                state["phase"] = "stopped"
        self._refresh_optimizer_status()
        if isinstance(payload, dict):
            message = str(payload.get("message") or "Optimizer stopped by user.")
            self.optimizer_top_results = list(payload.get("top_results") or [])
        else:
            message = str(payload)
            self.optimizer_top_results = []
        self._append_optimizer_log(f"Stopped: {message}")
        self.optimize_button.setEnabled(True)
        self.stop_optimizer_button.setEnabled(False)
        self.show_top_sets_button.setEnabled(bool(self.optimizer_top_results))
        self.optimizer_activity.setText("Stopped")
        self.optimizer_eta_value.setText("Estimated time remaining: stopped")
        self.optimizer_phase_value.setText("Current phase: stopped")
        self.statusBar().showMessage("Optimizer stopped", 5000)

    def stop_optimizer(self):
        if self.optimizer_thread and self.optimizer_thread.isRunning():
            self.optimizer_thread.request_stop()
            self.stop_optimizer_button.setEnabled(False)
            self.optimizer_activity.setText("Stopping...")
            for state in self._optimizer_run_state.values():
                if state.get("phase") not in {"completed", "failed"}:
                    state["phase"] = "stopping"
            self._refresh_optimizer_status()
            self._append_optimizer_log("Stop requested; finishing the current calculation...")

    def _optimizer_progress(self, message: str):
        ranking = re.search(r"WS ranking (\d+)/(\d+):\s*(.+)", message)
        if ranking:
            current, total = int(ranking.group(1)), int(ranking.group(2))
            self.optimizer_progress_value.setText(
                f"Approx. progress: {(current - 1) / max(1, total) * 100:.1f}%"
            )
            self.optimizer_phase_value.setText(
                f"Current phase: {ranking.group(3)}"
            )
        self._update_optimizer_run_state(message)
        if not re.search(r"Search run \d+", message):
            self._append_optimizer_log(message)
        if "started" in message.lower():
            self.optimizer_activity.setText("Running")

    def show_top_sets(self):
        if not self.optimizer_top_results:
            return
        self.top_sets_dialog = TopSetsDialog(self.optimizer_top_results, self.icons, self)
        self.top_sets_dialog.show()

    def load_optimizer_result(self, index: int, destination: str):
        """Load a selected best result into Quick Look or TP/WS editors."""
        if not 0 <= index < len(self.optimizer_top_results):
            return
        result = self.optimizer_top_results[index]
        tp_player = result.get("tp_player")
        ws_player = result.get("ws_player")
        if tp_player is None or ws_player is None:
            player = result.get("player")
            tp_player = player
            ws_player = player
        if tp_player is None or ws_player is None:
            return
        if destination == "tpws":
            self.tp_set.set_gearset(tp_player.gearset)
            self.ws_set.set_gearset(ws_player.gearset)
            self.tabs.setCurrentIndex(3)
            self.statusBar().showMessage("Loaded selected result into TP / WS sets", 5000)
        else:
            self.quick_set.set_gearset(tp_player.gearset)
            self.tabs.setCurrentIndex(0)
            self.statusBar().showMessage("Loaded selected TP result into Quick Look", 5000)

    def load_ws_ranking_result(self, entry: dict):
        player = entry.get("player")
        if player is None:
            return
        self.ws_set.set_gearset(player.gearset)
        self.ws_combo.setCurrentText(str(entry.get("ws_name") or "None"))
        self.tp_value.setValue(int(entry.get("tp") or 1000))
        self.tabs.setCurrentIndex(3)
        self.statusBar().showMessage(
            f"Loaded {entry.get('ws_name')} optimized WS set", 5000
        )

    def equip_best(self):
        if self.best_player is not None:
            self.quick_set.set_gearset(self.best_player.gearset)
            self.tabs.setCurrentIndex(0)

    def run_simulation(self):
        try:
            tp_player, enemy, _buffs, _abilities = self._context(self.tp_set.items)
            ws_player, _enemy, _buffs, _abilities = self._context(self.ws_set.items)
            actions.run_simulation(
                tp_player, ws_player, enemy, self.tp_value.value(),
                self.ws_combo.currentText(), self._ws_type(),
                self.plot_dps_checkbox.isChecked(),
            )
        except Exception as error:
            QMessageBox.critical(self, "Simulation failed", str(error))

    def plot_ws_distribution(self):
        if hasattr(self, "plot_thread") and self.plot_thread.isRunning():
            return
        try:
            player, enemy, _buffs, _abilities = self._context(self.ws_set.items)
            ws_name = self.ws_combo.currentText().strip()
            if not ws_name or ws_name == "None":
                raise ValueError("Select a weapon skill before creating a distribution plot.")
            effective_tp = actions.effective_ws_tp(self.tp_value.value(), player)
            tp_bonus = player.stats.get("TP Bonus", 0)
            self.plot_status.setText(
                f"Sampling 20,000 weapon skills at {self.tp_value.value():,} TP "
                f"+ {tp_bonus:,.0f} TP Bonus = {effective_tp:,.0f} effective TP..."
            )
            self.plot_thread = PlotThread(
                player, enemy, ws_name, self.tp_value.value(), self._ws_type(), parent=self
            )
            self.plot_thread.completed.connect(self._plot_distribution_done)
            self.plot_thread.failed.connect(self._plot_distribution_failed)
            self.plot_thread.start()
        except Exception as error:
            QMessageBox.critical(self, "Distribution plot", str(error))

    def _plot_distribution_done(self, damage):
        try:
            fancy_plot.plot_final(
                damage, self.plot_thread.player, self.plot_thread.tp_value,
                self.plot_thread.ws_name, icons_path=self.icons.plot_icon_sources(),
                items_file=APP_DIR / "item_list.csv",
            )
            self.plot_status.setText("Weapon-skill distribution plot complete.")
        except Exception as error:
            self._plot_distribution_failed(str(error))

    def _plot_distribution_failed(self, message: str):
        self.plot_status.setText(f"Distribution plot failed: {message}")
        QMessageBox.critical(self, "Distribution plot", message)

    def _load_enemy(self, name: str):
        enemy = enemies.preset_enemies.get(name)
        if enemy:
            for stat, spin in self.enemy_spins.items():
                spin.setValue(int(enemy.get(stat, 0)))


def main() -> int:
    multiprocessing.freeze_support()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("WSDist")
    app.setOrganizationName("WSDist")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
