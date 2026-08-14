"""Responsive PyQt6 interface for WSDist.

The permanent widget tree stays small. Large gear lists exist only while a
picker is open, avoiding the window-drag repaint cost of the legacy Tk UI.
The calculation and optimizer modules are reused without formula changes.
"""

from __future__ import annotations

import csv
import copy
import difflib
from html import escape
import json
import math
import multiprocessing
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import zipfile
from collections import OrderedDict
from pathlib import Path

import numpy as np

from PyQt6.QtCore import QEvent, QRect, QSettings, QSize, Qt, QStandardPaths, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction, QColor, QFont, QFontMetrics, QIcon, QKeySequence, QPainter, QPixmap,
    QSyntaxHighlighter, QTextCharFormat, QTextFormat,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QHeaderView, QInputDialog, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QDoubleSpinBox, QSpinBox, QSplitter,
    QSizePolicy, QStackedWidget, QStatusBar, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
    from matplotlib.figure import Figure
except ImportError:  # pragma: no cover - plotting remains optional for headless installs.
    FigureCanvas = None
    NavigationToolbar = None
    Figure = None

import actions
import buffs as buff_data
import create_player
import enemies
import fancy_plot
import gear
import wsdist
from equipment_rules import is_right_ear_only
from simulation_cache import SimulationCache, canonical_json, source_fingerprint
from result_history import ResultHistory
from wsdist_bridge import BridgeStore, bridge_hash
from lac_profile import (
    prepare_managed_update, prepare_profile_builder_update, write_managed_sets,
    write_profile_builder_sets, write_profile_source,
    write_reload_request,
)
from profile_builder import (
    GearSources, ProfileRecipe, bridge_candidates, build_profile_catalog, build_stat_set,
    child_seed, group_similar_ws_sets, pin_unmodeled_slots, optimizer_scenario,
    SET_SLOTS, weapon_category,
)


APP_DIR = Path(__file__).resolve().parent
CACHE_SOURCE_HASH = source_fingerprint([
            APP_DIR / name for name in (
        "gear.py", "equipment_rules.py", "actions.py", "attack_round_model.py", "create_player.py", "wsdist.py",
        "weaponskill_info.py", "get_hit_rate.py", "get_ma_rate.py", "get_fstr.py",
        "get_pdif.py", "get_phys_damage.py", "weapon_bonus.py", "get_tp.py",
        "nuking.py", "get_dint_m_v.py", "get_delay_timing.py", "enemies.py",
        "buffs.py",
    )
])
SLOTS = (
    "main", "sub", "ranged", "ammo", "head", "neck", "ear1", "ear2",
    "body", "hands", "ring1", "ring2", "back", "waist", "legs", "feet",
)
OPTIMIZER_RUN_COLORS = (
    "#d6ad68", "#c995a9", "#aaa3bc", "#c5b486", "#9d96ad",
    "#d0a28c", "#b9af9c", "#ad8f9e", "#c7b27e", "#958ea3",
)
SEARCH_QUALITY = {
    "Fast": {"restarts": 6, "passes": 4, "shared_start": True},
    "Standard": {"restarts": 10, "passes": 10, "shared_start": True},
    "Deep": {"restarts": 12, "passes": 10, "shared_start": False},
}
SEARCH_QUALITY_NAMES = tuple(SEARCH_QUALITY)
# The same three enemy tiers used by Profile Builder's Default, Accuracy, and
# High Accuracy scenarios.  Graphs may compare the active enemy against these
# references without changing the selected optimizer scenario.
PROFILE_REFERENCE_ENEMIES = (
    "Apex Toad",
    "Apex Knight Lugcrawler",
    "Apex Archaic Cogs",
)


def _reference_enemy_names(current_name: str, enabled: bool = True) -> tuple[str, ...]:
    """Return unique graph labels, keeping the active enemy first."""
    current = str(current_name or "").strip()
    names = [current] if current else []
    if enabled:
        names.extend(name for name in PROFILE_REFERENCE_ENEMIES if name != current)
    return tuple(dict.fromkeys(names))


def _normalized_search_quality(value: str, *, legacy_deep: bool = False) -> str:
    """Normalize persisted search quality, migrating the former Deep tier."""
    text = str(value or "").strip().title()
    if legacy_deep and text == "Deep":
        return "Standard"
    return text if text in SEARCH_QUALITY else "Fast"


def _search_quality_settings(value: str) -> tuple[int, int, bool]:
    policy = SEARCH_QUALITY[_normalized_search_quality(value)]
    return int(policy["passes"]), int(policy["restarts"]), bool(policy["shared_start"])
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


WS_BY_SKILL = {
    "Katana": ["Blade: Retsu", "Blade: Teki", "Blade: To", "Blade: Chi", "Blade: Ei", "Blade: Jin", "Blade: Ten", "Blade: Ku", "Blade: Yu", "Blade: Metsu", "Blade: Kamu", "Blade: Hi", "Blade: Shun", "Zesho Meppo"],
    "Great Katana": ["Tachi: Enpi", "Tachi: Goten", "Tachi: Kagero", "Tachi: Jinpu", "Tachi: Koki", "Tachi: Yukikaze", "Tachi: Gekko", "Tachi: Kasha", "Tachi: Ageha", "Tachi: Kaiten", "Tachi: Rana", "Tachi: Fudo", "Tachi: Shoha", "Tachi: Mumei"],
    "Dagger": ["Viper Bite", "Dancing Edge", "Shark Bite", "Evisceration", "Aeolian Edge", "Mercy Stroke", "Mandalic Stab", "Mordant Rime", "Pyrrhic Kleos", "Rudra's Storm", "Exenterator", "Ruthless Stroke"],
    "Sword": ["Fast Blade", "Fast Blade II", "Burning Blade", "Red Lotus Blade", "Seraph Blade", "Circle Blade", "Swift Blade", "Savage Blade", "Sanguine Blade", "Knights of Round", "Death Blossom", "Expiacion", "Chant du Cygne", "Requiescat", "Imperator"],
    "Scythe": ["Slice", "Dark Harvest", "Shadow of Death", "Nightmare Scythe", "Spinning Scythe", "Guillotine", "Cross Reaper", "Spiral Hell", "Infernal Scythe", "Catastrophe", "Quietus", "Insurgency", "Entropy", "Origin"],
    "Great Sword": ["Hard Slash", "Freezebite", "Shockwave", "Sickle Moon", "Spinning Slash", "Ground Strike", "Herculean Slash", "Resolution", "Scourge", "Dimidiation", "Torcleaver", "Fimbulvetr"],
    "Club": ["Shining Strike", "Seraph Strike", "Skullbreaker", "True Strike", "Judgment", "Hexa Strike", "Black Halo", "Randgrith", "Exudation", "Mystic Boon", "Realmrazer", "Dagda"],
    "Polearm": ["Double Thrust", "Thunder Thrust", "Raiden Thrust", "Penta Thrust", "Wheeling Thrust", "Impulse Drive", "Sonic Thrust", "Geirskogul", "Drakesbane", "Camlann's Torment", "Stardiver", "Diarmuid"],
    "Staff": ["Heavy Swing", "Rock Crusher", "Earth Crusher", "Starburst", "Sunburst", "Shell Crusher", "Full Swing", "Cataclysm", "Retribution", "Gate of Tartarus", "Omniscience", "Vidohunir", "Garland of Bliss", "Shattersoul", "Oshala"],
    "Great Axe": ["Iron Tempest", "Shield Break", "Armor Break", "Weapon Break", "Raging Rush", "Full Break", "Steel Cyclone", "Fell Cleave", "Metatron Torment", "King's Justice", "Ukko's Fury", "Upheaval", "Disaster"],
    "Axe": ["Raging Axe", "Spinning Axe", "Rampage", "Calamity", "Mistral Axe", "Decimation", "Bora Axe", "Onslaught", "Primal Rend", "Cloudsplitter", "Ruinator", "Blitz"],
    "Archery": ["Flaming Arrow", "Piercing Arrow", "Dulling Arrow", "Sidewinder", "Blast Arrow", "Empyreal Arrow", "Refulgent Arrow", "Namas Arrow", "Jishnu's Radiance", "Apex Arrow", "Sarv"],
    "Marksmanship": ["Hot Shot", "Split Shot", "Sniper Shot", "Slug Shot", "Blast Shot", "Detonator", "Coronach", "Leaden Salute", "Trueflight", "Wildfire", "Last Stand", "Terminus"],
    "Hand-to-Hand": ["Combo", "One Inch Punch", "Raging Fists", "Spinning Attack", "Howling Fist", "Dragon Kick", "Asuran Fists", "Tornado Kick", "Ascetic's Fury", "Stringing Pummel", "Final Heaven", "Victory Smite", "Shijin Spiral", "Maru Kala", "Dragon Blow"],
    "None": ["None"],
}
REMA_WEAPON_NAMES = frozenset({
    "Amanomurakumo", "Annihilator", "Apocalypse", "Bravura", "Excalibur", "Gungnir", "Guttler", "Kikoku", "Mandau", "Mjollnir", "Ragnarok", "Spharai", "Yoichinoyumi", "Almace", "Armageddon", "Caladbolg", "Farsha", "Gandiva", "Kannagi", "Masamune", "Redemption", "Rhongomiant", "Twashtar", "Ukonvasara", "Verethragna", "Hvergelmir", "Aymur", "Burtgang", "Carnwenhan", "Conqueror", "Death Penalty", "Gastraphetes", "Glanzfaust", "Kenkonken", "Kogarasumaru", "Laevateinn", "Liberator", "Murgleis", "Nagi", "Ryunohige", "Terpsichore", "Tizona", "Tupsimati", "Nirvana", "Vajra", "Yagrush", "Epeolatry", "Idris", "Aeneas", "Anguta", "Chango", "Dojikiri Yasutsuna", "Fail-not", "Fomalhaut", "Godhands", "Heishi Shorinken", "Khatvanga", "Lionheart", "Sequence", "Tishtrya", "Tri-edge", "Trishula",
})
SPELLS_BY_JOB = {
    "nin": [f"{element}: {tier}" for element in ("Doton", "Suiton", "Huton", "Katon", "Hyoton", "Raiton") for tier in ("Ichi", "Ni", "San")] + ["Ranged Attack"],
    "blm": [spell for spell in ("Stone", "Stone II", "Stone III", "Stone IV", "Stone V", "Stone VI", "Stoneja", "Water", "Water II", "Water III", "Water IV", "Water V", "Water VI", "Waterja", "Aero", "Aero II", "Aero III", "Aero IV", "Aero V", "Aero VI", "Aeroja", "Fire", "Fire II", "Fire III", "Fire IV", "Fire V", "Fire VI", "Firaja", "Blizzard", "Blizzard II", "Blizzard III", "Blizzard IV", "Blizzard V", "Blizzard VI", "Blizzaja", "Thunder", "Thunder II", "Thunder III", "Thunder IV", "Thunder V", "Thunder VI", "Thundaja", "Impact", "Ranged Attack")],
    "rdm": ["EnSpell", "Stone", "Stone II", "Stone III", "Stone IV", "Stone V", "Water", "Water II", "Water III", "Water IV", "Water V", "Aero", "Aero II", "Aero III", "Aero IV", "Aero V", "Fire", "Fire II", "Fire III", "Fire IV", "Fire V", "Blizzard", "Blizzard II", "Blizzard III", "Blizzard IV", "Blizzard V", "Thunder", "Thunder II", "Thunder III", "Impact", "Ranged Attack"],
    "geo": ["Stone", "Stone II", "Stone III", "Stone IV", "Stone V", "Water", "Water II", "Water III", "Water IV", "Water V", "Aero", "Aero II", "Aero III", "Aero IV", "Aero V", "Fire", "Fire II", "Fire III", "Fire IV", "Fire V", "Blizzard", "Blizzard II", "Blizzard III", "Blizzard IV", "Blizzard V", "Thunder", "Thunder II", "Thunder III", "Impact"],
    "sch": ["Stone", "Stone II", "Stone III", "Stone IV", "Stone V", "Geohelix II", "Water", "Water II", "Water III", "Water IV", "Water V", "Hydrohelix II", "Aero", "Aero II", "Aero III", "Aero IV", "Anemohelix II", "Fire", "Fire II", "Fire III", "Fire IV", "Pyrohelix II", "Blizzard", "Blizzard II", "Blizzard III", "Blizzard IV", "Cryohelix II", "Thunder", "Thunder II", "Thunder III", "Ionohelix II", "Luminohelix II", "Noctohelix II", "Kaustra", "Impact"],
    "drk": [f"{element}{suffix}" for element in ("Stone", "Water", "Aero", "Fire", "Blizzard") for suffix in ("", " II", " III")] + ["Thunder", "Thunder II", "Thunder III", "Impact"],
    "cor": ["Ranged Attack", "Earth Shot", "Water Shot", "Wind Shot", "Fire Shot", "Ice Shot", "Thunder Shot"],
    "rng": ["Ranged Attack"], "sam": ["Ranged Attack"], "thf": ["Ranged Attack"],
}
MAGIC_DAMAGE_TYPES = ("Elemental Magic", "Ninjutsu", "Quick Draw", "Ranged Attack", "EnSpell")
ELEMENTAL_DAMAGE_SPELLS = frozenset({
    "Stone", "Stone II", "Stone III", "Stone IV", "Stone V", "Stone VI", "Stoneja",
    "Water", "Water II", "Water III", "Water IV", "Water V", "Water VI", "Waterja",
    "Aero", "Aero II", "Aero III", "Aero IV", "Aero V", "Aero VI", "Aeroja",
    "Fire", "Fire II", "Fire III", "Fire IV", "Fire V", "Fire VI", "Firaja",
    "Blizzard", "Blizzard II", "Blizzard III", "Blizzard IV", "Blizzard V", "Blizzaja",
    "Thunder", "Thunder II", "Thunder III", "Thunder IV", "Thunder V", "Thundaja",
    "Geohelix II", "Hydrohelix II", "Anemohelix II", "Pyrohelix II",
    "Cryohelix II", "Ionohelix II", "Luminohelix II", "Noctohelix II", "Kaustra", "Impact",
})
ENFEEBLING_SPELLS = (
    "Slow", "Slow II", "Paralyze", "Paralyze II", "Silence", "Blind", "Blind II",
    "Addle", "Addle II", "Gravity", "Gravity II", "Distract", "Distract II",
    "Frazzle", "Frazzle II", "Sleep", "Sleep II", "Sleepga", "Sleepga II",
    "Bind", "Break", "Breakga", "Dispel", "Dia", "Dia II", "Dia III",
    "Inundation", "Poison", "Poison II", "Poisonga", "Bio", "Bio II", "Bio III",
    "Elegy", "Requiem",
)
SELF_BUFF_FAMILIES = {
    "Enhancing magic": {
        "variants": ("Enhancing Magic", "Haste", "Refresh", "Phalanx", "Temper"),
        "objective": ("Enhancing Magic Skill", "Enhancing Magic Duration", "Fast Cast", "DT"),
        "caps": (("Fast Cast", 80),),
        "note": "Prioritizes the 80% total Fast Cast target, then enhancing skill and duration.",
    },
    "Geomancy spell": {
        "variants": ("Indi spell", "Geo spell", "Entrust spell"),
        "objective": ("Geomancy Skill", "Geomancy Duration", "Geomancy Potency", "Fast Cast", "Magic Accuracy"),
        "caps": (),
        "note": "Prioritizes modeled geomancy skill, duration, potency, and casting speed.",
    },
    "BRD song": {
        "variants": tuple(str(name) for name in buff_data.brd),
        "objective": ("Singing Skill", "Wind Instrument Skill", "String Instrument Skill", "Song Duration", "Fast Cast"),
        "caps": (),
        "note": "Prioritizes song skill, instrument skill, duration, and casting speed.",
    },
    "COR roll": {
        "variants": tuple(f"{name} Roll" for name in buff_data.cor),
        "objective": ("Phantom Roll", "Roll Duration", "Fast Cast", "Enmity"),
        "caps": (),
        "note": "Prioritizes modeled roll potency/duration and casting speed.",
    },
}
AUTO_WEAPON_TYPE = "Auto (equipped weapon)"
RANGED_WEAPON_TYPES = frozenset(("Archery", "Marksmanship"))
WEAPON_TYPE_OPTIONS = [
    AUTO_WEAPON_TYPE,
    *sorted(
        (str(skill) for skill in WS_BY_SKILL if str(skill) not in {"None", "Instrument"}),
        key=str.casefold,
    ),
]

def weapon_skill_choices(weapon_type: str, gearset: dict | None = None) -> list[str]:
    """Return WS choices from an explicit skill family, not a temporary gearset."""
    selected = str(weapon_type or AUTO_WEAPON_TYPE)
    if selected == AUTO_WEAPON_TYPE:
        skills = []
        for slot in ("main", "ranged"):
            skill = (gearset or {}).get(slot, {}).get("Skill Type", "None")
            if skill and skill != "None":
                skills.extend(WS_BY_SKILL.get(skill, ()))
        if not skills:
            skills = [
                value for skill, values in WS_BY_SKILL.items()
                if skill != "None" for value in values if value != "None"
            ]
    else:
        skills = list(WS_BY_SKILL.get(selected, ()))
    return list(dict.fromkeys(("None", *skills)))


def magic_damage_spell_choices(spell_type: str) -> list[str]:
    """Return names supported by the selected spell-damage formula."""
    names = {
        str(name)
        for values in SPELLS_BY_JOB.values()
        for name in values
        if str(name) != "None"
    }
    selected = str(spell_type or "Elemental Magic")
    if selected == "Elemental Magic":
        names &= ELEMENTAL_DAMAGE_SPELLS
    elif selected == "Ninjutsu":
        names = {name for name in names if ":" in name}
    elif selected == "Quick Draw":
        names = {name for name in names if name.split()[-1:] == ["Shot"]}
    elif selected == "Ranged Attack":
        names = {"Ranged Attack"}
    elif selected == "EnSpell":
        names = {"EnSpell"}
    return ["None", *sorted(names, key=str.casefold)]


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
# Item level is an armor requirement here. Weapons use progression systems
# that are not represented reliably by their resource ItemLevel value.
ITEM_LEVEL_FILTER_SLOTS = ("head", "body", "hands", "legs", "feet")
SUBSTAT_OPTIONS = (
    "None", "Magic Evasion", "Evasion", "Defense", "Magic Defense",
    "Subtle Blow", "Counter", "Store TP", "Accuracy", "Magic Accuracy",
    "Attack", "Magic Attack", "HP", "MP", "Enmity",
)
WARM_CACHE_ACTION = "Warm cache - WS ranking (1k/2k/3k TP)"
OPTIMIZER_STATE_LABELS = {
    "idle": "READY",
    "starting": "STARTING",
    "running": "SIMULATION RUNNING",
    "warming": "CACHE RUNNING",
    "stopping": "STOPPING",
    "completed": "COMPLETE",
    "restored": "RESTORED",
    "stopped": "STOPPED",
    "failed": "FAILED",
}


def _item_level_candidate_allowed(slot: str, level: int | None, enabled: bool) -> bool:
    """Return whether the armor-level filter permits one optimizer item."""
    return (
        not enabled
        or slot not in ITEM_LEVEL_FILTER_SLOTS
        or level is None
        or level >= 119
    )


def _gearset_payload(gearset: dict) -> dict[str, dict]:
    """Copy plain item data so cached results never hold live player objects."""
    return {
        slot: dict(item) if isinstance(item, dict) else dict(gear.Empty)
        for slot, item in gearset.items()
    }


def _json_value(value):
    """Convert calculator tuples and NumPy scalars into history-safe values."""
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _json_value(value.item())
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _quick_cache_request(action: str, gearset: dict, *, main_job: str,
                         sub_job: str, master_level: int, buffs: dict,
                         abilities: dict, enemy: dict, tp: int = 0,
                         ws_name: str = "", ws_type: str = "",
                         spell_name: str = "", spell_type: str = "") -> dict:
    """Build the one canonical identity used by Quick Look and cache warming."""
    # ``tp`` was the label used by the original Quick Look button. Keep it
    # accepted for saved UI state while the calculation API uses ``attack``.
    action = "attack" if action == "tp" else action
    request = {
        "action": action,
        "gearset": _gearset_payload(gearset),
        "main_job": main_job,
        "sub_job": sub_job,
        "master_level": master_level,
        "buffs": buffs,
        "abilities": abilities,
        "enemy": enemy,
    }
    if action == "attack":
        request["tp"] = tp
    elif action == "ws":
        request.update({"tp": tp, "ws_name": ws_name, "ws_type": ws_type})
    elif action == "spell":
        request.update({"spell_name": spell_name, "spell_type": spell_type})
    else:
        raise ValueError(f"Unknown Quick Look action: {action}")
    return request


def _evaluate_quick_result(player, enemy, action: str, *, tp: int = 0,
                           ws_name: str = "", ws_type: str = "",
                           spell_name: str = "", spell_type: str = ""):
    """Return the exact output/text shape consumed by Quick Look cache hits."""
    action = "attack" if action == "tp" else action
    if action == "ws":
        output = actions.average_ws(
            player, enemy, ws_name, tp, ws_type, "Damage dealt"
        )
        text = f"Average damage: {output[1][0]:,.0f}    TP return: {output[1][1]:,.1f}"
    elif action == "spell":
        output = actions.cast_spell(
            player, enemy, spell_name, spell_type, "Damage dealt"
        )
        text = f"Average damage: {output[1][0]:,.0f}    TP return: {output[1][1]:,.1f}"
    elif action == "attack":
        output = actions.average_attack_round(player, enemy, 0, tp, "Time to WS")
        text = f"Time per WS: {output[0]:,.3f}s    TP per round: {output[1][1]:,.1f}"
    else:
        raise ValueError(f"Unknown Quick Look action: {action}")
    return output, text


def _quick_result_chart_data(action: str, output, *, tp_target: int = 1000) -> dict | None:
    """Normalize a deterministic Quick Look result for its inline chart."""
    action = "attack" if action == "tp" else action
    try:
        values = list(output or ())
        details = list(values[1] or ())
        if action == "attack" and len(values) >= 2 and len(details) >= 3:
            time_to_ws = max(0.0, float(values[0]))
            damage = max(0.0, float(details[0]))
            tp_per_round = max(0.0, float(details[1]))
            round_time = max(0.0, float(details[2]))
            magical_damage = max(0.0, float(values[2])) if len(values) >= 3 else 0.0
            target = max(1.0, float(tp_target or 1000))
            return {
                "kind": "tp_pace",
                "time_to_ws": time_to_ws,
                "target_tp": target,
                "damage_per_round": damage,
                "tp_per_round": tp_per_round,
                "round_time": round_time,
                "physical_damage": max(0.0, damage - magical_damage),
                "magical_damage": min(damage, magical_damage),
            }
        if action in {"ws", "spell"} and len(details) >= 2:
            return {
                "kind": "action_result",
                "action": action,
                "damage": max(0.0, float(details[0])),
                "tp_return": max(0.0, float(details[1])),
            }
    except (TypeError, ValueError, IndexError):
        return None
    return None


def _ws_distribution_chart_data(distribution: dict | None) -> dict | None:
    """Validate and normalize a sampled WS histogram for an inline graph."""
    try:
        distribution = distribution or {}
        histogram = distribution.get("histogram") or {}
        edges = np.asarray(histogram.get("edges") or [], dtype=float)
        counts = np.asarray(histogram.get("counts") or [], dtype=float)
        samples = int(distribution.get("samples") or np.sum(counts))
        summary_values = np.asarray([
            distribution.get("mean", 0), distribution.get("median", 0),
            distribution.get("p05", 0), distribution.get("p95", 0),
        ], dtype=float)
        if (
            len(edges) != len(counts) + 1 or not len(counts) or samples <= 0
            or not np.isfinite(edges).all() or not np.isfinite(counts).all()
            or not np.isfinite(summary_values).all()
            or np.any(np.diff(edges) <= 0) or np.any(counts < 0)
        ):
            return None
        return {
            "edges": edges,
            "counts": counts,
            "samples": samples,
            "mean": float(summary_values[0]),
            "median": float(summary_values[1]),
            "p05": float(summary_values[2]),
            "p95": float(summary_values[3]),
        }
    except (TypeError, ValueError, OverflowError):
        return None


def _dps_series_chart_data(summary: dict | None) -> dict | None:
    """Validate the two-hour convergence series before plotting it in Qt."""
    try:
        summary = summary or {}
        source = summary.get("dps_series") or {}
        result = {}
        for name, time_name in (("total", "time"), ("tp", "tp_time"), ("ws", "ws_time")):
            times = np.asarray(source.get(time_name) or [], dtype=float)
            values = np.asarray(source.get(name) or [], dtype=float)
            if len(times) != len(values) or not len(times):
                continue
            finite = np.isfinite(times) & np.isfinite(values)
            if np.any(finite):
                result[name] = (times[finite], values[finite])
        return result or None
    except (TypeError, ValueError):
        return None


def _remaining_time_estimate(samples: list[tuple[float, float]], *,
                             elapsed: float, progress: float) -> float | None:
    """Estimate remaining work from a stable blend of recent and total progress."""
    progress = max(0.0, min(1.0, float(progress)))
    elapsed = max(0.0, float(elapsed))
    if progress >= 1.0:
        return 0.0
    if progress <= 0.001 or elapsed < 1.0:
        return None
    global_rate = progress / elapsed
    recent_rate = None
    useful = [
        (float(moment), float(fraction)) for moment, fraction in samples
        if moment >= 0 and 0 <= fraction <= progress
    ]
    if len(useful) >= 2:
        newest_time, newest_progress = useful[-1]
        # Prefer roughly the last minute, but keep at least one older sample.
        oldest_time, oldest_progress = useful[0]
        for candidate_time, candidate_progress in reversed(useful[:-1]):
            oldest_time, oldest_progress = candidate_time, candidate_progress
            if newest_time - candidate_time >= 45:
                break
        delta_time = newest_time - oldest_time
        delta_progress = newest_progress - oldest_progress
        if delta_time >= 2 and delta_progress > 0.0001:
            recent_rate = delta_progress / delta_time
    rate = global_rate if recent_rate is None else 0.65 * recent_rate + 0.35 * global_rate
    if rate <= 0:
        return None
    estimate = (1.0 - progress) / rate
    # Prevent one sparse worker update from producing a wildly misleading ETA.
    return min(estimate, elapsed * 20 + 3600)


def _optimizer_current_result_lines(value: str | None) -> str:
    """Put unlike live-result metrics on separate, scannable lines."""
    text = str(value or "waiting for a valid set").strip()
    text = re.sub(r"^current results:\s*", "", text, flags=re.I)
    parts = [part.strip().rstrip(".") for part in text.split(";") if part.strip()]
    return "\n".join(f"• {part}" for part in parts) or "• waiting for a valid set"


def _reduction_text(value) -> str:
    """Present the optimizer's negative damage-taken convention as a reduction."""
    try:
        return f"{max(0.0, -float(value)):g}%"
    except (TypeError, ValueError):
        return "0%"


def _optimizer_result_summary(action_type: str, output, metric: float,
                              metric_name: str = "", ws_name: str = "") -> str:
    """Return player-facing combat results instead of an internal score."""
    try:
        values = list(output or ())
        if action_type == "attack round" and len(values) >= 3:
            damage, tp_per_round, round_time = map(float, values[:3])
            parts = [
                f"Melee {damage / round_time:,.1f} DPS" if round_time > 0 else "Melee DPS unavailable",
                f"{damage:,.0f} damage/round",
                f"{tp_per_round:,.1f} TP/round",
                f"{round_time:.2f}s/round",
            ]
            if metric_name == "Time to WS" and metric > 0:
                parts.append(f"{1.0 / metric:.1f}s to WS")
            return " · ".join(parts)
        if action_type == "combined tp/ws" and len(values) >= 5:
            return (
                f"TP+WS {float(metric):,.1f} DPS · {float(values[2]):.1f}s to WS · "
                f"{float(values[3]):,.0f} {ws_name or 'WS'} damage · {float(values[4]):.1f}s cycle"
            )
        if action_type in {"weapon skill", "spell cast"} and len(values) >= 2:
            label = ws_name or ("Spell" if action_type == "spell cast" else "WS")
            return f"{label}: {float(values[0]):,.0f} average damage · {float(values[1]):,.1f} TP return"
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return "Simulation completed; detailed combat breakdown is unavailable."


def _optimizer_defense_summary(player) -> dict:
    """Get persisted optimizer defense data or calculate it for older results."""
    stored = getattr(player, "optimizer_defense", None)
    if isinstance(stored, dict) and isinstance(stored.get("actual"), dict):
        return stored
    try:
        gearset = player.gearset
        totals = create_player.damage_taken_totals(gearset, player.buffs)
        pdt, mdt = create_player.damage_taken_from_totals(
            totals, gearset["main"], player.abilities
        )
        dt = create_player.damage_taken_dt_from_totals(
            totals, gearset["main"], player.abilities
        )
        return {"requested": {}, "actual": {"PDT": pdt, "MDT": mdt, "DT": dt}, "fallback": False}
    except (AttributeError, KeyError, TypeError):
        return {"requested": {}, "actual": {}, "fallback": False}


def _serialize_optimizer_player(player) -> dict:
    def metadata(value) -> dict:
        defense = getattr(value, "optimizer_defense", None)
        return {"optimizer_defense": defense} if isinstance(defense, dict) else {}

    if isinstance(player, wsdist.CombinedSetResult):
        return {
            "combined": True,
            "tp_gearset": _gearset_payload(player.tp_player.gearset),
            "ws_gearset": _gearset_payload(player.ws_player.gearset),
            "tp_metadata": metadata(player.tp_player),
            "ws_metadata": metadata(player.ws_player),
        }
    return {
        "combined": False,
        "gearset": _gearset_payload(player.gearset),
        "metadata": metadata(player),
    }


def _cached_player(data: dict, context: dict):
    def build(gearset, metadata=None):
        player = create_player.create_player(
            context["main_job"], context["sub_job"], context["master_level"],
            gearset=gearset, buffs=context["buffs"], abilities=context["abilities"],
        )
        defense = (metadata or {}).get("optimizer_defense")
        if isinstance(defense, dict):
            player.optimizer_defense = defense
        return player
    if data.get("combined"):
        return wsdist.CombinedSetResult(
            build(data["tp_gearset"], data.get("tp_metadata")),
            build(data["ws_gearset"], data.get("ws_metadata")),
        )
    return build(data["gearset"], data.get("metadata"))


def _serialize_top_results(results: list[dict]) -> list[dict]:
    saved = []
    for entry in results:
        row = {}
        for key, value in entry.items():
            if key == "player" and value is not None:
                row[key] = {"__player__": _serialize_optimizer_player(value)}
            elif key in {"tp_player", "ws_player"} and value is not None:
                row[key] = {"__player__": _serialize_optimizer_player(value)}
            else:
                row[key] = value
        saved.append(row)
    return saved


def _restore_top_results(results: list[dict], context: dict) -> list[dict]:
    restored = []
    for entry in results or ():
        row = dict(entry)
        for key in ("player", "tp_player", "ws_player"):
            wrapped = row.get(key)
            if isinstance(wrapped, dict) and "__player__" in wrapped:
                row[key] = _cached_player(wrapped["__player__"], context)
        restored.append(row)
    return restored


def _optimizer_result_players(result: dict):
    """Return the TP/WS pair whether stored explicitly or in a combined wrapper."""
    player = result.get("player")
    tp_player = result.get("tp_player") or getattr(player, "tp_player", None) or player
    ws_player = result.get("ws_player") or getattr(player, "ws_player", None) or player
    return tp_player, ws_player


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


PROFILE_VARIANTS = {
    "default": "Default", "acc": "Acc", "highacc": "HighAcc",
    "hybrid": "Hybrid", "hybridacc": "HybridAcc",
    "hybridhighacc": "HybridHighAcc", "wsdist": "WSDist",
}


def _profile_set_descriptor(set_name: str, metadata: dict | None = None) -> dict:
    """Classify one raw LAC set without treating partial layers as full sets."""
    supplied = metadata if isinstance(metadata, dict) else {}
    pieces = [piece for piece in re.split(r"_+", set_name) if piece]
    lowered = [piece.casefold() for piece in pieces]
    variant_index = next((i for i, value in enumerate(lowered) if value in PROFILE_VARIANTS), None)
    variant = PROFILE_VARIANTS.get(lowered[variant_index], "Default") if variant_index is not None else "Default"
    variant_end = variant_index + 1 if variant_index is not None else 1
    if variant == "Hybrid" and variant_end < len(lowered):
        if lowered[variant_end] == "acc":
            variant = "HybridAcc"
            variant_end += 1
        elif lowered[variant_end] == "highacc":
            variant = "HybridHighAcc"
            variant_end += 1
    family = "_".join(pieces[:variant_index]) if variant_index is not None else (pieces[0] if pieces else set_name)
    modifiers = pieces[variant_end:] if variant_index is not None else pieces[1:]
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
    ignored = {
        "Name", "Name2", "Jobs", "Slots", "Bridge Key", "Eligible",
        "Shared Only", "Shared Characters", "Aspirational Only", "Model Warning",
        "Unknown Augments", "Item ID", "Accessible Count", "Total Count",
        "Model Complete", "Item Level", "ItemLevel", "Rank", "Rare", "Exclusive",
        "Transferable", "Porter Only", "LAC", "Augments", "Resource Flags",
    }
    lines = [str(item.get("Name") or item_name(item))]
    for key, value in item.items():
        if key not in ignored and value not in (None, "", 0, False, [], {}):
            lines.append(f"{key}: {value}")
    if item.get("Model Warning"):
        lines.append(f"WARNING: {item['Model Warning']}")
    return "\n".join(lines)


def item_detail_stats(item: dict, *, limit: int = 7) -> list[str]:
    """Return player-facing item stats, excluding inventory/catalog metadata."""
    ignored = {
        "Name", "Name2", "Jobs", "Slots", "Bridge Key", "Eligible",
        "Shared Only", "Shared Characters", "Aspirational Only", "Model Warning",
        "Unknown Augments", "Item ID", "Accessible Count", "Total Count",
        "Model Complete", "Item Level", "ItemLevel", "Rank", "Type", "Skill Type",
        "Resource Flags", "Model Source", "Source", "Augment Path", "Rare",
        "Exclusive", "Transferable", "LAC", "Augments", "Data Source",
        "Porter Only",
    }
    priority = (
        "DMG", "Damage", "Delay", "Accuracy", "Attack", "Ranged Accuracy",
        "Ranged Attack", "Magic Accuracy", "Magic Attack", "Magic Damage",
        "STR", "DEX", "VIT", "AGI", "INT", "MND", "CHR", "HP", "MP",
        "Defense", "Evasion", "Magic Evasion", "Magic Defense", "Gear Haste", "DT",
    )
    available = {
        key: value for key, value in item.items()
        if key not in ignored and value not in (None, "", 0, False, [], {})
        and isinstance(value, (int, float, str))
    }
    ordered = [key for key in priority if key in available]
    ordered.extend(key for key in available if key not in ordered)
    values = []
    for key in ordered[:limit]:
        value = available[key]
        display_key = {"Gear Haste": "Haste"}.get(key, key)
        if display_key == "Haste" and isinstance(value, (int, float)):
            values.append(f"{display_key}: {value:g}%")
        else:
            values.append(f"{display_key}: {value:g}" if isinstance(value, float) else f"{display_key}: {value}")
    return values


def _is_r15_variant(item: dict) -> bool:
    """Recognize both modeled R15 names and bridge-export rank labels."""
    text = " ".join(str(item.get(key) or "") for key in ("Name", "Name2"))
    return bool(re.search(r"\br15\b|rank\s*=\s*15", text, re.IGNORECASE))


def _aspirational_catalog() -> dict[str, dict]:
    """Return the legacy modeled catalog grouped by gear variant name."""
    catalog: dict[str, dict] = {}
    for slot, items in _base_equipment().items():
        for item in items:
            name = item_name(item)
            if name == "Empty":
                continue
            entry = catalog.setdefault(name, {"item": item, "slots": set()})
            entry["slots"].add(slot)
    return catalog


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
        self._bridge_icon_dirs: tuple[Path, ...] = ()
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

    def set_bridge_icon_dirs(self, directories):
        """Use all discovered character exports for shared/transferable gear."""
        resolved = tuple(dict.fromkeys(
            path.resolve() for path in (directories or ())
            if path and Path(path).exists()
        ))
        if resolved != self._bridge_icon_dirs:
            self._bridge_icon_dirs = resolved
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
        roots.extend(self._bridge_icon_dirs)
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
        # Profiles can retain an older-stage REMA resource ID while the owned
        # inventory/export has the current 119 III ID. If the exact historical
        # icon is unavailable, fall back to the canonical name's current icon
        # instead of showing an empty Masamune (or equivalent upgrade).
        if icon.isNull():
            fallback_ids = []
            for key in (item.get("Name"), item.get("Name2")):
                fallback_id = self._item_ids.get(str(key or "").casefold())
                if fallback_id and fallback_id != item_id and fallback_id not in fallback_ids:
                    fallback_ids.append(fallback_id)
            for fallback_id in fallback_ids:
                for root in roots:
                    for extension in ("png", "bmp", "ico"):
                        path = root / f"{fallback_id}.{extension}"
                        if path.is_file():
                            icon = QIcon(str(path))
                            break
                    if not icon.isNull():
                        break
                if icon.isNull():
                    icon = self._archive_icon(fallback_id)
                if not icon.isNull():
                    break
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
    blacklist_requested = pyqtSignal(str)

    def __init__(self, slot: str, items: list[dict], selected: set[str],
                 icons: GearIconProvider, locked_name: str = "", parent=None,
                 *, blacklisted_items: list[dict] | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Optimizer candidates: {slot.title()}")
        self.setMinimumSize(520, 440)
        self.resize(600, 680)
        self._items = sorted(items, key=lambda value: item_name(value).lower())
        self._player_items = [
            item for item in self._items
            if not item.get("Shared Only") and not item.get("Aspirational Only")
            and not item.get("Porter Only")
        ]
        self._porter_items = [item for item in self._items if item.get("Porter Only")]
        self._aspirational_items = [item for item in self._items if item.get("Aspirational Only")]
        self._transferable_items = [item for item in self._items if item.get("Shared Only")]
        self._blacklisted_items = sorted(
            blacklisted_items or [], key=lambda value: item_name(value).lower()
        )
        self.selected_names = set(selected)
        self.locked_name = str(locked_name or "")
        self.icons = icons
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter candidates...")
        self.list = QListWidget()
        self.list.setIconSize(QSize(32, 32))
        self.list.setSpacing(1)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.setToolTip(
            "Double-click an item to lock or unlock the slot. Right-click to blacklist it."
        )
        layout.addWidget(self.search)
        lock_row = QHBoxLayout()
        self.lock_label = QLabel("Lock this slot")
        self.lock_label.setObjectName("candidateLockLabel")
        lock_row.addWidget(self.lock_label)
        self.lock_combo = QComboBox()
        self.lock_combo.setObjectName("candidateLockCombo")
        self.lock_combo.addItem("No lock", "")
        for item in self._items:
            self.lock_combo.addItem(item_name(item), item_name(item))
        lock_index = self.lock_combo.findData(self.locked_name)
        self.lock_combo.setCurrentIndex(max(0, lock_index))
        self.lock_combo.setToolTip(
            "The selected item is forced in this slot when the optimizer runs."
        )
        self.lock_combo.currentIndexChanged.connect(self._refresh_lock_style)
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
        self.list.itemDoubleClicked.connect(self._toggle_row_lock)
        self.list.customContextMenuRequested.connect(self._show_item_menu)
        self._refresh_lock_style()
        self._populate()

    def _refresh_lock_style(self):
        locked = bool(self.lock_combo.currentData())
        self.lock_combo.setProperty("locked", locked)
        self.lock_label.setProperty("locked", locked)
        self.lock_label.setText("LOCKED" if locked else "Lock this slot")
        for widget in (self.lock_combo, self.lock_label):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    @staticmethod
    def _is_candidate_row(row: QListWidgetItem) -> bool:
        return bool(row.flags() & Qt.ItemFlag.ItemIsUserCheckable)

    def _remember_visible(self):
        for index in range(self.list.count()):
            row = self.list.item(index)
            if not self._is_candidate_row(row):
                continue
            name = str(row.data(Qt.ItemDataRole.UserRole) or "")
            if row.checkState() == Qt.CheckState.Checked:
                self.selected_names.add(name)
            else:
                self.selected_names.discard(name)

    def _append_items(self, items: list[dict], query: str):
        for item in items:
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
            row.setSizeHint(QSize(0, 40))
            self.list.addItem(row)

    def _append_blacklisted_items(self, items: list[dict]):
        for item in items:
            name = item_name(item)
            row = QListWidgetItem(name)
            row.setIcon(self.icons.icon(item))
            row.setData(Qt.ItemDataRole.UserRole, name)
            row.setToolTip(f"BLACKLISTED — excluded from optimizer.\n{item_tooltip(item)}")
            row.setFlags(Qt.ItemFlag.NoItemFlags)
            row.setForeground(QColor("#817d8a"))
            row.setSizeHint(QSize(0, 40))
            self.list.addItem(row)

    def _append_section_header(self, title: str, color: str = "#e6c983"):
        header = QListWidgetItem(title)
        header.setFlags(Qt.ItemFlag.NoItemFlags)
        header.setForeground(QColor(color))
        font = header.font()
        font.setBold(True)
        header.setFont(font)
        header.setSizeHint(QSize(0, 26))
        self.list.addItem(header)

    def _populate(self):
        self._remember_visible()
        query = self.search.text().strip().lower()
        self.list.clear()
        self._append_items(self._player_items, query)
        porter = [
            item for item in self._porter_items
            if not query or query in item_tooltip(item).lower()
        ]
        if porter:
            self._append_section_header("Porter slip gear")
            self._append_items(porter, "")
        transferable = [
            item for item in self._transferable_items
            if not query or query in item_tooltip(item).lower()
        ]
        if transferable:
            self._append_section_header("Transferable gear")
            self._append_items(transferable, "")
        aspirational = [
            item for item in self._aspirational_items
            if not query or query in item_tooltip(item).lower()
        ]
        if aspirational:
            self._append_section_header("Aspirational gear")
            self._append_items(aspirational, "")
        blacklisted = [
            item for item in self._blacklisted_items
            if not query or query in item_tooltip(item).lower()
        ]
        if blacklisted:
            self._append_section_header(
                "Blacklisted — excluded from optimizer", "#817d8a"
            )
            self._append_blacklisted_items(blacklisted)

    def _check_visible(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for index in range(self.list.count()):
            row = self.list.item(index)
            if self._is_candidate_row(row):
                row.setCheckState(state)

    def _toggle_row_lock(self, row: QListWidgetItem):
        if not self._is_candidate_row(row):
            return
        name = str(row.data(Qt.ItemDataRole.UserRole) or "")
        target = "" if self.lock_combo.currentData() == name else name
        index = self.lock_combo.findData(target)
        self.lock_combo.setCurrentIndex(max(0, index))

    def _show_item_menu(self, position):
        row = self.list.itemAt(position)
        if row is None or not self._is_candidate_row(row):
            return
        name = str(row.data(Qt.ItemDataRole.UserRole) or "")
        if not name or name == "Empty":
            return
        menu = QMenu(self)
        blacklist = menu.addAction(f"Add {name} to global blacklist")
        blacklist.triggered.connect(lambda _checked=False: self._blacklist_candidate(name))
        menu.exec(self.list.viewport().mapToGlobal(position))

    def _blacklist_candidate(self, name: str):
        item = next((value for value in self._items if item_name(value) == name), None)
        if item is None:
            return
        self._remember_visible()
        self.selected_names.discard(name)
        if self.lock_combo.currentData() == name:
            self.lock_combo.setCurrentIndex(0)
        lock_index = self.lock_combo.findData(name)
        if lock_index >= 0:
            self.lock_combo.removeItem(lock_index)
        self._items = [value for value in self._items if item_name(value) != name]
        for collection in (
            self._player_items, self._porter_items,
            self._transferable_items, self._aspirational_items,
        ):
            collection[:] = [value for value in collection if item_name(value) != name]
        if not any(item_name(value) == name for value in self._blacklisted_items):
            self._blacklisted_items.append(item)
            self._blacklisted_items.sort(key=lambda value: item_name(value).casefold())
        self.list.clear()
        self._populate()
        self.blacklist_requested.emit(name)

    def _accept_selection(self):
        self._remember_visible()
        self.locked_name = str(self.lock_combo.currentData() or "")
        self.accept()


class GearSetEditor(QWidget):
    changed = pyqtSignal()

    def __init__(self, title: str, owner: "MainWindow", *, game_grid: bool = False):
        super().__init__()
        self.owner = owner
        self.game_grid = game_grid
        self.items = {slot: gear.Empty for slot in SLOTS}
        self.buttons: dict[str, QPushButton] = {}
        self.empty_slot_labels: dict[str, QLabel] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        if game_grid:
            # Match the reference equipment panel directly; the Quick View
            # column supplies the surrounding frame and spacing.
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            self.setObjectName("gameEquipmentEditor")
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            equipment_box = QGroupBox("")
            equipment_box.setObjectName("equipmentPanel")
            equipment_box.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            equipment_box.setFixedWidth(183)
            equipment_layout = QVBoxLayout(equipment_box)
            equipment_layout.setContentsMargins(3, 3, 3, 3)
            equipment_layout.setSpacing(0)
            equipment_grid = QGridLayout()
            equipment_grid.setContentsMargins(0, 0, 0, 0)
            equipment_grid.setSpacing(1)
            equipment_grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            for index, slot in enumerate(SLOTS):
                button = QPushButton()
                button.setObjectName("equip_slot")
                button.setProperty("selected", slot == "main")
                button.setIconSize(QSize(32, 32))
                button.setFixedSize(42, 42)
                button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                slot_label = {
                    "main": "Main", "sub": "Sub", "ranged": "Rng", "ammo": "Ammo",
                    "head": "Head", "neck": "Neck", "ear1": "Ear1", "ear2": "Ear2",
                    "body": "Body", "hands": "Hands", "ring1": "Ring1", "ring2": "Ring2",
                    "back": "Back", "waist": "Waist", "legs": "Legs", "feet": "Feet",
                }[slot]
                button.setText("")
                empty_label = QLabel(slot_label, button)
                empty_label.setObjectName("emptySlotLabel")
                empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                empty_label.setGeometry(button.rect())
                empty_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                button.setToolTip(f"{slot_label}: Empty")
                button.setToolTipDuration(20000)
                button.setAccessibleName(f"{slot_label}: Empty")
                button.clicked.connect(lambda _checked=False, name=slot: self.choose(name))
                row, column = divmod(index, 4)
                equipment_grid.addWidget(button, row, column)
                self.buttons[slot] = button
                self.empty_slot_labels[slot] = empty_label
            equipment_layout.addLayout(equipment_grid)
            layout.addWidget(equipment_box)
            return
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        heading.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(heading)
        weapon_box = QGroupBox("Fixed weapon row")
        weapon_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        weapon_box.setMinimumHeight(86)
        weapon_grid = QGridLayout(weapon_box)
        weapon_grid.setContentsMargins(6, 8, 6, 6)
        weapon_grid.setHorizontalSpacing(3)
        weapon_grid.setVerticalSpacing(1)
        for index, slot in enumerate(WEAPON_SLOTS):
            button = QPushButton("—")
            button.setFixedSize(40, 40)
            button.setIconSize(QSize(34, 34))
            button.setToolTip(f"{slot.upper()}: Empty")
            button.clicked.connect(lambda _checked=False, name=slot: self.choose(name))
            row, column = divmod(index, 4)
            cell = QVBoxLayout()
            cell.setContentsMargins(0, 0, 0, 0)
            cell.setSpacing(1)
            label = QLabel(slot.upper())
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.addWidget(label)
            cell.addWidget(button, alignment=Qt.AlignmentFlag.AlignHCenter)
            weapon_grid.addLayout(cell, row, column)
            weapon_grid.setColumnStretch(column, 1)
            self.buttons[slot] = button
        layout.addWidget(weapon_box)
        armor_box = QGroupBox("Armor and accessories")
        armor_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        armor_box.setMinimumHeight(196)
        armor_grid = QGridLayout(armor_box)
        armor_grid.setContentsMargins(6, 8, 6, 6)
        armor_grid.setHorizontalSpacing(3)
        armor_grid.setVerticalSpacing(2)
        for index, slot in enumerate(ARMOR_SLOTS):
            button = QPushButton("—")
            button.setFixedSize(36, 36)
            button.setIconSize(QSize(32, 32))
            button.setToolTip(f"{slot.upper()}: Empty")
            button.clicked.connect(lambda _checked=False, name=slot: self.choose(name))
            row, column = divmod(index, 4)
            cell = QVBoxLayout()
            cell.setContentsMargins(0, 0, 0, 0)
            cell.setSpacing(1)
            label = QLabel(slot.upper())
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.addWidget(label)
            cell.addWidget(button, alignment=Qt.AlignmentFlag.AlignHCenter)
            armor_grid.addLayout(cell, row, column)
            armor_grid.setColumnStretch(column, 1)
            self.buttons[slot] = button
        layout.addWidget(armor_box)
        # A splitter gives each editor ample height. Keep equipment at the
        # top instead of stretching group boxes into an empty middle region.
        layout.addStretch(1)

    def choose(self, slot: str):
        if self.game_grid:
            for name, button in self.buttons.items():
                button.setProperty("selected", name == slot)
                button.style().unpolish(button)
                button.style().polish(button)
        dialog = GearPicker(
            slot, self.owner.items_for_slot(slot), self.items[slot], self.owner.icons, self
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_item(slot, dialog.selected_item)

    def set_item(self, slot: str, item: dict, *, emit: bool = True):
        self.items[slot] = item
        name = item_name(item)
        self.buttons[slot].setText(name if len(name) <= 24 else name[:21] + "…")
        self.buttons[slot].setIcon(self.owner.icons.icon(item))
        self.buttons[slot].setToolTip(item_tooltip(item))
        self.buttons[slot].setText("" if name != "Empty" else "—")
        self.buttons[slot].setToolTip(f"{slot.upper()}:\n{item_tooltip(item)}")
        self.buttons[slot].setAccessibleName(f"{slot.upper()}: {name}")
        if self.game_grid:
            self.buttons[slot].setText("")
            self.empty_slot_labels[slot].setVisible(name == "Empty")
            self.empty_slot_labels[slot].raise_()
            self.buttons[slot].style().unpolish(self.buttons[slot])
            self.buttons[slot].style().polish(self.buttons[slot])
        if emit:
            self.changed.emit()

    def refresh_icons(self):
        for slot, item in self.items.items():
            self.buttons[slot].setIcon(self.owner.icons.icon(item))

    def set_gearset(self, gearset: dict):
        for slot in SLOTS:
            self.set_item(slot, gearset.get(slot, gear.Empty), emit=False)
        self.changed.emit()


class ProfileGearPreview(QWidget):
    """Compact, read-only 4x4 preview for one generated LAC set."""

    def __init__(self, owner: "MainWindow"):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.owner = owner
        self.items = {slot: gear.Empty for slot in SLOTS}
        self.buttons: dict[str, QPushButton] = {}
        self.empty_slot_labels: dict[str, QLabel] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(1)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        for index, slot in enumerate(SLOTS):
            button = QPushButton()
            button.setObjectName("profileGearSlot")
            button.setProperty("selected", False)
            button.setFixedSize(42, 42)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            button.setIconSize(QSize(32, 32))
            empty_label = QLabel(self._empty_slot_label(slot), button)
            empty_label.setObjectName("emptySlotLabel")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_label.setGeometry(button.rect())
            empty_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            button.clicked.connect(lambda _checked=False, name=slot: self._select(name))
            row, column = divmod(index, 4)
            grid.addWidget(button, row, column)
            self.buttons[slot] = button
            self.empty_slot_labels[slot] = empty_label
        layout.addLayout(grid)
        self.detail = QLabel("Select a slot to inspect its full item details.")
        self.detail.setObjectName("profileGearDetail")
        self.detail.setWordWrap(True)
        self.detail.setFixedHeight(58)
        layout.addWidget(self.detail)

    @staticmethod
    def _slot_label(slot: str) -> str:
        return {
            "main": "MAIN", "sub": "SUB", "ranged": "RANGE", "ammo": "AMMO",
            "head": "HEAD", "neck": "NECK", "ear1": "EAR 1", "ear2": "EAR 2",
            "body": "BODY", "hands": "HANDS", "ring1": "RING 1", "ring2": "RING 2",
            "back": "BACK", "waist": "WAIST", "legs": "LEGS", "feet": "FEET",
        }[slot]

    @staticmethod
    def _empty_slot_label(slot: str) -> str:
        return {
            "main": "MAIN", "sub": "SUB", "ranged": "RNG", "ammo": "AMMO",
            "head": "HEAD", "neck": "NECK", "ear1": "EAR1", "ear2": "EAR2",
            "body": "BODY", "hands": "HANDS", "ring1": "RING1", "ring2": "RING2",
            "back": "BACK", "waist": "WAIST", "legs": "LEGS", "feet": "FEET",
        }[slot]

    def set_gearset(self, armor: dict, overlay: dict | None = None):
        combined = {slot: gear.Empty for slot in SLOTS}
        combined.update(armor or {})
        if overlay:
            overlay_items = overlay.get("gearset") or {}
            for slot in overlay.get("specified_slots") or ():
                if slot in WEAPON_SLOTS and slot in overlay_items:
                    combined[slot] = overlay_items[slot]
        self.items = combined
        for slot, button in self.buttons.items():
            item = combined.get(slot, gear.Empty)
            name = item_name(item)
            button.setText("")
            button.setIcon(self.owner.icons.icon(item))
            self.empty_slot_labels[slot].setVisible(name == "Empty")
            self.empty_slot_labels[slot].raise_()
            button.setToolTip(item_tooltip(item))
            button.setAccessibleName(f"{self._slot_label(slot)}: {name}")
        selected = next(
            (slot for slot in SLOTS if item_name(combined.get(slot, gear.Empty)) != "Empty"),
            "head",
        )
        self._select(selected)

    def _select(self, slot: str):
        for name, button in self.buttons.items():
            button.setProperty("selected", name == slot)
            button.style().unpolish(button)
            button.style().polish(button)
        item = self.items.get(slot, gear.Empty)
        stats = item_detail_stats(item, limit=8)
        detail = " · ".join(stats) if stats else "No modeled stats."
        self.detail.setText(
            f"<b>{self._slot_label(slot)} · {escape(item_name(item))}</b><br>"
            f"<span style='color:#d0ccd6'>{escape(detail)}</span>"
        )


class ResponsiveStatSection(QGroupBox):
    """Reflow every totals section at the same stable UI breakpoints."""

    TWO_COLUMN_WIDTH = 880
    FOUR_COLUMN_WIDTH = 1440
    MAX_COLUMNS = 4

    def __init__(self, title: str, rows: list[QWidget]):
        super().__init__(title)
        self.setObjectName("quickStatsSection")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._rows = rows
        self._columns = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(4, 8, 4, 4)
        self._grid.setHorizontalSpacing(3)
        self._grid.setVerticalSpacing(2)
        self._reflow(self.width())

    @classmethod
    def columns_for_width(cls, width: int, row_count: int) -> int:
        """Use only 1, 2, or 4 columns so adjacent sections resize together."""
        if width >= cls.FOUR_COLUMN_WIDTH:
            requested = 4
        elif width >= cls.TWO_COLUMN_WIDTH:
            requested = 2
        else:
            requested = 1
        return max(1, min(cls.MAX_COLUMNS, max(1, row_count), requested))

    def _reflow(self, width: int):
        columns = self.columns_for_width(width, len(self._rows))
        if columns == self._columns:
            return
        self._columns = columns
        # Explicitly clear placements before re-adding rows.  Qt normally
        # reparents an item when it is added again, but clearing first avoids
        # stale grid cells during rapid splitter resizes.
        while self._grid.count():
            self._grid.takeAt(0)
        for index, row in enumerate(self._rows):
            self._grid.addWidget(row, index // columns, index % columns)
        for column in range(self.MAX_COLUMNS):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow(event.size().width())


class ResponsiveTotalsHeader(QWidget):
    """Keep the Live Totals explanation and action from squeezing each other."""

    STACK_WIDTH = 820

    def __init__(self, legend: QWidget, action: QWidget, parent=None):
        super().__init__(parent)
        self._legend = legend
        self._action = action
        self._stacked = None
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(4)
        self._reflow(self.width())

    def _reflow(self, width: int):
        stacked = width < self.STACK_WIDTH
        if stacked == self._stacked:
            return
        self._stacked = stacked
        if stacked:
            self._grid.addWidget(self._legend, 0, 0, 1, 2)
            self._grid.addWidget(
                self._action, 1, 1, alignment=Qt.AlignmentFlag.AlignRight
            )
        else:
            self._grid.addWidget(self._legend, 0, 0)
            self._grid.addWidget(
                self._action, 0, 1, alignment=Qt.AlignmentFlag.AlignRight
            )
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow(event.size().width())


class ResponsiveControlStrip(QWidget):
    """Keep two compact control groups on one row, stacking when space is tight."""

    def __init__(self, primary: QWidget, secondary: QWidget, *, stack_width: int = 760,
                 parent=None):
        super().__init__(parent)
        self._primary = primary
        self._secondary = secondary
        self._stack_width = stack_width
        self._stacked = None
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(4)
        self._reflow(self.width())

    def _reflow(self, width: int):
        stacked = width < self._stack_width
        if stacked == self._stacked:
            return
        self._stacked = stacked
        if stacked:
            self._grid.addWidget(self._primary, 0, 0)
            self._grid.addWidget(self._secondary, 1, 0)
            self._grid.setColumnStretch(0, 1)
            self._grid.setColumnStretch(1, 0)
        else:
            self._grid.addWidget(self._primary, 0, 0)
            self._grid.addWidget(self._secondary, 0, 1)
            self._grid.setColumnStretch(0, 0)
            self._grid.setColumnStretch(1, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow(event.size().width())


class ResponsiveBuffGrid(QWidget):
    """Keep Active Buff cards readable instead of squeezing their form fields."""

    TWO_COLUMN_WIDTH = 840

    def __init__(self, panels: list[QWidget], parent=None):
        super().__init__(parent)
        self._panels = panels
        self._columns = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(2, 2, 2, 2)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(8)
        self._reflow(self.width())

    @classmethod
    def columns_for_width(cls, width: int) -> int:
        return 2 if width >= cls.TWO_COLUMN_WIDTH else 1

    def _reflow(self, width: int):
        columns = self.columns_for_width(width)
        if columns == self._columns:
            return
        self._columns = columns
        for index, panel in enumerate(self._panels):
            self._grid.addWidget(panel, index // columns, index % columns)
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 1 if columns == 2 else 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow(event.size().width())


class LuaSyntaxHighlighter(QSyntaxHighlighter):
    """Small VS Code-inspired Lua highlighter for LuAshitacast profiles."""

    KEYWORDS = (
        "and", "break", "do", "else", "elseif", "end", "false", "for",
        "function", "goto", "if", "in", "local", "nil", "not", "or",
        "repeat", "return", "then", "true", "until", "while",
    )

    def __init__(self, document):
        super().__init__(document)
        self._formats = {
            "keyword": self._format("#c586c0", bold=True),
            "builtin": self._format("#4ec9b0"),
            "function": self._format("#dcdcaa"),
            "string": self._format("#ce9178"),
            "number": self._format("#b5cea8"),
            "comment": self._format("#6a9955", italic=True),
            "field": self._format("#9cdcfe"),
        }
        keyword_pattern = r"\b(?:" + "|".join(self.KEYWORDS) + r")\b"
        self._rules = (
            (re.compile(keyword_pattern), "keyword"),
            (re.compile(r"\b(?:ashita|gData|gFunc|sets|profile|settings)\b"), "builtin"),
            (re.compile(r"\b(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?)\b"), "number"),
            (re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\""), "string"),
            (re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?=\s*\()"), "function"),
            (re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?=\s*=)"), "field"),
        )

    @staticmethod
    def _format(color: str, *, bold: bool = False, italic: bool = False) -> QTextCharFormat:
        value = QTextCharFormat()
        value.setForeground(QColor(color))
        value.setFontWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        value.setFontItalic(italic)
        return value

    def highlightBlock(self, text: str):
        for pattern, format_name in self._rules:
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), self._formats[format_name])

        # Lua line comments and long comments override tokens inside them.
        comment_kind, comment_start = self._comment_start(text)
        if self.previousBlockState() == 1:
            start = 0
        elif comment_kind == "long":
            start = comment_start
        else:
            start = -1
        if start >= 0:
            end = text.find("]]", start + (0 if self.previousBlockState() == 1 else 4))
            if end < 0:
                self.setCurrentBlockState(1)
                self.setFormat(start, len(text) - start, self._formats["comment"])
                return
            self.setCurrentBlockState(0)
            self.setFormat(start, end + 2 - start, self._formats["comment"])
        else:
            self.setCurrentBlockState(0)
        if comment_kind == "line" and self.previousBlockState() != 1:
            self.setFormat(comment_start, len(text) - comment_start, self._formats["comment"])

    @staticmethod
    def _comment_start(text: str) -> tuple[str, int]:
        """Find the first Lua comment delimiter that is outside a string."""
        quote = ""
        escaped = False
        index = 0
        while index < len(text) - 1:
            character = text[index]
            if quote:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = ""
                index += 1
                continue
            if character in {"'", '"'}:
                quote = character
                index += 1
                continue
            if text[index:index + 2] == "--":
                return ("long" if text[index:index + 4] == "--[[" else "line", index)
            index += 1
        return "", -1


class LineNumberArea(QWidget):
    def __init__(self, editor: "LuaCodeEditor"):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_line_number_area(event)


class LuaCodeEditor(QPlainTextEdit):
    """Lua editor with line numbers and a restrained VS Code-like surface."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("lacCodeEditor")
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(QFontMetrics(font).horizontalAdvance(" ") * 4)
        self.line_numbers = LineNumberArea(self)
        self.highlighter = LuaSyntaxHighlighter(self.document())
        self.blockCountChanged.connect(self._update_line_number_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_line_number_width()
        self._highlight_current_line()

    def line_number_area_width(self) -> int:
        digits = len(str(max(1, self.blockCount())))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_line_number_width(self, *_args):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_number_area(self, rect: QRect, dy: int):
        if dy:
            self.line_numbers.scroll(0, dy)
        else:
            self.line_numbers.update(0, rect.y(), self.line_numbers.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        rect = self.contentsRect()
        self.line_numbers.setGeometry(
            QRect(rect.left(), rect.top(), self.line_number_area_width(), rect.height())
        )

    def _highlight_current_line(self):
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor("#252526"))
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])

    def paint_line_number_area(self, event):
        painter = QPainter(self.line_numbers)
        painter.fillRect(event.rect(), QColor("#181818"))
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                color = "#c6c6c6" if number == self.textCursor().blockNumber() else "#858585"
                painter.setPen(QColor(color))
                painter.drawText(
                    0, top, self.line_numbers.width() - 5, self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight, str(number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            number += 1


class ResponsiveCatalogSplitter(QSplitter):
    """Stack catalog/detail panes when a side-by-side layout would overlap."""

    NARROW_WIDTH = 820

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setChildrenCollapsible(False)

    def resizeEvent(self, event):
        desired = (
            Qt.Orientation.Vertical
            if event.size().width() < self.NARROW_WIDTH
            else Qt.Orientation.Horizontal
        )
        changed = desired != self.orientation()
        if changed:
            self.setOrientation(desired)
        super().resizeEvent(event)
        if changed:
            extent = event.size().height() if desired == Qt.Orientation.Vertical else event.size().width()
            self.setSizes([max(190, int(extent * 0.43)), max(240, int(extent * 0.57))])


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


def _run_overnight_cache_task(task: dict, cache: SimulationCache) -> str:
    """Evaluate one deterministic cache-warming task.

    The task contains only plain dictionaries so it can safely run outside the
    GUI thread.  Cache access is opened and closed per operation by
    ``SimulationCache``; this avoids keeping a SQLite connection tied to Qt's
    worker thread.
    """
    cache_kind = task.get("kind", "quick-look")
    cache_key = cache.key_for(cache_kind, task["request"])
    if cache.get(cache_key, cache_kind) is not None:
        return "cached"

    started_at = time.monotonic()
    context = task["context"]
    player = create_player.create_player(
        context["main_job"], context["sub_job"], context["master_level"],
        gearset=task["gearset"], buffs=context["buffs"], abilities=context["abilities"],
    )
    enemy = create_player.create_enemy(task["enemy"])
    output, text = _evaluate_quick_result(
        player, enemy, task["action"], tp=task.get("tp", 0),
        ws_name=task.get("ws_name", ""), ws_type=task.get("ws_type", ""),
        spell_name=task.get("spell_name", ""),
        spell_type=task.get("spell_type", ""),
    )

    if cache.put(
        cache_key, cache_kind, {"text": text, "output": output},
        time.monotonic() - started_at,
    ):
        return "stored"
    return "failed"


class OvernightSimulationThread(QThread):
    """Warm deterministic simulation results without blocking the GUI."""

    progress = pyqtSignal(object)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    stopped = pyqtSignal(object)

    def __init__(self, tasks: list[dict], cache: SimulationCache, hours: float, parent=None):
        super().__init__(parent)
        self.tasks = tasks
        self.cache = cache
        self.hours = max(0.1, float(hours))
        self._stop_requested = threading.Event()

    def request_stop(self):
        self._stop_requested.set()

    def run(self):
        started_at = time.monotonic()
        deadline = started_at + self.hours * 3600
        summary = {
            "planned": len(self.tasks), "processed": 0, "stored": 0,
            "cached": 0, "failed": 0, "errors": [],
            "elapsed": 0.0, "expired": False,
        }
        try:
            for index, task in enumerate(self.tasks, 1):
                if self._stop_requested.is_set():
                    summary["elapsed"] = time.monotonic() - started_at
                    self.stopped.emit(summary)
                    return
                if time.monotonic() >= deadline:
                    summary["expired"] = True
                    break
                try:
                    result = _run_overnight_cache_task(task, self.cache)
                    summary[result] = summary.get(result, 0) + 1
                except Exception as error:
                    summary["failed"] += 1
                    if len(summary["errors"]) < 8:
                        summary["errors"].append(str(error))
                summary["processed"] = index
                if index == 1 or index % 10 == 0 or index == len(self.tasks):
                    summary["elapsed"] = time.monotonic() - started_at
                    self.progress.emit(dict(summary))
            summary["elapsed"] = time.monotonic() - started_at
            self.succeeded.emit(summary)
        except Exception as error:
            self.failed.emit(str(error))


class PlotThread(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)
    stopped = pyqtSignal(str)

    def __init__(self, player, enemy, ws_name: str, tp_value: int, ws_type: str,
                 samples=20000, seed=None, reference_enemies=None, parent=None):
        super().__init__(parent)
        self.player = player
        self.enemy = enemy
        self.ws_name = ws_name
        self.tp_value = tp_value
        self.ws_type = ws_type
        self.samples = samples
        self.seed = seed
        self.reference_enemies = list(reference_enemies or ())
        self._stop_requested = threading.Event()

    def request_stop(self):
        self._stop_requested.set()

    def run(self):
        try:
            if self._stop_requested.is_set():
                return
            distribution = actions.simulate_ws_distribution(
                self.player, self.enemy, self.ws_name, self.tp_value, self.ws_type,
                seed=self.seed, samples=self.samples, stop_event=self._stop_requested,
            )
            if self.reference_enemies:
                reference_distributions = {}
                for offset, (name, enemy) in enumerate(self.reference_enemies, start=1):
                    if self._stop_requested.is_set():
                        raise actions.SimulationStopped("Weapon-skill distribution stopped by user.")
                    reference_distributions[str(name)] = actions.simulate_ws_distribution(
                        self.player, enemy, self.ws_name, self.tp_value, self.ws_type,
                        seed=(int(self.seed or 0) + offset) & 0xFFFFFFFF,
                        samples=self.samples, stop_event=self._stop_requested,
                    )
                distribution["reference_distributions"] = reference_distributions
            self.completed.emit(distribution)
        except actions.SimulationStopped as error:
            self.stopped.emit(str(error))
        except Exception as error:
            self.failed.emit(str(error))


class SimulationThread(QThread):
    """Run the fixed two-hour cycle away from the GUI thread."""

    completed = pyqtSignal(object)
    failed = pyqtSignal(str)
    stopped = pyqtSignal(str)

    def __init__(self, player_tp, player_ws, enemy, threshold, ws_name, ws_type, seed,
                 reference_enemies=None, parent=None):
        super().__init__(parent)
        self.player_tp = player_tp
        self.player_ws = player_ws
        self.enemy = enemy
        self.threshold = threshold
        self.ws_name = ws_name
        self.ws_type = ws_type
        self.seed = seed
        self.reference_enemies = list(reference_enemies or ())
        self._stop_requested = threading.Event()

    def request_stop(self):
        self._stop_requested.set()

    def run(self):
        try:
            if self._stop_requested.is_set():
                return
            summary = actions.run_simulation_structured(
                self.player_tp, self.player_ws, self.enemy, self.threshold,
                self.ws_name, self.ws_type, seed=self.seed, stop_event=self._stop_requested,
            )
            if self.reference_enemies:
                reference_summaries = {}
                for offset, (name, enemy) in enumerate(self.reference_enemies, start=1):
                    if self._stop_requested.is_set():
                        raise actions.SimulationStopped("Two-hour simulation stopped by user.")
                    reference_summaries[str(name)] = actions.run_simulation_structured(
                        self.player_tp, self.player_ws, enemy, self.threshold,
                        self.ws_name, self.ws_type,
                        seed=(int(self.seed or 0) + offset) & 0xFFFFFFFF,
                        stop_event=self._stop_requested,
                    )
                summary["reference_summaries"] = reference_summaries
            self.completed.emit(summary)
        except actions.SimulationStopped as error:
            self.stopped.emit(str(error))
        except Exception as error:
            self.failed.emit(str(error))


def _result_age_text(created_at: float) -> str:
    age = max(0, int(time.time() - float(created_at)))
    if age < 60:
        return f"{age}s ago"
    if age < 3600:
        return f"{age // 60}m ago"
    if age < 86400:
        return f"{age // 3600}h ago"
    return f"{age // 86400}d ago"


class ResultComparisonDialog(QDialog):
    """Embedded plot and gear/stat comparison for one to six saved results."""

    def __init__(self, records: list[dict], icons: GearIconProvider, parent=None):
        super().__init__(parent)
        self.records = list(records[:6])
        self.icons = icons
        self.setWindowTitle(
            "Simulation result graphs" if len(self.records) == 1
            else "Simulation result comparison"
        )
        self.resize(1180, 820)
        layout = QVBoxLayout(self)
        if len(self.records) > 1:
            scenario_lines = self._scenario_differences()
            if scenario_lines:
                warning = QLabel("Scenario differences:\n" + "\n".join(scenario_lines))
                warning.setWordWrap(True)
                warning.setStyleSheet(
                    "color: #ffe2a8; background: #352819; "
                    "border: 1px solid #8a6430; padding: 6px;"
                )
                layout.addWidget(warning)
            baseline_controls = QHBoxLayout()
            baseline_controls.addWidget(QLabel("Baseline for deltas"))
            self.baseline_combo = QComboBox()
            self.baseline_combo.addItems([
                str(record.get("title") or f"Result {record.get('id')}") for record in self.records
            ])
            self.baseline_combo.setToolTip("The first selected result is the default baseline. Choose another to rebuild the dashboard.")
            self.baseline_combo.currentIndexChanged.connect(self._change_baseline)
            baseline_controls.addWidget(self.baseline_combo, 1)
            layout.addLayout(baseline_controls)
        if len(self.records) > 1:
            summary = QTableWidget(len(self.records), 8)
            summary.setHorizontalHeaderLabels([
                "Result", "Kind", "Total DPS", "TP DPS", "WS DPS",
                "Time to WS", "Round time", "TP / WS",
            ])
            summary.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            summary.setAlternatingRowColors(True)
            summary.verticalHeader().setVisible(False)
            summary.verticalHeader().setDefaultSectionSize(28)
            summary.setWordWrap(False)
            for row, record in enumerate(self.records):
                payload = record.get("payload") or {}
                metrics = payload.get("metrics") or {}
                values = (
                    record.get("title") or f"Result {record.get('id')}", record.get("kind", ""),
                    metrics.get("total_dps", metrics.get("dps", "")),
                    metrics.get("tp_dps", ""), metrics.get("ws_dps", ""),
                    metrics.get("expected_time_to_ws", metrics.get("time_to_ws", "")),
                    metrics.get("time_per_attack_round", ""), metrics.get("average_ws_tp", ""),
                )
                for column, value in enumerate(values):
                    summary.setItem(row, column, QTableWidgetItem(self._format_value(value)))
            summary.resizeColumnsToContents()
            layout.addWidget(summary)
            gear_diff = self._gear_difference_table()
            if gear_diff is not None:
                layout.addWidget(gear_diff)
            stat_diff = self._stat_difference_table()
            if stat_diff is not None:
                layout.addWidget(stat_diff)
        else:
            record = self.records[0] if self.records else {}
            payload = record.get("payload") or {}
            scenario = payload.get("scenario") or {}
            context = "  ·  ".join(filter(None, (
                str(scenario.get("enemy") or ""),
                str(scenario.get("ws") or ""),
            )))
            if context:
                label = QLabel(context)
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                label.setStyleSheet("color: #d0ccd6; padding: 2px;")
                layout.addWidget(label)
        if FigureCanvas is not None:
            self.figure = self._build_figure()
            layout.addWidget(NavigationToolbar(self.figure, self))
            layout.addWidget(self.figure, 1)
        else:
            layout.addWidget(QLabel("Matplotlib Qt embedding is unavailable in this Python environment."), 1)
        controls = QHBoxLayout()
        export_png = QPushButton("Export PNG")
        export_csv = QPushButton("Export CSV")
        export_png.clicked.connect(self._export_png)
        export_csv.clicked.connect(self._export_csv)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        controls.addWidget(export_png)
        controls.addWidget(export_csv)
        controls.addStretch(1)
        controls.addWidget(close)
        layout.addLayout(controls)

    def _change_baseline(self, index: int):
        if index <= 0 or index >= len(self.records):
            return
        records = list(self.records)
        records.insert(0, records.pop(index))
        # Rebuild once so gear deltas, summary ordering, and plot colors all
        # use the same baseline. Keep the replacement referenced by this
        # dialog so Qt does not collect it immediately.
        self._replacement_dialog = ResultComparisonDialog(records, self.icons, self.parentWidget())
        self._replacement_dialog.show()
        self.close()

    @staticmethod
    def _format_value(value) -> str:
        try:
            return f"{float(value):,.2f}"
        except (TypeError, ValueError):
            return str(value or "")

    def _scenario_differences(self) -> list[str]:
        contexts = []
        for record in self.records:
            payload = record.get("payload") or {}
            contexts.append(payload.get("scenario") or {})
        if not contexts:
            return []
        differences = []
        for key in sorted({key for context in contexts for key in context}):
            values = list(dict.fromkeys(str(context.get(key, "")) for context in contexts))
            if len(values) > 1:
                differences.append(f"{key}: {' | '.join(values)}")
        return differences

    @staticmethod
    def _comparison_gearset(record: dict) -> dict:
        gearsets = (record.get("payload") or {}).get("gearsets") or {}
        for name in ("single", "tp", "ws"):
            if isinstance(gearsets.get(name), dict):
                return gearsets[name]
        for gearset in gearsets.values():
            if isinstance(gearset, dict):
                return gearset
        return {}

    @staticmethod
    def _single_result_gearset(record: dict) -> dict:
        """Prefer WS equipment for the original single-result chart layout."""
        gearsets = (record.get("payload") or {}).get("gearsets") or {}
        for name in ("ws", "single", "tp"):
            if isinstance(gearsets.get(name), dict):
                return gearsets[name]
        return ResultComparisonDialog._comparison_gearset(record)

    def _gear_difference_table(self):
        if len(self.records) < 2:
            return None
        baseline = self._comparison_gearset(self.records[0])
        if not baseline:
            return None
        table = QTableWidget(len(SLOTS), len(self.records) + 1)
        table.setHorizontalHeaderLabels(
            ["Slot", "Baseline"] + [str(record.get("title", "Result"))[:24] for record in self.records[1:]]
        )
        table.setMaximumHeight(210)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(28)
        table.setWordWrap(False)
        for row, slot in enumerate(SLOTS):
            table.setItem(row, 0, QTableWidgetItem(slot.upper()))
            base_item = baseline.get(slot) or {}
            table.setItem(row, 1, QTableWidgetItem(item_name(base_item)))
            for column, record in enumerate(self.records[1:], start=2):
                item = self._comparison_gearset(record).get(slot) or {}
                cell = QTableWidgetItem(item_name(item))
                if item_name(item) != item_name(base_item):
                    cell.setForeground(QColor("#ffc4c1"))
                table.setItem(row, column, cell)
        table.resizeColumnsToContents()
        table.setToolTip("Slot-by-slot gear differences. The first selected result is the baseline.")
        return table

    def _stat_difference_table(self):
        if len(self.records) < 2:
            return None
        keys = ("Attack", "Accuracy", "Store TP", "Double Attack", "Triple Attack", "Fast Cast", "PDT", "MDT", "DT", "HP", "Evasion", "Magic Evasion")

        def totals(record):
            result = {key: 0.0 for key in keys}
            for item in self._comparison_gearset(record).values():
                for key in keys:
                    value = item.get(key, 0)
                    try:
                        result[key] += float(value)
                    except (TypeError, ValueError):
                        continue
            return result

        baseline = totals(self.records[0])
        table = QTableWidget(len(keys), len(self.records) + 1)
        table.setHorizontalHeaderLabels(
            ["Stat delta", "Baseline"] + [str(record.get("title", "Result"))[:24] for record in self.records[1:]]
        )
        table.setMaximumHeight(260)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(28)
        table.setWordWrap(False)
        for row, key in enumerate(keys):
            table.setItem(row, 0, QTableWidgetItem(key))
            table.setItem(row, 1, QTableWidgetItem(f"{baseline[key]:+.1f}"))
            for column, record in enumerate(self.records[1:], start=2):
                delta = totals(record)[key] - baseline[key]
                table.setItem(row, column, QTableWidgetItem(f"{delta:+.1f}"))
        table.resizeColumnsToContents()
        table.setToolTip("Aggregate item-stat deltas; modeled player traits and buffs are not included in this compact view.")
        return table

    def _build_figure(self):
        if len(self.records) == 1:
            return FigureCanvas(self._build_original_style_figure(self.records[0]))
        return FigureCanvas(self._build_comparison_figure())

    def _build_comparison_figure(self):
        import matplotlib.pyplot as plt
        figure, axes = plt.subplots(2, 2, figsize=(11, 7), constrained_layout=True)
        colors = ("#1769aa", "#c2185b", "#00897b", "#d35400", "#8e44ad", "#5d4037")
        for index, record in enumerate(self.records):
            payload = record.get("payload") or {}
            label = str(record.get("title") or f"Result {record.get('id')}")
            color = colors[index % len(colors)]
            metrics = payload.get("metrics") or {}
            axes[0, 0].bar(index, float(metrics.get("total_dps", 0) or 0), color=color, label=label)
            series = payload.get("dps_series") or {}
            axes[0, 1].plot(series.get("time", []), series.get("total", []), color=color, label=label)
            distribution = payload.get("distribution") or {}
            histogram = distribution.get("histogram") or {}
            edges = histogram.get("edges") or []
            counts = histogram.get("counts") or []
            if len(edges) == len(counts) + 1:
                centers = [(edges[i] + edges[i + 1]) / 2 for i in range(len(counts))]
                axes[1, 0].plot(centers, counts, color=color, label=label)
            axes[1, 1].bar(index - 0.3, float(metrics.get("average_ws_damage", 0) or 0),
                            width=0.6, color=color)
        axes[0, 0].set_title("Total DPS")
        axes[0, 1].set_title("DPS convergence")
        axes[1, 0].set_title("WS damage distribution")
        axes[1, 1].set_title("Average WS damage")
        axes[0, 0].set_xticks(range(len(self.records)), [str(record.get("title", ""))[:14] for record in self.records], rotation=25, ha="right")
        axes[1, 1].set_xticks(range(len(self.records)), [str(record.get("title", ""))[:14] for record in self.records], rotation=25, ha="right")
        for axis in axes.flat:
            axis.grid(alpha=0.2)
            if len(self.records) > 1:
                axis.legend(fontsize=8)
        return figure

    @staticmethod
    def _number(value, default=0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    def _build_original_style_figure(self, record: dict):
        """Embed the legacy focused graphs instead of a generic dashboard."""
        import matplotlib.pyplot as plt

        payload = record.get("payload") or {}
        if payload.get("distribution"):
            return self._build_legacy_distribution_figure(record, plt)
        return self._build_legacy_cycle_figure(record, plt)

    def _build_legacy_cycle_figure(self, record: dict, plt):
        payload = record.get("payload") or {}
        metrics = payload.get("metrics") or {}
        scenario = payload.get("scenario") or {}
        series = payload.get("dps_series") or {}
        figure = plt.figure(figsize=(10, 5))
        axis = figure.add_axes((0.105, 0.13, 0.86, 0.74))
        time_values = series.get("time") or []
        total_values = series.get("total") or []
        if time_values and total_values:
            total_dps = self._number(metrics.get("total_dps"))
            axis.plot(time_values, total_values, label=f"Total={total_dps:7.1f}")
            reference_colors = ("#72c7e8", "#d99bea", "#9de2a8")
            for index, (name, reference) in enumerate(
                (payload.get("reference_summaries") or {}).items()
            ):
                reference_series = reference.get("dps_series") or {}
                reference_times = reference_series.get("time") or []
                reference_values = reference_series.get("total") or []
                if reference_times and reference_values:
                    reference_metrics = reference.get("total_dps", 0)
                    axis.plot(
                        reference_times, reference_values,
                        color=reference_colors[index % len(reference_colors)],
                        label=f"{name}={self._number(reference_metrics):7.1f}",
                    )
            total_damage = self._number(metrics.get("total_damage"))
            for time_key, value_key, metric_key, prefix in (
                ("tp_time", "tp", "tp_dps", "TP"),
                ("ws_time", "ws", "ws_dps", "WS"),
            ):
                values = series.get(value_key) or []
                times = series.get(time_key) or []
                if not times or not values:
                    continue
                damage_key = "tp_damage" if prefix == "TP" else "ws_damage"
                contribution = 100.0 * self._number(metrics.get(damage_key)) / total_damage if total_damage else 0.0
                axis.plot(times, values, label=f"{prefix}={self._number(metrics.get(metric_key)):7.1f} ({contribution:5.1f}%)")
            axis.set_xlabel("Time (s)")
            axis.set_ylabel("DPS")
            axis.legend()
        else:
            axis.text(0.5, 0.5, "This saved result has no DPS series.", ha="center", va="center", transform=axis.transAxes)
            axis.set_axis_off()
        title = str(record.get("title") or "TP → WS simulation")
        details = " · ".join(filter(None, (
            str(scenario.get("job") or ""), str(scenario.get("enemy") or ""),
            f"{scenario.get('tp')} TP" if scenario.get("tp") else "",
        )))
        figure.suptitle(f"{title}\n{details}" if details else title, x=0.105, ha="left", fontsize=11)
        return figure

    def _build_legacy_distribution_figure(self, record: dict, plt):
        payload = record.get("payload") or {}
        distribution = payload.get("distribution") or {}
        histogram = distribution.get("histogram") or {}
        edges = np.asarray(histogram.get("edges") or [], dtype=float)
        counts = np.asarray(histogram.get("counts") or [], dtype=float)
        scenario = payload.get("scenario") or {}
        snapshot = (payload.get("plot") or {}).get("ws_player") or {}
        stats = snapshot.get("stats") or {}
        gearset = self._single_result_gearset(record)
        figure = plt.figure(figsize=(10, 5))
        axis = figure.add_axes((0.275, 0.10, 0.70, 0.75))

        if len(edges) == len(counts) + 1 and len(counts) and np.isfinite(counts).all():
            widths = np.diff(edges)
            count_total = float(np.sum(counts))
            if count_total and np.all(widths > 0):
                density = counts / (count_total * widths)
                axis.stairs(density, edges, fill=True, color="grey", alpha=0.25)
                axis.stairs(density, edges, color="black", alpha=1.0)
                average = self._number(distribution.get("mean"))
                axis.axvline(average, color="black", linestyle="--", label=f"Average = {int(average)} damage.")
                reference_colors = ("#1769aa", "#c2185b", "#00897b")
                for index, (name, reference) in enumerate(
                    (distribution.get("reference_distributions") or {}).items()
                ):
                    reference_mean = self._number(reference.get("mean"))
                    axis.axvline(
                        reference_mean,
                        color=reference_colors[index % len(reference_colors)],
                        linestyle=":",
                        label=f"{name} mean = {int(reference_mean)}",
                    )
                axis.legend(loc="best")
        else:
            axis.text(0.5, 0.5, "This saved result has no WS histogram.", ha="center", va="center", transform=axis.transAxes)
        axis.set_xlabel("Damage")
        axis.tick_params(axis="y", which="both", left=False, labelleft=False)

        self._add_legacy_gear_icons(figure, gearset)
        annotation = self._legacy_stat_annotation(snapshot, scenario, distribution, payload.get("seed"))
        figure.text(0.012, 0.17, annotation, family="monospace", fontsize=8,
                    bbox={"boxstyle": "round", "fc": "1.0"})
        base_tp = self._number(scenario.get("tp"), 1000.0)
        bonus = self._number(stats.get("TP Bonus")) if stats else 0.0
        effective_tp = max(1000.0, min(3000.0, base_tp + bonus))
        job = str(snapshot.get("main_job") or scenario.get("job") or "").upper()
        subjob = str(snapshot.get("sub_job") or "").upper()
        job_label = f"ML{int(self._number(snapshot.get('master_level')))} {job}/{subjob}" if snapshot else job
        tp_label = f"TP={base_tp:.0f} + {bonus:.0f} bonus = {effective_tp:.0f} effective"
        ws_name = str(scenario.get("ws") or record.get("title") or "Weapon skill")
        axis.set_title(
            f"{job_label}\n{tp_label:>35s} {'Minimum':>8s} {'Mean':>8s} {'Median':>8s} {'Maximum':>8s}\n"
            f"{ws_name:>15s} {self._number(distribution.get('minimum')):>8.0f} "
            f"{self._number(distribution.get('mean')):>8.0f} {self._number(distribution.get('median')):>8.0f} "
            f"{self._number(distribution.get('maximum')):>8.0f}",
            loc="left",
        )
        return figure

    def _add_legacy_gear_icons(self, figure, gearset: dict):
        """Use the same icon strip as the original WSDist distribution graph."""
        sources = tuple(str(path) for path in self.icons.plot_icon_sources())
        for index, slot in enumerate(fancy_plot.PLOT_SLOTS):
            row, column = divmod(index, 4)
            icon_axis = figure.add_axes((0.018 + column * 0.061, 0.77 - row * 0.155, 0.054, 0.105))
            icon_axis.set_xticks([])
            icon_axis.set_yticks([])
            icon_axis.set_title(slot.upper(), fontsize=5, pad=1)
            item = gearset.get(slot) or {}
            item_id = self.icons.item_id(item)
            if not item_id:
                continue
            try:
                image = fancy_plot._read_icon(sources, int(item_id))
                if image is not None:
                    icon_axis.imshow(image)
            except (OSError, ValueError, TypeError):
                # Optional decorations must never prevent a saved graph from opening.
                continue

    def _legacy_stat_annotation(self, snapshot: dict, scenario: dict, distribution: dict, seed) -> str:
        stats = snapshot.get("stats") or {}
        if not stats:
            return "\n".join((
                f"Enemy = {scenario.get('enemy', '')}",
                f"Samples = {distribution.get('samples', '')}",
                "", "Legacy result:", "full player stats", "were not saved.",
            ))
        keys = (
            ("STR", "STR"), ("DEX", "DEX"), ("VIT", "VIT"), ("AGI", "AGI"),
            ("INT", "INT"), ("MND", "MND"), ("CHR", "CHR"),
            ("Accuracy1", "Accuracy1"), ("Accuracy2", "Accuracy2"),
            ("Attack1", "Attack1"), ("Attack2", "Attack2"),
            ("Ranged Accuracy", "Ranged Acc."), ("Ranged Attack", "Ranged Atk."),
        )
        return "\n".join(f"{label + ' = ':>14s}{self._number(stats.get(key)):>4.0f}" for key, label in keys)

    def _export_png(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export comparison PNG", "simulation-comparison.png", "PNG (*.png)")
        if path and FigureCanvas is not None:
            self.figure.figure.savefig(path, dpi=160)

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export comparison CSV", "simulation-comparison.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow([
                "title", "kind", "seed", "job", "enemy", "ws", "tp",
                "total_dps", "tp_dps", "ws_dps", "average_ws_damage", "expected_time_to_ws",
                "time_per_attack_round", "average_ws_tp",
                "set", "slot", "gear", "relevant_stats",
            ])
            for record in self.records:
                payload = record.get("payload") or {}
                metrics = payload.get("metrics") or {}
                scenario = payload.get("scenario") or {}
                gearsets = payload.get("gearsets") or {"": {}}
                for set_name, gearset in gearsets.items():
                    gearset = gearset if isinstance(gearset, dict) else {}
                    rows = ((slot, gearset.get(slot) or {}) for slot in SLOTS) or (("", {}),)
                    for slot, item in rows:
                        writer.writerow([
                            record.get("title", ""), record.get("kind", ""), payload.get("seed", ""),
                            scenario.get("job", ""), scenario.get("enemy", ""), scenario.get("ws", ""), scenario.get("tp", ""),
                            metrics.get("total_dps", ""), metrics.get("tp_dps", ""), metrics.get("ws_dps", ""),
                            metrics.get("average_ws_damage", ""), metrics.get("expected_time_to_ws", metrics.get("time_to_ws", "")),
                            metrics.get("time_per_attack_round", ""), metrics.get("average_ws_tp", ""),
                            set_name, slot, item_name(item), MainWindow._history_relevant_stats(item),
                        ])


class TopSetsDialog(QDialog):
    """Compare completed results and load a selected result into the GUI."""

    def __init__(self, results: list[dict], icons: GearIconProvider, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Best optimizer sets")
        self.resize(1050, 760)
        self.icons = icons
        self._gear_cells: list[tuple[QFrame, str, dict]] = []
        # Retain enough distinct candidates to compare meaningful alternatives
        # without allowing an unbounded result window.
        self.results = list(results[:wsdist.OPTIMIZER_RESULT_LIMIT])
        layout = QVBoxLayout(self)
        note = QLabel(
            "Combined TP + WS results show the TP and WS sets side by side. "
            "Other optimizer modes show one full equipment set."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
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
            comparison = QGroupBox(
                "Tradeoff frontier" if any(result.get("tradeoff") for result in substat_results)
                else "Secondary-stat comparison"
            )
            comparison_layout = QVBoxLayout(comparison)
            comparison_note = QLabel(
                "Values are compared using the same modeled player stats as each set. "
                "Delta is relative to the best-damage set."
            )
            comparison_note.setWordWrap(True)
            comparison_layout.addWidget(comparison_note)
            tradeoff = any(result.get("tradeoff") for result in substat_results)
            table = QTableWidget(
                len(substat_results) if tradeoff else len(targets),
                3 + len(targets) if tradeoff else 3,
            )
            table.setHorizontalHeaderLabels(
                ["Set", "Primary", "Loss %", *targets] if tradeoff else
                ["Secondary stat", "Best damage set", "Sub-stat optimized / delta"]
            )
            table.verticalHeader().setVisible(False)
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            optimized = next(
                (result for result in substat_results
                 if result.get("label") == "Sub-stat optimized"),
                substat_results[-1],
            )
            baseline_values = baseline.get("substats", {})
            if tradeoff:
                for row, result in enumerate(substat_results):
                    table.setItem(row, 0, QTableWidgetItem(str(result.get("label") or f"Tradeoff {row + 1}")))
                    table.setItem(row, 1, QTableWidgetItem(f"{float(result.get('metric') or 0):,.4f}"))
                    table.setItem(row, 2, QTableWidgetItem(f"{float(result.get('primary_loss') or 0):.2f}"))
                    for column, target in enumerate(targets, start=3):
                        value = float(result.get("substats", {}).get(target, 0.0))
                        delta = value - float(baseline_values.get(target, 0.0))
                        table.setItem(row, column, QTableWidgetItem(f"{value:,.1f} ({delta:+,.1f})"))
            else:
                optimized_values = optimized.get("substats", {})
                for row, target in enumerate(targets):
                    base_value = float(baseline_values.get(target, 0.0))
                    optimized_value = float(optimized_values.get(target, 0.0))
                    table.setItem(row, 0, QTableWidgetItem(str(target)))
                    table.setItem(row, 1, QTableWidgetItem(f"{base_value:,.1f}"))
                    table.setItem(row, 2, QTableWidgetItem(f"{optimized_value:,.1f}  ({optimized_value - base_value:+,.1f})"))
            table.resizeColumnsToContents()
            comparison_layout.addWidget(table)
            layout.addWidget(comparison)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        top_metric = max((float(result.get("metric") or 0) for result in self.results), default=0)
        for result_index, result in enumerate(self.results):
            metric = float(result.get("metric") or 0)
            worse = 0.0 if top_metric <= 0 else max(0.0, (top_metric - metric) / abs(top_metric) * 100)
            label = result.get("label") or f"Set {result.get('rank', '?')}"
            group = QGroupBox(f"{label}  ·  {worse:.2f}% below top")
            group_layout = QVBoxLayout(group)
            action_row = QHBoxLayout()
            action_row.addWidget(QLabel("Load candidate:"))
            load_tp = QPushButton("Load into TP")
            load_ws = QPushButton("Load into WS")
            load_quick = QPushButton("Load into Quick View")
            load_tp.setEnabled(self.parent() is not None)
            load_ws.setEnabled(self.parent() is not None)
            load_quick.setEnabled(self.parent() is not None)
            if self.parent() is not None:
                load_tp.clicked.connect(lambda _checked=False, index=result_index: self._load_result(index, "tp"))
                load_ws.clicked.connect(lambda _checked=False, index=result_index: self._load_result(index, "ws"))
                load_quick.clicked.connect(
                    lambda _checked=False, index=result_index: self._load_result(index, "quick")
                )
            action_row.addWidget(load_tp)
            action_row.addWidget(load_ws)
            action_row.addWidget(load_quick)
            pair_tp, pair_ws = _optimizer_result_players(result)
            combined_pair = (
                pair_tp is not None and pair_ws is not None and pair_tp is not pair_ws
            )
            if combined_pair:
                load_pair = QPushButton("Load linked TP + WS")
                load_pair.setObjectName("primaryAction")
                load_pair.setToolTip(
                    "Load both halves of this combined result into the TP → WS workspace."
                )
                load_pair.setEnabled(self.parent() is not None)
                if self.parent() is not None:
                    load_pair.clicked.connect(
                        lambda _checked=False, index=result_index:
                        self._load_result(index, "tpws")
                    )
                action_row.addWidget(load_pair)
            action_row.addStretch(1)
            group_layout.addLayout(action_row)
            if combined_pair:
                linked = QLabel(
                    "Linked weapon overlay · " + " · ".join(
                        f"{slot.upper()}: {item_name(pair_ws.gearset.get(slot, gear.Empty))}"
                        for slot in WEAPON_SLOTS
                    )
                )
                linked.setObjectName("linkedWeaponOverlay")
                linked.setWordWrap(True)
                group_layout.addWidget(linked)
                row = QHBoxLayout()
                row.addWidget(self._gear_panel("TP set", pair_tp.gearset))
                row.addWidget(self._gear_panel("WS set", pair_ws.gearset))
                group_layout.addLayout(row)
            else:
                grid = QGridLayout()
                player = result.get("player")
                gearset = getattr(player, "gearset", {}) if player is not None else {}
                self._add_gear_cells(grid, gearset, columns=4, cell_width=198)
                group_layout.addLayout(grid)
            content_layout.addWidget(group)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignRight)

    def _load_result(self, index: int, destination: str):
        """Load one visible retained candidate into the requested set."""
        if self.parent() is not None:
            self.parent().load_optimizer_result(index, destination)
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
            cell.setObjectName("topSetGearCell")
            cell.setFixedSize(cell_width, 68)
            cell.setFrameShape(QFrame.Shape.StyledPanel)
            # Keep the card compact, but expose the complete modeled item row
            # (including augments) on hover so the retained-set view is useful for
            # comparing candidates without opening another picker.
            cell.setToolTip(
                "Double-click to lock or unlock this optimizer slot.\n"
                "Right-click to add or remove the item from the global blacklist.\n\n"
                + item_tooltip(item)
            )
            cell.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            cell.customContextMenuRequested.connect(
                lambda position, target=cell, target_slot=slot, target_item=item:
                self._show_gear_cell_menu(target, position, target_slot, target_item)
            )
            cell.installEventFilter(self)
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(5, 4, 5, 4)
            icon_label = QLabel()
            icon_label.setFixedSize(36, 36)
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon = self.icons.icon(item)
            if not icon.isNull():
                icon_label.setPixmap(icon.pixmap(QSize(32, 32)))
            icon_label.setToolTip(item_tooltip(item))
            icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            cell_layout.addWidget(icon_label)
            text_layout = QVBoxLayout()
            slot_label = QLabel(slot.upper())
            slot_label.setObjectName("topSetSlotLabel")
            name = str(item.get("Name") or "Empty")
            name_label = QLabel()
            name_label.setObjectName("topSetItemName")
            name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            name_label.setToolTip(name)
            name_label.setText(QFontMetrics(name_label.font()).elidedText(
                name, Qt.TextElideMode.ElideRight, max(90, cell_width - 58)
            ))
            slot_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            text_layout.addWidget(slot_label)
            text_layout.addWidget(name_label)
            text_layout.addStretch(1)
            cell_layout.addLayout(text_layout, 1)
            row, column = divmod(index, columns)
            grid.addWidget(cell, row, column)
            self._gear_cells.append((cell, slot, item))
        self._refresh_gear_cell_styles()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonDblClick:
            match = next(
                ((slot, item) for cell, slot, item in self._gear_cells if cell is watched),
                None,
            )
            if match is not None:
                self._toggle_gear_lock(*match)
                return True
        return super().eventFilter(watched, event)

    def _show_gear_cell_menu(self, cell: QFrame, position, slot: str, item: dict):
        name = item_name(item)
        if name == "Empty" or self.parent() is None:
            return
        blocked = _blacklist_matches(item, self.parent().gear_blacklist)
        menu = QMenu(self)
        blacklist = menu.addAction(
            f"Remove {name} from global blacklist" if blocked
            else f"Add {name} to global blacklist"
        )
        blacklist.triggered.connect(
            lambda _checked=False: self._set_gear_blacklisted(name, not blocked)
        )
        menu.exec(cell.mapToGlobal(position))

    def _set_gear_blacklisted(self, name: str, blocked: bool):
        parent = self.parent()
        if parent is None:
            return
        parent.set_optimizer_item_blacklisted(name, blocked)
        self._refresh_gear_cell_styles()

    def _toggle_gear_lock(self, slot: str, item: dict):
        parent = self.parent()
        if parent is None or item_name(item) == "Empty":
            return
        parent.toggle_optimizer_item_lock(slot, item_name(item))
        self._refresh_gear_cell_styles()

    def _refresh_gear_cell_styles(self):
        parent = self.parent()
        if parent is None:
            return
        for cell, slot, item in self._gear_cells:
            name = item_name(item)
            blocked = _blacklist_matches(item, parent.gear_blacklist)
            cell.setProperty("blacklisted", blocked)
            cell.setProperty("locked", not blocked and parent.locked_gear.get(slot) == name)
            cell.style().unpolish(cell)
            cell.style().polish(cell)


class GearBlacklistDialog(QDialog):
    """Edit the account-wide item-name blacklist used by every character."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Global gear blacklist")
        self.resize(560, 480)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Blacklisted base names are hidden from every character's gear pickers "
            "and optimizer candidates. Augmented variants are covered by their base name. "
            "The automatic helper only compares gear within each character's own inventory."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter available gear...")
        self.filter.textChanged.connect(self._filter_items)
        layout.addWidget(self.filter)
        self.items = QListWidget()
        self.items.setAlternatingRowColors(True)
        self.items.setIconSize(QSize(32, 32))
        self.items.setToolTip("Check an item to hide it from every character and optimizer section.")
        available_items = parent.available_blacklist_records()
        for key in parent.gear_blacklist:
            available_items.setdefault(key, {"Name": key})
        for key, record in available_items.items():
            item = QListWidgetItem(str(record.get("Name") or record.get("Name2") or key))
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setIcon(parent.icons.icon(record))
            item.setToolTip(item_tooltip(record))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if key in parent.gear_blacklist else Qt.CheckState.Unchecked
            )
            self.items.addItem(item)
        layout.addWidget(self.items, 1)
        self.auto_status = QLabel()
        self.auto_status.setWordWrap(True)
        layout.addWidget(self.auto_status)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.auto_blacklist = QPushButton("Auto-blacklist obvious non-upgrades")
        self.auto_blacklist.setToolTip(
            "Marks only fully modeled, unconditional, unaugmented base items when every "
            "known variant is strictly dominated by another item in every compatible slot. "
            "Items with special effects, conditional bonuses, warnings, or augments are left alone. "
            "Save applies the marked entries."
        )
        self.auto_blacklist.clicked.connect(self._mark_obvious_non_upgrades)
        buttons.addButton(self.auto_blacklist, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _filter_items(self, text: str):
        needle = text.strip().casefold()
        for index in range(self.items.count()):
            item = self.items.item(index)
            item.setHidden(bool(needle) and needle not in item.text().casefold())

    def _mark_obvious_non_upgrades(self):
        suggestions = self.parent().obvious_blacklist_suggestions()
        if not suggestions:
            self.auto_status.setText("No globally safe non-upgrades were found.")
            return
        marked = 0
        for index in range(self.items.count()):
            item = self.items.item(index)
            key = str(item.data(Qt.ItemDataRole.UserRole) or "")
            if key in suggestions and item.checkState() != Qt.CheckState.Checked:
                item.setCheckState(Qt.CheckState.Checked)
                marked += 1
        self.auto_status.setText(
            f"Marked {marked} clearly dominated base item(s). "
            "Conditional, augmented, special-effect, and uncertain items were skipped. "
            "Save to apply; existing checks were left unchanged."
        )

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


class OvernightScenarioDialog(QDialog):
    """Choose the enemy scenarios included in an overnight cache batch."""

    def __init__(self, enemy_names: list[str], current_enemy: str, selected: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Overnight simulation scenarios")
        self.resize(420, 520)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Select one or more enemies. The current enemy keeps its edited stats; "
            "other entries use their preset stats. The current character, buffs, "
            "abilities, gear candidates, TP values, and WS coverage are reused for each enemy."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.enemy_list = QListWidget()
        selected_names = set(selected)
        for name in enemy_names:
            row = QListWidgetItem(name)
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(
                Qt.CheckState.Checked if name in selected_names
                else Qt.CheckState.Unchecked
            )
            self.enemy_list.addItem(row)
        layout.addWidget(self.enemy_list, 1)

        controls = QHBoxLayout()
        select_current = QPushButton("Current only")
        select_current.clicked.connect(lambda: self._set_checked({current_enemy}))
        select_all = QPushButton("Select all")
        select_all.clicked.connect(
            lambda: self._set_checked(set(enemy_names))
        )
        controls.addWidget(select_current)
        controls.addWidget(select_all)
        controls.addStretch(1)
        layout.addLayout(controls)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_checked(self, names: set[str]):
        for index in range(self.enemy_list.count()):
            row = self.enemy_list.item(index)
            row.setCheckState(
                Qt.CheckState.Checked if row.text() in names
                else Qt.CheckState.Unchecked
            )

    def selected_names(self) -> list[str]:
        return [
            self.enemy_list.item(index).text()
            for index in range(self.enemy_list.count())
            if self.enemy_list.item(index).checkState() == Qt.CheckState.Checked
        ]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WSDist — Qt")
        self.resize(1220, 820)
        self.setMinimumSize(QSize(980, 650))
        self.icons = GearIconProvider()
        window_icon = self.icons.icon({"Item ID": 23937})
        if not window_icon.isNull():
            self.setWindowIcon(window_icon)
        self.settings = QSettings("WSDist", "QtGui")
        raw_favorites = self.settings.value("favorites/weapon_setups", "{}", str)
        try:
            parsed_favorites = json.loads(raw_favorites or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed_favorites = {}
        self.favorite_weapon_setups = parsed_favorites if isinstance(parsed_favorites, dict) else {}
        raw_characters = self.settings.value("favorites/characters", "[]", str)
        try:
            parsed_characters = json.loads(raw_characters or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed_characters = []
        self.favorite_characters = {
            str(value) for value in parsed_characters if isinstance(value, str) and value.strip()
        }
        cache_path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
        self.simulation_cache = SimulationCache(
            Path(cache_path) if cache_path else APP_DIR / ".cache", source_hash=CACHE_SOURCE_HASH
        )
        history_directory = Path(cache_path) if cache_path else APP_DIR / ".cache"
        try:
            self.result_history = ResultHistory(
                history_directory, source_hash=CACHE_SOURCE_HASH, limit=100,
            )
        except (OSError, sqlite3.Error):
            # Some portable Python/Ashita launches report a cache location
            # that exists but is not writable.  Keep result history usable in
            # the application directory without touching character settings.
            self.result_history = ResultHistory(
                APP_DIR / ".cache", source_hash=CACHE_SOURCE_HASH, limit=100,
            )
        self.cache_enabled = self.settings.value("simulation_cache/enabled", True, bool)
        # Quick Look calculations are typically millisecond-scale.  Keep them
        # responsive within this process without filling the durable optimizer
        # cache with opaque one-off rows.
        self._quick_lookup_cache: OrderedDict[str, dict] = OrderedDict()
        self._quick_lookup_cache_limit = 256
        self._active_optimizer_cache: dict | None = None
        self.bridge_store = BridgeStore()
        self.character_paths: dict[str, Path] = {}
        self._active_character_key = ""
        self.equipment = _base_equipment()
        self.aspirational_catalog = _aspirational_catalog()
        self.aspirational_selected: set[str] = set()
        self.optimizer_thread: OptimizeThread | None = None
        self.overnight_thread: OvernightSimulationThread | None = None
        self.simulation_thread: SimulationThread | None = None
        self.quick_distribution_thread: PlotThread | None = None
        self._quick_distribution_threads: list[PlotThread] = []
        self._last_quick_result: dict | None = None
        self._history_selected_id: int | None = None
        self._workspace_generated_set_name = ""
        self._dashboard_ws_ranking_active = False
        self._dashboard_ws_ranking_result: dict | None = None
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
        self._optimizer_eta_started_at: float | None = None
        self._optimizer_progress_samples: list[tuple[float, float]] = []
        self._optimizer_status_timer = QTimer(self)
        self._optimizer_status_timer.setInterval(1000)
        self._optimizer_status_timer.timeout.connect(self._refresh_optimizer_status)
        self._optimizer_run_cards: dict[int, dict] = {}
        self._optimizer_log_messages: list[str] = []
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
        splitter.setObjectName("mainSplitter")
        splitter.setOpaqueResize(False)
        splitter.addWidget(self._build_inputs())
        splitter.addWidget(self._build_workspace())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setSizes([270, 950])
        root = QWidget()
        root.setObjectName("root")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(4)
        header = QHBoxLayout()
        header.setSpacing(3)
        title = QLabel("FFXI Sim")
        title.setObjectName("title")
        title.setFixedWidth(270)
        simulation_strip = QFrame()
        simulation_strip.setObjectName("simulationHeaderPanel")
        simulation_strip.setProperty("state", "idle")
        self.simulation_header_panel = simulation_strip
        simulation_layout = QHBoxLayout(simulation_strip)
        simulation_layout.setContentsMargins(10, 5, 10, 5)
        simulation_layout.setSpacing(8)
        simulation_label = QLabel("SIMULATION")
        simulation_label.setObjectName("simulationHeaderLabel")
        self.optimizer_control_layout.removeWidget(self.optimizer_run_progress)
        self.optimizer_primary_controls.removeWidget(self.stop_optimizer_button)
        self.optimizer_primary_controls.removeWidget(self.show_optimizer_status_button)
        self.show_optimizer_status_button.setMinimumHeight(34)
        self.show_optimizer_status_button.setMaximumHeight(38)
        self.stop_optimizer_button.setMinimumHeight(34)
        self.stop_optimizer_button.setMaximumHeight(38)
        self.optimizer_run_progress.setMinimumHeight(24)
        self.optimizer_run_progress.setMaximumHeight(24)
        self.optimizer_run_progress.setMaximumWidth(520)
        self.show_results_button = QPushButton("Show Gear")
        self.show_results_button.setObjectName("optimizerResultsAction")
        self.show_results_button.setMinimumHeight(34)
        self.show_results_button.setMaximumHeight(38)
        self.show_results_button.setEnabled(bool(self.optimizer_top_results))
        self.show_results_button.setToolTip(
            "Open the retained optimizer gear sets (up to 50 distinct results)."
        )
        self.show_results_button.clicked.connect(self.show_top_sets)
        simulation_layout.addWidget(simulation_label)
        simulation_layout.addWidget(self.show_optimizer_status_button)
        simulation_layout.addWidget(self.stop_optimizer_button)
        simulation_layout.addWidget(self.optimizer_run_progress, 1)
        self.optimizer_header_eta = QLabel("Est. Time Remaining: --")
        self.optimizer_header_eta.setObjectName("optimizerHeaderEta")
        self.optimizer_header_eta.setMinimumWidth(150)
        self.optimizer_header_eta.setToolTip(
            "Estimated seconds remaining for the active simulation."
        )
        simulation_layout.addWidget(self.optimizer_header_eta)
        simulation_layout.addStretch(1)
        simulation_layout.addWidget(self.show_results_button)
        header.addWidget(title)
        header.addWidget(simulation_strip, 1)
        root_layout.addLayout(header)
        root_layout.addWidget(splitter, 1)
        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready — no background polling")
        stylesheet = """
            QMainWindow, QDialog { background: #171624; color: #f5f1ff; font-family: 'Segoe UI', Arial; font-size: 13px; }
            QWidget { color: #f5f1ff; background-color: #171624; }
            QLabel, QCheckBox { background: transparent; }
            QWidget#root { background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 1, stop: 0 #5d5d62, stop: .48 #34343a, stop: 1 #5b5b60); }
            QLabel#title { min-height: 34px; background: #19172e; border: 2px solid #5a566f; border-left: 4px solid #d6ad68; color: #fffaff; font-size: 21px; font-weight: 700; padding: 4px 8px; }
            QFrame#simulationHeaderPanel { min-height: 34px; background: #19172e; border: 2px solid #5a566f; }
            QFrame#simulationHeaderPanel[state="starting"], QFrame#simulationHeaderPanel[state="running"], QFrame#simulationHeaderPanel[state="warming"] { background: #24213a; border-color: #9c8ca4; border-left: 5px solid #d6ad68; }
            QFrame#simulationHeaderPanel[state="stopping"] { background: #352819; border-color: #e6c983; }
            QFrame#simulationHeaderPanel[state="completed"], QFrame#simulationHeaderPanel[state="restored"] { background: #252d29; border-color: #9caf94; }
            QFrame#simulationHeaderPanel[state="failed"] { background: #34252b; border-color: #c58d91; }
            QLabel#simulationHeaderLabel { color: #e6c983; border: 0; font-size: 11px; font-weight: 900; letter-spacing: 1px; padding: 0 4px; }
            QSplitter#mainSplitter { background: #24213a; }
            QSplitter::handle { background: #57536f; width: 2px; }
            QMenuBar { background: #19172e; color: #f5f1ff; border-bottom: 1px solid #57536f; }
            QMenuBar::item { padding: 5px 10px; background: transparent; }
            QMenuBar::item:selected, QMenu::item:selected { background: #493a68; color: #fffaff; }
            QMenu { background: #24213a; color: #f5f1ff; border: 1px solid #716893; }
            QMenu::item { padding: 6px 26px 6px 12px; }
            QGroupBox { font-weight: 600; margin-top: 10px; padding: 5px; border: 1px solid #57536f; border-radius: 4px; background: qlineargradient(y1: 0, y2: 1, stop: 0 #242344, stop: .55 #17162d, stop: 1 #211e40); }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 5px; color: #eeeaf2; background: #242044; }
            QLabel#sectionTitle { color: #fffaff; font-size: 16px; font-weight: 700; padding: 5px 8px; border: 1px solid #5a566f; border-left: 4px solid #d6ad68; border-radius: 3px; background: #19172e; }
            QLabel#dashboardValue { color: #e6c983; font-weight: 700; padding: 3px 6px; background: #17142e; border: 1px solid #3d3955; }
            QLabel#dashboardStatus, QLabel#readinessDetails { color: #d0ccd6; padding: 7px 9px; background: #17142e; border: 1px solid #57536f; }
            QLabel#workflowStep { color: #918ca0; padding: 7px; background: #1b1930; border: 1px solid #454158; font-weight: 700; }
            QLabel#workflowStep[state="complete"] { color: #8cf3b2; border-color: #397055; }
            QLabel#workflowStep[state="current"] { color: #fff45c; border-color: #d6ad68; }
            QLabel#workflowStep[state="optional"] { color: #35aee9; border-color: #39718d; }
            QLabel#workflowStep[state="blocked"] { color: #ffc4c1; border-color: #8d4f58; }
            QPushButton { padding: 4px 8px; color: #f8f2ff; background: #282348; border: 1px solid #716893; border-radius: 3px; }
            QPushButton:hover { background: #493a68; border-color: #d9b36e; }
            QPushButton:pressed { background: #5a4173; }
            QPushButton:disabled { color: #8e899d; background: #211f31; border-color: #413d52; }
            QPushButton#primaryAction { color: #fffaff; background: #5a4173; border: 2px solid #d6ad68; font-weight: 700; }
            QFrame#optimizerControlPanel { background: #211f36; border: 1px solid #716893; border-left: 5px solid #d6ad68; }
            QFrame#optimizerControlPanel[state="starting"] { background: #30291d; border-color: #e6c983; }
            QFrame#optimizerControlPanel[state="running"], QFrame#optimizerControlPanel[state="warming"] { background: #24213a; border: 2px solid #8f829b; border-left: 6px solid #d6ad68; }
            QFrame#optimizerControlPanel[state="stopping"], QFrame#optimizerControlPanel[state="stopped"] { background: #352819; border-color: #e6c983; }
            QFrame#optimizerControlPanel[state="failed"] { background: #34252b; border-color: #c58d91; }
            QFrame#optimizerControlPanel[state="completed"], QFrame#optimizerControlPanel[state="restored"] { background: #252d29; border-color: #9caf94; }
            QLabel#optimizerRunState { min-width: 150px; padding: 4px 8px; color: #eeeaf2; border: 1px solid #716893; font-weight: 800; letter-spacing: 0.6px; }
            QLabel#optimizerRunState[state="starting"] { color: #fff45c; border-color: #e6c983; }
            QLabel#optimizerRunState[state="running"], QLabel#optimizerRunState[state="warming"] { color: #e6c983; border-color: #8f829b; }
            QLabel#optimizerRunState[state="completed"], QLabel#optimizerRunState[state="restored"] { color: #b7c5ab; border-color: #71806b; }
            QLabel#optimizerRunState[state="stopping"], QLabel#optimizerRunState[state="stopped"] { color: #ffe2a8; border-color: #8a6430; }
            QLabel#optimizerRunState[state="failed"] { color: #ffc4c1; border-color: #8d4f58; }
            QLabel#optimizerRunSummary { color: #f5f1ff; font-weight: 600; }
            QLabel#optimizerHeaderEta { min-width: 150px; padding: 4px 7px; color: #e6c983; background: #302b47; border: 1px solid #8f829b; font-weight: 700; }
            QLabel#optimizerHeaderEta[state="completed"], QLabel#optimizerHeaderEta[state="restored"] { color: #b7c5ab; border-color: #71806b; }
            QLabel#optimizerHeaderEta[state="failed"] { color: #ffc4c1; border-color: #8d4f58; }
            QLabel#optimizerHeaderEta[state="stopping"], QLabel#optimizerHeaderEta[state="stopped"] { color: #ffe2a8; border-color: #8a6430; }
            QPushButton#optimizerStartAction { color: #171624; background: #e6c983; border: 2px solid #fff0a8; font-weight: 800; }
            QPushButton#optimizerStartAction:hover { background: #fff0a8; border-color: #fffaff; }
            QPushButton#optimizerStartAction:disabled { color: #8e899d; background: #302d3a; border-color: #57536f; }
            QPushButton#optimizerStopAction { color: #fffaff; background: #7a303a; border: 2px solid #d77a83; font-weight: 800; }
            QPushButton#optimizerStopAction:hover { background: #9a3b48; border-color: #ffc4c1; }
            QPushButton#optimizerStopAction:disabled { color: #766f80; background: #251f2c; border-color: #493d4a; }
            QPushButton#optimizerShowAction { color: #fffaff; background: #4b405a; border: 2px solid #a18fa3; font-weight: 800; }
            QPushButton#optimizerShowAction:hover { background: #5b4c67; border-color: #d6ad68; }
            QPushButton#optimizerResultsAction { color: #fffaff; background: #302b47; border: 2px solid #8f829b; font-weight: 800; }
            QPushButton#optimizerResultsAction:hover { background: #493f58; border-color: #d6ad68; }
            QProgressBar#optimizerRunProgress { min-height: 20px; max-height: 24px; color: #fffaff; text-align: center; font-size: 10px; font-weight: 800; background: #5b5981; border: 2px solid #d6ad68; border-left-width: 6px; border-right-width: 6px; border-radius: 5px; }
            QProgressBar#optimizerRunProgress::chunk { background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 #d999ad, stop: .5 #c77f99, stop: 1 #a95f7b); border-right: 1px solid #ead0d8; }
            QLabel#simulationProgressValue, QLabel#simulationEtaValue, QLabel#simulationPhaseValue { background: #1b1930; border: 1px solid #454158; padding: 7px 9px; }
            QLabel#simulationResultValue { color: #fffaff; background: #302535; border: 1px solid #806b82; border-left: 4px solid #c995a9; padding: 7px 9px; font-weight: 700; }
            QLabel#simulationActivity { color: #e6c983; font-weight: 800; }
            QDialog#simulationStatusDialog { background: #4a4a51; color: #fffaff; }
            QDialog#simulationStatusDialog QGroupBox { background: qlineargradient(y1: 0, y2: 1, stop: 0 #55555e, stop: .5 #42424a, stop: 1 #38383f); border: 1px solid #85818f; }
            QDialog#simulationStatusDialog QGroupBox::title { color: #fffaff; background: #4a4a52; }
            QDialog#simulationStatusDialog QTextEdit, QDialog#simulationStatusDialog QLineEdit { color: #fffaff; background: #24242b; border: 1px solid #85818f; selection-background-color: #6b5963; }
            QDialog#simulationStatusDialog QPushButton { color: #fffaff; background: #414149; border: 1px solid #96919e; }
            QDialog#simulationStatusDialog QPushButton:hover { background: #55525a; border-color: #d6ad68; }
            QDialog#simulationStatusDialog QLabel#simulationProgressValue, QDialog#simulationStatusDialog QLabel#simulationEtaValue, QDialog#simulationStatusDialog QLabel#simulationPhaseValue { background: #303037; border: 1px solid #77737f; }
            QDialog#simulationStatusDialog QLabel#simulationResultValue { background: #41393a; border: 1px solid #8d7d75; border-left: 4px solid #d6ad68; }
            QDialog#simulationStatusDialog QFrame#optimizerCurrentResult { background: #343238; border: 1px solid #817b86; border-left: 4px solid #d6ad68; }
            QLabel#optimizerCurrentResultValues { color: #fffaff; border: 0; font-family: Consolas, monospace; font-weight: 700; }
            QDialog#simulationStatusDialog QWidget#simulationCacheBar, QDialog#simulationStatusDialog QWidget#simulationFooter { background: transparent; }
            QDialog#simulationStatusDialog QCheckBox::indicator { background: #303037; border-color: #d6ad68; }
            QDialog#simulationStatusDialog QScrollBar:vertical { background: #303037; }
            QDialog#simulationStatusDialog QScrollBar::handle:vertical { background: #77737f; }
            QLabel#linkedWeaponOverlay { color: #e6c983; background: #211f36; border: 1px solid #716893; border-left: 4px solid #d6ad68; padding: 5px 8px; font-weight: 700; }
            QFrame#topSetGearCell { background: #1d1a36; border: 1px solid #57536f; border-radius: 3px; }
            QFrame#topSetGearCell:hover { background: #2a2442; border-color: #d6ad68; }
            QFrame#topSetGearCell[locked="true"] { background: #3b311d; border: 2px solid #e6c983; }
            QFrame#topSetGearCell[blacklisted="true"] { background: #211f29; border: 1px solid #4b4854; color: #817d8a; }
            QLabel#topSetSlotLabel { color: #8f89a5; font-size: 10px; }
            QFrame#topSetGearCell[locked="true"] QLabel#topSetSlotLabel { color: #e6c983; font-weight: 800; }
            QFrame#topSetGearCell[blacklisted="true"] QLabel { color: #817d8a; }
            QPushButton#profileGearSlot { text-align: center; padding: 0; font-size: 9px; background: qlineargradient(y1: 0, y2: 1, stop: 0 #5d5b70, stop: 1 #454357); border: 1px solid #77728f; border-radius: 0; }
            QPushButton#profileGearSlot:hover { border: 1px solid #d6ad68; }
            QPushButton#profileGearSlot[selected="true"] { background: #555268; border: 2px solid #dfa064; color: #fffaff; }
            QLabel#emptySlotLabel { color: #f7f4ff; background: transparent; border: 0; padding: 0; font-size: 9px; }
            QLabel#profileGearDetail { color: #f5f1ff; padding: 5px 8px; background: #17142e; border: 1px solid #57536f; }
            QLabel#profileRecipe { color: #f5f1ff; padding: 7px 9px; background: #17142e; border: 1px solid #57536f; }
            QLabel#profileWarning { color: #ffe2a8; padding: 5px 8px; background: #352819; border: 1px solid #8a6430; }
            QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit { min-height: 24px; padding: 1px 4px; color: #f5f1ff; background: #17142e; border: 1px solid #5d5878; border-radius: 3px; selection-color: #fffaff; selection-background-color: #654266; }
            QComboBox::drop-down { border: 0; width: 20px; }
            QComboBox QAbstractItemView { background: #17142e; color: #d0ccd6; border: 1px solid #716893; selection-color: #ff6fb3; selection-background-color: #34334d; }
            QListWidget, QTableWidget { background: #17142e; color: #d0ccd6; border: 1px solid #5d5878; alternate-background-color: #1d1a36; gridline-color: #302d4a; }
            QListWidget::item { padding: 2px 5px; }
            QListWidget::item:selected { color: #ff6fb3; background: #282348; border: 0; }
            QTableWidget::item { padding: 2px 5px; border: 0; }
            QTableWidget::item:selected { color: #fffaff; background: #4a3038; border: 0; }
            QHeaderView { background: #17142e; color: #eeeaf2; }
            QHeaderView::section { min-height: 24px; padding: 2px 6px; color: #eeeaf2; background: #24213a; border: 0; border-right: 1px solid #57536f; border-bottom: 1px solid #716893; font-weight: 700; }
            QTableCornerButton::section { background: #24213a; border: 0; border-right: 1px solid #57536f; border-bottom: 1px solid #716893; }
            QListWidget::indicator { width: 18px; height: 18px; border: 2px solid #8f89a5; background: #17142e; }
            QListWidget::indicator:checked { image: url(__CHECKMARK_GOLD__); border: 0; background: #e6c983; }
            QTabWidget::pane { border: 1px solid #57536f; background: #1b1930; }
            QTabBar::tab { padding: 6px 10px; margin: 0 1px; color: #d0ccd6; background: #24213a; border: 1px solid #57536f; border-bottom: 0; border-top-left-radius: 3px; border-top-right-radius: 3px; }
            QTabBar::tab:selected { color: #fffaff; background: #493a68; border-color: #d6ad68; }
            QTabBar::tab:hover:!selected { background: #302b4d; }
            QScrollArea { background: transparent; border: 0; }
            QScrollBar:vertical { background: #17142e; width: 10px; margin: 0; }
            QScrollBar::handle:vertical { background: #57536f; min-height: 24px; border-radius: 4px; }
            QScrollBar:horizontal { background: #17142e; height: 10px; margin: 0; }
            QScrollBar::handle:horizontal { background: #57536f; min-width: 24px; border-radius: 4px; }
            QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: transparent; border: 0; }
            QProgressBar { min-height: 16px; color: #fffaff; text-align: center; background: #17142e; border: 1px solid #57536f; }
            QProgressBar::chunk { background: #5b4778; border-right: 1px solid #d6ad68; }
            QStatusBar { background: #151329; color: #c7c2d2; border-top: 1px solid #57536f; }
            QToolTip { background: #17142e; color: #fffaff; border: 1px solid #a39ab9; padding: 6px; }
            QCheckBox { spacing: 7px; }
            QCheckBox::indicator { width: 18px; height: 18px; border: 2px solid #d6ad68; background: #211b30; }
            QCheckBox::indicator:hover { border-color: #fff0a8; background: #332b1f; }
            QCheckBox::indicator:checked { image: url(__CHECKMARK_GOLD__); border: 0; background: #e6c983; }
            QGroupBox#equipmentPanel { background: qlineargradient(y1: 0, y2: 1, stop: 0 #242344, stop: .5 #13132b, stop: 1 #211e40); border: 2px solid #57536f; margin-top: 0; padding: 0; }
            QPushButton#equip_slot { background: qlineargradient(y1: 0, y2: 1, stop: 0 #5d5b70, stop: .52 #504e62, stop: 1 #454357); border: 1px solid #77728f; border-radius: 0; color: #f7f4ff; padding: 0; font-size: 9px; text-align: center; }
            QPushButton#equip_slot:hover { border: 1px solid #d6ad68; }
            QPushButton#equip_slot:pressed { background: #3f3c52; border: 1px solid #e6c983; }
            QPushButton#equip_slot[selected="true"] { background: #555268; border: 2px solid #dfa064; }
            QFrame#quickResultPanel { background: #17142e; border: 2px solid #57536f; border-left: 4px solid #d6ad68; }
            QLabel#quickResult { color: #fffaff; background: transparent; border: 0; padding: 3px 5px; font-weight: 700; }
            QWidget#quickResultGraph { background: #17142e; border: 0; }
            QLabel#quickResultGraphFallback { color: #a9a4b5; background: transparent; border: 0; padding: 8px; }
            QLabel#cycleIntro { color: #d0ccd6; padding: 2px 4px 4px; }
            QGroupBox#cycleSetPanel, QGroupBox#cycleActionPanel, QGroupBox#cycleStatusPanel { background: #1b1930; border: 1px solid #57536f; }
            QGroupBox#cycleSetPanel { border-top: 2px solid #d6ad68; }
            QGroupBox#cycleSetPanel::title, QGroupBox#cycleActionPanel::title, QGroupBox#cycleStatusPanel::title { color: #fffaff; font-weight: 700; }
            QLabel#cycleSetSummary { color: #c7c2d2; background: #17142e; border: 1px solid #302d4a; padding: 5px 7px; }
            QLabel#cycleStatus { color: #ffe2a8; background: #352819; border: 1px solid #8a6430; padding: 7px 9px; font-weight: 600; }
            QPushButton#cycleRunAction { color: #171624; background: #e6c983; border: 2px solid #fff0a8; font-weight: 800; }
            QPushButton#cycleRunAction:hover { background: #fff0a8; border-color: #fffaff; }
            QPushButton#cycleStopAction { color: #fffaff; background: #7a303a; border: 2px solid #d77a83; font-weight: 800; }
            QPushButton#cycleStopAction:hover { background: #9a3b48; border-color: #ffc4c1; }
            QPushButton#cycleSecondaryAction { background: #282348; border: 1px solid #716893; }
            QPushButton#cycleSecondaryAction:hover { background: #493a68; border-color: #d6ad68; }
            QGroupBox#quickStatsSection { margin-top: 8px; padding: 2px; }
            QFrame#quickStatRow { background: #17142e; border: 1px solid #302d4a; }
            QLabel#quickStatName { color: #d0ccd6; font-weight: 600; }
            QLabel#quickStatValue { color: #f5f1ff; font-family: Consolas, monospace; font-weight: 700; }
            QLabel#quickStatAccent { font-family: Consolas, monospace; font-weight: 700; }
            QCheckBox#referenceEnemyToggle { font-size: 10pt; font-weight: 600; spacing: 5px; }
            QLabel#buffIntro { color: #d0ccd6; padding: 1px 2px 3px; }
            QLabel#buffPresetNote { color: #c7c2d2; background: #1b1930; border-left: 3px solid #d6ad68; padding: 4px 7px; }
            QFrame#lacEditorToolbar { background: #252526; border: 1px solid #3f3f46; }
            QLabel#lacEditorPath { color: #9cdcfe; background: transparent; border: 0; padding: 2px 5px; }
            QLabel#lacEditorStatus { color: #c7c2d2; background: #252526; border-left: 3px solid #007acc; padding: 5px 8px; }
            QPlainTextEdit#lacCodeEditor { color: #d4d4d4; background: #1e1e1e; border: 1px solid #3f3f46; selection-background-color: #264f78; selection-color: #ffffff; padding: 0; }
            QPushButton#lacEditorSave { color: #ffffff; background: #0e639c; border: 1px solid #1177bb; font-weight: 700; }
            QPushButton#lacEditorSave:hover { background: #1177bb; border-color: #3794ff; }
            QPushButton#lacEditorSave:disabled { color: #777777; background: #2d2d30; border-color: #3f3f46; }
            QFrame#candidateCard { background: #242344; border: 1px solid #716893; border-radius: 7px; }
            QFrame#candidateCard[locked="true"] { background: #3b311d; border: 2px solid #e6c983; }
            QLabel#candidateSlot { color: #eeeaf2; font-weight: 700; letter-spacing: 0.5px; }
            QLabel#candidateSlot[locked="true"] { color: #fff0a8; }
            QLabel#candidatePlayer { color: #d0ccd6; }
            QPushButton#candidateButton { background: #282348; border: 1px solid #716893; border-radius: 4px; padding: 4px 8px; }
            QPushButton#candidateButton:hover { background: #493a68; }
            QPushButton#candidateButton:pressed { background: #5a4173; }
            QPushButton#candidateButton[locked="true"] { color: #171624; background: #e6c983; border: 2px solid #fff0a8; font-weight: 800; }
            QComboBox#candidateLockCombo[locked="true"] { color: #171624; background: #e6c983; border: 2px solid #fff0a8; font-weight: 800; }
            QLabel#candidateLockLabel[locked="true"] { color: #e6c983; font-weight: 800; }
            QPlainTextEdit { font-family: Consolas, monospace; }
            QPlainTextEdit#resultDetailsText { font-size: 12px; padding: 4px 6px; }
        """
        self.setStyleSheet(stylesheet.replace(
            "__CHECKMARK_GOLD__", (APP_DIR / "assets" / "checkmark-gold.svg").as_posix()
        ))

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("File")
        select_root = QAction("Select Ashita folder...", self)
        select_root.triggered.connect(self.choose_bridge_root)
        refresh = QAction("Refresh selected character", self)
        refresh.triggered.connect(self.refresh_bridge)
        blacklist = QAction("Gear blacklist...", self)
        blacklist.triggered.connect(self.open_gear_blacklist)
        self.cache_enabled_action = QAction("Enable simulation cache", self)
        self.cache_enabled_action.setCheckable(True)
        self.cache_enabled_action.setChecked(self.cache_enabled)
        self.cache_enabled_action.setToolTip(
            "Reuse completed deterministic Quick Look and seeded optimizer results."
        )
        self.cache_enabled_action.toggled.connect(self._set_cache_enabled)
        performance = QAction("Performance and storage...", self)
        performance.triggered.connect(self.show_performance_settings)
        close = QAction("Exit", self)
        close.triggered.connect(self.close)
        file_menu.addActions([select_root, refresh, blacklist])
        file_menu.addSeparator()
        file_menu.addAction(performance)
        file_menu.addSeparator()
        file_menu.addAction(close)

    def _set_cache_enabled(self, enabled: bool):
        self.cache_enabled = bool(enabled)
        if hasattr(self, "cache_enabled_action"):
            self.cache_enabled_action.blockSignals(True)
            self.cache_enabled_action.setChecked(self.cache_enabled)
            self.cache_enabled_action.blockSignals(False)
        self.settings.setValue("simulation_cache/enabled", self.cache_enabled)
        self.statusBar().showMessage(
            "Simulation cache enabled" if self.cache_enabled else "Simulation cache disabled", 4000
        )
        if hasattr(self, "cache_status_value"):
            self._refresh_cache_status()

    @staticmethod
    def _format_bytes(value: int) -> str:
        if value < 1024 * 1024:
            return f"{value / 1024:.1f} KiB"
        return f"{value / (1024 * 1024):.1f} MiB"

    def _refresh_cache_status(self):
        if not hasattr(self, "cache_status_value"):
            return
        summary = self.simulation_cache.summary()
        enabled = "enabled" if self.cache_enabled else "disabled"
        self.cache_status_value.setText(
            f"Cache {enabled}: {summary['entries']} entries, "
            f"{self._format_bytes(summary['bytes'])} payload / "
            f"{self._format_bytes(summary['disk_bytes'])} on disk"
        )

    def show_cache_info(self):
        summary = self.simulation_cache.summary()
        kinds = ", ".join(
            f"{kind}: {count}" for kind, count in sorted(summary.get("kinds", {}).items())
        ) or "none"
        QMessageBox.information(
            self, "Simulation cache",
            f"Caching is {'enabled' if self.cache_enabled else 'disabled'}.\n\n"
            f"{summary['entries']} entries using {self._format_bytes(summary['bytes'])} of payload "
            f"and {self._format_bytes(summary['disk_bytes'])} on disk.\n"
            f"Entries by type: {kinds}.\n"
            "Completed Quick Look evaluations and seeded optimizer searches are reused when every input and calculation source matches."
        )

    def show_performance_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Performance and storage")
        dialog.setMinimumWidth(560)
        layout = QVBoxLayout(dialog)
        reuse = QCheckBox("Reuse completed calculations")
        reuse.setChecked(self.cache_enabled)
        reuse.setToolTip("Reuse only calculations whose effective inputs and formula version match.")
        reuse.toggled.connect(self._set_cache_enabled)
        layout.addWidget(reuse)
        summary = self.simulation_cache.summary()
        storage = QLabel(
            f"Saved calculations: {summary['entries']:,} · "
            f"{self._format_bytes(summary['bytes'])} payload · "
            f"{self._format_bytes(summary['disk_bytes'])} on disk\n"
            "Saved work expires after 90 days and is limited to 250 MiB."
        )
        storage.setWordWrap(True)
        layout.addWidget(storage)
        actions_box = QGroupBox("Advanced cache tools")
        actions_layout = QGridLayout(actions_box)
        warm_rankings = QPushButton("Precompute WS rankings")
        warm_rankings.setToolTip("Precompute current weapon-type rankings at 1,000, 2,000, and 3,000 TP.")
        overnight = QPushButton("Precompute common evaluations")
        overnight.setToolTip("Build reusable Quick Look evaluations for the current candidates.")
        stop = QPushButton("Stop precompute")
        stop.setEnabled(bool(self.overnight_thread and self.overnight_thread.isRunning()))
        clear = QPushButton("Clear saved calculations")
        warm_rankings.clicked.connect(lambda: (dialog.accept(), QTimer.singleShot(0, self.run_warm_cache_from_advanced)))
        overnight.clicked.connect(lambda: (dialog.accept(), QTimer.singleShot(0, self.run_overnight_simulations)))
        stop.clicked.connect(self.stop_overnight_simulations)
        clear.clicked.connect(self.clear_simulation_cache)
        actions_layout.addWidget(warm_rankings, 0, 0)
        actions_layout.addWidget(overnight, 0, 1)
        actions_layout.addWidget(stop, 1, 0)
        actions_layout.addWidget(clear, 1, 1)
        layout.addWidget(actions_box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def run_warm_cache_from_advanced(self):
        previous = self.optimize_action.currentText()
        self.optimize_action.addItem(WARM_CACHE_ACTION)
        self.optimize_action.setCurrentText(WARM_CACHE_ACTION)
        try:
            self.run_optimizer()
        finally:
            self.optimize_action.setCurrentText(previous)
            index = self.optimize_action.findText(WARM_CACHE_ACTION)
            if index >= 0:
                self.optimize_action.removeItem(index)

    def clear_simulation_cache(self):
        if self.simulation_cache.clear():
            self._refresh_cache_status()
            self.statusBar().showMessage("Simulation cache cleared", 4000)
        else:
            QMessageBox.warning(self, "Simulation cache", "The cache could not be cleared.")

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
        return {
            key: str(item.get("Name") or item.get("Name2") or key)
            for key, item in self.available_blacklist_records().items()
        }

    def available_blacklist_records(self) -> dict[str, dict]:
        """Return one representative item record per name in display order."""
        available: dict[str, dict] = {}

        def add(item: dict):
            if item_name(item) == "Empty":
                return
            base = _normalized_item_name(item.get("Name") or item.get("Name2"))
            if base:
                available.setdefault(base, item)

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
        slot_order = {slot: index for index, slot in enumerate(SLOTS)}
        type_slots = {
            "weapon": "main",
            "shield": "sub",
            "grip": "sub",
            "ranged": "ranged",
            "ammo": "ammo",
        }

        def slot_rank(item: dict) -> int:
            slots = item.get("Slots") or ()
            if isinstance(slots, str):
                slots = (slots,)
            ranks = [slot_order[slot] for slot in slots if slot in slot_order]
            if ranks:
                return min(ranks)
            return slot_order.get(type_slots.get(str(item.get("Type") or "").casefold()), len(SLOTS))

        def level_rank(item: dict) -> int:
            return self._item_level(item) or -1

        return dict(sorted(
            available.items(),
            key=lambda entry: (
                slot_rank(entry[1]),
                -level_rank(entry[1]),
                str(entry[1].get("Name") or entry[1].get("Name2") or entry[0]).casefold(),
            ),
        ))

    def obvious_blacklist_suggestions(self) -> dict[str, set[str]]:
        """Find global suggestions without letting one character hide another's gear."""
        items_by_owner: dict[str, dict[str, list[dict]]] = {}

        def add_item(items_by_slot: dict[str, list[dict]], item: dict, slots=None):
            if not isinstance(item, dict) or item_name(item) == "Empty":
                return
            slots = slots if slots is not None else item.get("Slots") or ()
            if isinstance(slots, str):
                slots = (slots,)
            for slot in slots:
                if slot in items_by_slot:
                    items_by_slot[slot].append(item)

        current_owner = str(self.bridge_store.bridge_path or "current character")
        current_items = {slot: [] for slot in SLOTS}
        items_by_owner[current_owner] = current_items
        # ``equipment`` gives the selected character's slot-normalized data.
        for slot, values in self.equipment.items():
            for item in values:
                add_item(current_items, item, (slot,))

        def add_catalog(items_by_slot: dict[str, list[dict]], catalog):
            for item in catalog.values():
                if item.get("Eligible"):
                    add_item(items_by_slot, item)

        add_catalog(current_items, self.bridge_store.catalog)
        for path in self.character_paths.values():
            if self.bridge_store.bridge_path and path.resolve() == self.bridge_store.bridge_path.resolve():
                continue
            try:
                store = BridgeStore(self.bridge_store.ashita_root)
                store.load(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            owner_items = {slot: [] for slot in SLOTS}
            items_by_owner[str(path)] = owner_items
            add_catalog(owner_items, store.catalog)

        suggestions = wsdist.universal_blacklist_suggestions(items_by_owner)
        return {
            name: dominators for name, dominators in suggestions.items()
            if name not in self.gear_blacklist
        }

    def set_gear_blacklist(self, values: set[str]) -> None:
        self.gear_blacklist = {
            _normalized_item_name(value) for value in values if _normalized_item_name(value)
        }
        self.settings.setValue("global_gear_blacklist", json.dumps(sorted(self.gear_blacklist)))
        self._refresh_shared_gear()
        self._reset_invalid_equipment()
        self._refresh_locked_gear_options()
        for slot in SLOTS:
            self._update_candidate_button(slot)
        self.statusBar().showMessage(
            f"Global gear blacklist updated ({len(self.gear_blacklist)} item names).", 5000
        )

    def set_optimizer_item_blacklisted(self, name: str, blocked: bool = True):
        """Apply a result/candidate context-menu blacklist change immediately."""
        normalized = _normalized_item_name(name)
        if not normalized:
            return
        values = set(self.gear_blacklist)
        if blocked:
            values.add(normalized)
        else:
            values.discard(normalized)
        self.set_gear_blacklist(values)
        action = "Added to" if blocked else "Removed from"
        self.statusBar().showMessage(f"{name}: {action} global gear blacklist.", 5000)

    def toggle_optimizer_item_lock(self, slot: str, name: str) -> bool:
        """Toggle a slot lock from an optimizer result or candidate row."""
        if slot not in self.locked_gear or not name or name == "Empty":
            return False
        if self.locked_gear.get(slot) == name:
            self.locked_gear[slot] = ""
            self._update_candidate_button(slot)
            self.statusBar().showMessage(f"{slot.upper()} unlocked.", 4000)
            return False
        available = {item_name(item) for item in self.optimizer_items_for_slot(slot)}
        if name not in available:
            self.statusBar().showMessage(
                f"Cannot lock {name}: it is not an available {slot.upper()} candidate.", 5000
            )
            return False
        self.candidates[slot].add(name)
        self.locked_gear[slot] = name
        self._update_candidate_button(slot)
        self.statusBar().showMessage(f"{slot.upper()} locked to {name}.", 4000)
        return True

    def open_gear_blacklist(self):
        dialog = GearBlacklistDialog(self)
        dialog.exec()

    def _build_inputs(self) -> QWidget:
        content = QWidget()
        content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        bridge = QGroupBox("Character bridge")
        bridge_layout = QVBoxLayout(bridge)
        choose = QPushButton("Select Ashita folder...")
        choose.clicked.connect(self.choose_bridge_root)
        self.character_combo = QComboBox()
        self.character_combo.setEnabled(False)
        self.character_combo.currentTextChanged.connect(self._load_character)
        self.favorite_character_button = QPushButton("☆ Favorite")
        self.favorite_character_button.setCheckable(True)
        self.favorite_character_button.setEnabled(False)
        self.favorite_character_button.setToolTip("Keep this character at the top of the character list.")
        self.favorite_character_button.clicked.connect(self._toggle_favorite_character)
        self.bridge_label = QLabel("No character loaded")
        self.bridge_label.setWordWrap(True)
        bridge_layout.addWidget(choose)
        character_row = QHBoxLayout()
        character_row.addWidget(self.character_combo, 1)
        character_row.addWidget(self.favorite_character_button)
        bridge_layout.addLayout(character_row)
        bridge_layout.addWidget(self.bridge_label)
        layout.addWidget(bridge)

        player = QGroupBox("Player")
        form = QFormLayout(player)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
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
        self.weapon_type_combo = QComboBox()
        self.weapon_type_combo.addItems(WEAPON_TYPE_OPTIONS)
        self.weapon_type_combo.setToolTip(
            "Choose the weapon-skill family explicitly. This controls the weapon-skill list and simulation type; "
            "it does not require the temporary Quick Look weapon to be equipped. Auto uses the equipped Quick Look weapon."
        )
        self.ws_combo = QComboBox()
        self.ws_combo.setEditable(False)
        self.ws_combo.setMaxVisibleItems(18)
        self.ws_combo.setToolTip(
            "Choose a weapon skill from the selected weapon-type family."
        )
        self.spell_combo = QComboBox()
        self.spell_combo.setEditable(True)
        form.addRow("Main job", self.main_job)
        form.addRow("Sub job", self.sub_job)
        form.addRow("Master level", self.master_level)
        form.addRow("Hoxne mastery rank", self.hoxne_mastery_rank)
        form.addRow("TP", self.tp_value)
        form.addRow("Aftermath", self.aftermath)
        form.addRow("Weapon type", self.weapon_type_combo)
        form.addRow("Weapon skill", self.ws_combo)
        form.addRow("Spell", self.spell_combo)
        self.main_job.currentTextChanged.connect(self._refresh_job_data)
        self.sub_job.currentTextChanged.connect(self._refresh_quick_ability_job)
        self.master_level.valueChanged.connect(self._refresh_quick_ability_job)
        self.hoxne_mastery_rank.valueChanged.connect(self._hoxne_mastery_rank_changed)
        self.aftermath.valueChanged.connect(self.refresh_quick_stats)
        self.weapon_type_combo.currentTextChanged.connect(self._refresh_ws_choices)
        layout.addWidget(player)

        enemy_box = QGroupBox("Enemy")
        enemy_form = QFormLayout(enemy_box)
        enemy_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
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
        scroll.setObjectName("contextScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        scroll.setMinimumWidth(250)
        scroll.setMaximumWidth(280)
        return scroll

    def _refresh_favorite_character_button(self, *_args):
        if not hasattr(self, "favorite_character_button"):
            return
        label = self.character_combo.currentText().strip()
        favorite = label in self.favorite_characters
        self.favorite_character_button.setText("★ Favorite" if favorite else "☆ Favorite")
        self.favorite_character_button.setChecked(favorite)
        self.favorite_character_button.setEnabled(bool(label))

    def _toggle_favorite_character(self):
        label = self.character_combo.currentText().strip()
        if not label:
            return
        if label in self.favorite_characters:
            self.favorite_characters.remove(label)
        else:
            self.favorite_characters.add(label)
        self.settings.setValue(
            "favorites/characters", json.dumps(sorted(self.favorite_characters), ensure_ascii=False)
        )
        self._sort_character_choices()
        self._refresh_favorite_character_button()

    def _sort_character_choices(self):
        if not hasattr(self, "character_combo"):
            return
        current = self.character_combo.currentText()
        labels = [self.character_combo.itemText(index) for index in range(self.character_combo.count())]
        labels.sort(key=lambda value: (value not in self.favorite_characters, value.casefold()))
        self.character_combo.blockSignals(True)
        self.character_combo.clear()
        self.character_combo.addItems(labels)
        if current in labels:
            self.character_combo.setCurrentText(current)
        self.character_combo.blockSignals(False)

    def _build_workspace(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)
        self.tabs = QTabWidget()
        self.quick_set = GearSetEditor("Quick View equipment", self, game_grid=True)
        # TP and WS use the same compact reference-style 4x4 equipment grid
        # as Quick Look so the cycle view is scannable and directly comparable.
        self.tp_set = GearSetEditor("TP equipment", self, game_grid=True)
        self.ws_set = GearSetEditor("Weapon-skill equipment", self, game_grid=True)
        self.quick_set.changed.connect(self._gear_changed)
        self.tp_set.changed.connect(self._gear_changed)
        self.ws_set.changed.connect(self._gear_changed)
        # Keep the primary workflow left-to-right: build gear, optimize it,
        # publish profile sets, then inspect saved results.  Supporting
        # calculators and legacy views stay available without competing with
        # the main flow.
        # Active Buffs must be constructed before Profile Builder because the
        # profile recipes use its initialized controls while building.
        active_buffs_tab = self._buffs_tab()
        calculators_tab = self._calculators_tab()
        optimizer_tab = self._optimizer_tab()
        profile_builder_tab = self._profile_builder_tab()
        lac_editor_tab = self._lac_editor_tab()
        self.profile_job_combo.currentTextChanged.connect(self._profile_job_for_editor_changed)
        aspirational_tab = self._aspirational_tab()
        self.tabs.addTab(self._gear_workspace_tab(), "Gear Workspace")
        self.tabs.addTab(profile_builder_tab, "Profile Builder")
        self.tabs.addTab(lac_editor_tab, "LAC Editor")
        self.tabs.addTab(optimizer_tab, "Optimizer")
        self.tabs.addTab(self._results_tab(), "Results")
        self.tabs.addTab(aspirational_tab, "Aspirational")
        self.tabs.addTab(active_buffs_tab, "Active Buffs")
        self.tabs.addTab(calculators_tab, "Calculators")
        self.tabs.insertTab(1, self._build_dashboard_tab(), "Build Dashboard")
        tab_help = {
            "Gear Workspace": "Edit a single set or the TP → WS cycle, then launch reproducible simulations.",
            "Build Dashboard": "Run the complete build workflow, apply scenario presets, check gear readiness, and manage favorites.",
            "Optimizer": "Search selected candidates for damage, defense, WS ranking, or tradeoffs.",
            "Profile Builder": "Turn optimized results into reviewed LuAshitacast profile sets.",
            "LAC Editor": "View and safely edit the selected character's current LuAshitacast Lua file.",
            "Results": "Browse, pin, rerun, compare, and export completed simulations.",
            "Aspirational": "Review modeled gear you do not currently own; never published automatically.",
            "Active Buffs": "Configure active songs, rolls, GEO, food, debuffs, and test scenarios.",
            "Calculators": "Job abilities, magic damage, self-buff casting sets, and enfeebling checks.",
        }
        for index in range(self.tabs.count()):
            label = self.tabs.tabText(index)
            self.tabs.setTabToolTip(index, tab_help.get(label, label))
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setUsesScrollButtons(True)
        self.tabs.tabBar().setExpanding(False)
        layout.addWidget(self.tabs, 1)
        return container

    def _select_tab(self, name: str) -> bool:
        """Select a workspace tab by its stable display name."""
        wanted = str(name or "").strip().casefold()
        wanted = {"ja": "active buffs", "job ability": "active buffs", "job abilities": "active buffs"}.get(wanted, wanted)
        for index in range(self.tabs.count()):
            tab_label = self.tabs.tabText(index).removesuffix(" *").casefold()
            if tab_label == wanted:
                self.tabs.setCurrentIndex(index)
                return True
            page = self.tabs.widget(index)
            for nested in page.findChildren(QTabWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
                for child_index in range(nested.count()):
                    if nested.tabText(child_index).casefold() == wanted:
                        self.tabs.setCurrentIndex(index)
                        nested.setCurrentIndex(child_index)
                        return True
        return False

    def _current_tab_name(self) -> str:
        index = self.tabs.currentIndex()
        if index < 0:
            return ""
        page = self.tabs.widget(index)
        for nested in page.findChildren(QTabWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            child_index = nested.currentIndex()
            if child_index >= 0:
                return nested.tabText(child_index)
        return self.tabs.tabText(index)

    def _calculators_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        note = QLabel(
            "Supporting calculators are grouped here so the main workflow stays focused on gear, optimization, profile building, and Results."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.calculator_tabs = QTabWidget()
        self.calculator_tabs.addTab(self._magic_damage_tab(), "Magic Damage")
        self.calculator_tabs.addTab(self._self_buffs_tab(), "Self Buffs")
        self.calculator_tabs.addTab(self._enfeebling_magic_tab(), "Enfeebling Magic")
        calculator_help = {
            "Magic Damage": "Evaluate modeled elemental, ninjutsu, ranged, and Quick Draw actions.",
            "Self Buffs": "Build casting sets for enhancing magic, GEO, BRD songs, and COR rolls.",
            "Enfeebling Magic": "Review enfeebling magic accuracy and resistance estimates.",
        }
        for index in range(self.calculator_tabs.count()):
            self.calculator_tabs.setTabToolTip(index, calculator_help[self.calculator_tabs.tabText(index)])
        layout.addWidget(self.calculator_tabs, 1)
        return tab

    def _build_dashboard_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        heading = QLabel("Build LuAshitacast starting sets")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        intro = QLabel(
            "Create practical owned-gear starting sets first. Combat simulation is an optional second pass, "
            "and publishing always opens an exact diff for review."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        context_box = QGroupBox("1  Source profile")
        context_layout = QGridLayout(context_box)
        self.dashboard_character_value = QLabel("No character selected")
        self.dashboard_character_value.setObjectName("dashboardValue")
        self.dashboard_profile_value = QLabel("No LAC profile loaded")
        self.dashboard_profile_value.setObjectName("dashboardValue")
        self.dashboard_source_value = QLabel("Accessible + Porter gear")
        self.dashboard_source_value.setObjectName("dashboardValue")
        refresh = QPushButton("Refresh inventory and profiles")
        refresh.clicked.connect(self.refresh_bridge)
        configure = QPushButton("Configure build")
        configure.clicked.connect(lambda: self._select_tab("Profile Builder"))
        context_layout.addWidget(QLabel("Character"), 0, 0)
        context_layout.addWidget(self.dashboard_character_value, 0, 1)
        context_layout.addWidget(QLabel("Profile"), 1, 0)
        context_layout.addWidget(self.dashboard_profile_value, 1, 1)
        context_layout.addWidget(QLabel("Gear sources"), 2, 0)
        context_layout.addWidget(self.dashboard_source_value, 2, 1)
        context_layout.addWidget(refresh, 0, 2)
        context_layout.addWidget(configure, 1, 2)
        context_layout.setColumnStretch(1, 1)
        layout.addWidget(context_box)

        build_box = QGroupBox("2  Generate and improve")
        build_layout = QVBoxLayout(build_box)
        build_layout.setContentsMargins(8, 10, 8, 8)
        build_layout.setSpacing(7)
        steps = QGridLayout()
        self.dashboard_source_step = QLabel("1  Check source")
        self.dashboard_base_step = QLabel("2  Create starting sets")
        self.dashboard_combat_step = QLabel("3  Improve combat sets (optional)")
        for column, label in enumerate((
            self.dashboard_source_step,
            self.dashboard_base_step,
            self.dashboard_combat_step,
        )):
            label.setObjectName("workflowStep")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            steps.addWidget(label, 0, column)
            steps.setColumnStretch(column, 1)
        build_layout.addLayout(steps)
        build_actions = QHBoxLayout()
        self.dashboard_search_quality = QComboBox()
        self.dashboard_search_quality.addItems(SEARCH_QUALITY_NAMES)
        self.dashboard_search_quality.setCurrentText(
            self.profile_builder_depth.currentText() if hasattr(self, "profile_builder_depth") else "Fast"
        )
        self.dashboard_search_quality.setToolTip(
            "Fast 6x4 · Standard 10x10 · Deep 12x10 without shared starting knowledge."
        )
        self.dashboard_search_quality.currentTextChanged.connect(
            self._set_profile_builder_depth_from_dashboard
        )
        if hasattr(self, "profile_builder_depth"):
            self.profile_builder_depth.currentTextChanged.connect(
                lambda quality: self.dashboard_search_quality.setCurrentText(quality)
            )
        self.dashboard_build_button = QPushButton("Create starting sets")
        self.dashboard_build_button.setObjectName("primaryAction")
        self.dashboard_build_button.setToolTip(
            "Create deterministic owned-gear sets without changing the LAC file."
        )
        self.dashboard_build_button.clicked.connect(self.build_everything)
        self.dashboard_optimize_button = QPushButton("Improve combat sets")
        self.dashboard_optimize_button.setEnabled(False)
        self.dashboard_optimize_button.clicked.connect(self.optimize_all_profile_builder_sections)
        self.dashboard_review_button = QPushButton("Review sets and publish")
        self.dashboard_review_button.setEnabled(False)
        self.dashboard_review_button.clicked.connect(lambda: self._select_tab("Profile Builder"))
        build_actions.addWidget(QLabel("Search quality"))
        build_actions.addWidget(self.dashboard_search_quality)
        build_actions.addWidget(self.dashboard_build_button)
        build_actions.addWidget(self.dashboard_optimize_button)
        build_actions.addWidget(self.dashboard_review_button)
        build_actions.addStretch(1)
        build_layout.addLayout(build_actions)
        self.dashboard_progress = QProgressBar()
        self.dashboard_progress.setTextVisible(True)
        self.dashboard_progress.setFormat("No catalog generated")
        build_layout.addWidget(self.dashboard_progress)
        self.dashboard_build_status = QLabel("No build has been started.")
        self.dashboard_build_status.setWordWrap(True)
        self.dashboard_build_status.setObjectName("dashboardStatus")
        build_layout.addWidget(self.dashboard_build_status)
        layout.addWidget(build_box)

        readiness_box = QGroupBox("Readiness details")
        readiness_layout = QVBoxLayout(readiness_box)
        readiness_buttons = QHBoxLayout()
        check_readiness = QPushButton("Refresh readiness")
        check_readiness.clicked.connect(self.refresh_gear_readiness)
        readiness_buttons.addWidget(check_readiness)
        readiness_buttons.addStretch(1)
        readiness_layout.addLayout(readiness_buttons)
        self.dashboard_readiness = QLabel("Readiness findings will appear here.")
        self.dashboard_readiness.setObjectName("readinessDetails")
        self.dashboard_readiness.setWordWrap(True)
        self.dashboard_readiness.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        readiness_layout.addWidget(self.dashboard_readiness)
        layout.addWidget(readiness_box)

        ws_box = QGroupBox("3  Weapon Skill ranking and shared sets")
        ws_layout = QVBoxLayout(ws_box)
        ws_layout.setContentsMargins(8, 10, 8, 8)
        ws_layout.setSpacing(5)
        ws_note = QLabel(
            "Optimize every WS in one weapon family against the Default enemy (Apex Toad). "
            "Sets within two armor-slot changes are grouped; the largest compatible group inherits Ws_Default."
        )
        ws_note.setWordWrap(True)
        ws_layout.addWidget(ws_note)
        ws_controls = QHBoxLayout()
        ws_controls.addWidget(QLabel("Fixed weapon layer"))
        self.dashboard_ws_overlay = QComboBox()
        self.dashboard_ws_overlay.setMinimumWidth(240)
        self.dashboard_ws_run_button = QPushButton("Rank WS and consolidate catalog")
        self.dashboard_ws_run_button.setEnabled(False)
        self.dashboard_ws_run_button.clicked.connect(self.run_dashboard_ws_ranking)
        ws_controls.addWidget(self.dashboard_ws_overlay, 1)
        ws_controls.addWidget(self.dashboard_ws_run_button)
        ws_layout.addLayout(ws_controls)
        self.dashboard_ws_status = QLabel("Create starting sets before running a WS ranking.")
        self.dashboard_ws_status.setObjectName("dashboardStatus")
        self.dashboard_ws_status.setWordWrap(True)
        ws_layout.addWidget(self.dashboard_ws_status)
        layout.addWidget(ws_box)
        layout.addStretch(1)
        self.refresh_gear_readiness()
        self._refresh_build_dashboard()
        return tab

    def _set_profile_builder_depth_from_dashboard(self, quality: str):
        if hasattr(self, "profile_builder_depth"):
            self.profile_builder_depth.setCurrentText(_normalized_search_quality(quality))

    def build_everything(self):
        self.dashboard_build_status.setText("Creating owned-gear starting sets…")
        self.build_complete_lac_profile()
        self._refresh_build_dashboard()

    def _refresh_build_dashboard(self):
        if not hasattr(self, "dashboard_build_status"):
            return
        profile = self._profile_for_job() if hasattr(self, "profile_job_combo") else None
        character = self.character_combo.currentText().strip() if hasattr(self, "character_combo") else ""
        self.dashboard_character_value.setText(character or "No character selected")
        if profile:
            job = str(profile.get("job") or "").upper()
            try:
                profile_name = self.bridge_store.profile_path(job).name
            except (OSError, ValueError, KeyError):
                profile_name = f"{job}.lua" if job else "LAC profile"
            self.dashboard_profile_value.setText(f"{job} · {profile_name}")
        else:
            self.dashboard_profile_value.setText("No LAC profile loaded")
        if hasattr(self, "profile_source_accessible"):
            source_names = []
            if self.profile_source_accessible.isChecked():
                source_names.append("accessible")
            if self.profile_source_porter.isChecked():
                source_names.append("Porter")
            if self.profile_source_transferable.isChecked():
                source_names.append("transferable")
            self.dashboard_source_value.setText(" + ".join(source_names) if source_names else "No gear sources selected")

        source_ready = bool(profile and self.bridge_store.data)
        self._set_workflow_step(self.dashboard_source_step, "complete" if source_ready else "blocked")
        self.dashboard_build_button.setEnabled(source_ready)
        build = getattr(self, "_profile_builder_result", None) or {}
        self._refresh_dashboard_ws_options(build)
        if not build:
            self.dashboard_build_status.setText("No build has been started.")
            self.dashboard_progress.setRange(0, 1)
            self.dashboard_progress.setValue(0)
            self.dashboard_progress.setFormat("Ready to generate" if source_ready else "Source profile required")
            self._set_workflow_step(self.dashboard_base_step, "current" if source_ready else "pending")
            self._set_workflow_step(self.dashboard_combat_step, "pending")
            self.dashboard_optimize_button.setEnabled(False)
            self.dashboard_review_button.setEnabled(False)
            if hasattr(self, "profile_optimize_all_button"):
                self.profile_optimize_all_button.setEnabled(False)
                self.profile_publish_button.setEnabled(False)
            return
        details = build.get("recipe_details") or {}
        combat = sum(1 for value in details.values() if value.get("optimizer"))
        direct = max(0, len(details) - combat)
        if build.get("settings_stale"):
            self.dashboard_progress.setRange(0, 1)
            self.dashboard_progress.setValue(0)
            self.dashboard_progress.setFormat("Settings changed · rebuild starting sets")
            self._set_workflow_step(self.dashboard_base_step, "current")
            self._set_workflow_step(self.dashboard_combat_step, "pending")
            self.dashboard_optimize_button.setEnabled(False)
            self.dashboard_review_button.setEnabled(True)
            self.profile_optimize_all_button.setEnabled(False)
            self.profile_publish_button.setEnabled(False)
            self.dashboard_build_status.setText(
                f"The preview contains {len(build.get('sets') or {})} sets from the previous settings. "
                "Create the starting sets again before improving or publishing."
            )
            return
        completed = sum(
            1 for value in details.values()
            if value.get("optimizer") and value.get("optimization_state") == "optimized"
        )
        batch_state = str(getattr(self, "_profile_builder_optimizer_batch_state", "ready") or "ready")
        running = batch_state == "running"
        self.dashboard_progress.setRange(0, max(1, combat + 1))
        self.dashboard_progress.setValue(min(combat + 1, completed + 1))
        self.dashboard_progress.setFormat(
            f"Starting sets ready · {completed}/{combat} combat sets improved"
        )
        self._set_workflow_step(self.dashboard_base_step, "complete")
        self._set_workflow_step(
            self.dashboard_combat_step,
            "complete" if combat == 0 or completed >= combat else "current" if running else "optional",
        )
        self.dashboard_optimize_button.setEnabled(bool(combat and completed < combat and not running))
        self.dashboard_review_button.setEnabled(True)
        self.profile_optimize_all_button.setEnabled(bool(combat and completed < combat and not running))
        self.profile_publish_button.setEnabled(not bool(build.get("published")))
        self.dashboard_build_status.setText(
            f"{len(build.get('sets') or {})} starting sets: {combat} combat and {direct} utility/defense. "
            f"{completed}/{combat} combat sets improved; {len(build.get('warnings') or [])} warning(s). "
            f"{batch_state}."
        )

    def _refresh_dashboard_ws_options(self, build: dict):
        if not hasattr(self, "dashboard_ws_overlay"):
            return
        current = self.dashboard_ws_overlay.currentData()
        self.dashboard_ws_overlay.blockSignals(True)
        self.dashboard_ws_overlay.clear()
        for overlay in build.get("overlay_items") or ():
            overlay_items = overlay.get("gearset") or {}
            skills = [
                str(overlay_items.get(slot, {}).get("Skill Type") or "None")
                for slot in ("main", "ranged")
            ]
            skill = next((value for value in skills if value in WS_BY_SKILL), "")
            if not skill:
                continue
            name = str(overlay.get("name") or "Weapon layer")
            self.dashboard_ws_overlay.addItem(f"{name} · {skill}", name)
            index = self.dashboard_ws_overlay.count() - 1
            self.dashboard_ws_overlay.setItemData(index, skill, Qt.ItemDataRole.UserRole + 1)
        if current:
            index = self.dashboard_ws_overlay.findData(current)
            if index >= 0:
                self.dashboard_ws_overlay.setCurrentIndex(index)
        self.dashboard_ws_overlay.blockSignals(False)
        running = bool(self.optimizer_thread and self.optimizer_thread.isRunning())
        ready = bool(build.get("sets") and self.dashboard_ws_overlay.count() and not build.get("settings_stale"))
        self.dashboard_ws_run_button.setEnabled(ready and not running)
        if not build.get("sets"):
            self.dashboard_ws_status.setText("Create starting sets before running a WS ranking.")
        elif not self.dashboard_ws_overlay.count():
            self.dashboard_ws_status.setText("No modeled fixed weapon layer can run a WS ranking.")

    def run_dashboard_ws_ranking(self):
        """Launch the existing rank optimizer with Profile Builder defaults."""
        if self.optimizer_thread and self.optimizer_thread.isRunning():
            QMessageBox.information(self, "Weapon Skill ranking", "Wait for the current optimizer run to finish.")
            return
        build = getattr(self, "_profile_builder_result", None) or {}
        overlay_name = str(self.dashboard_ws_overlay.currentData() or "")
        overlay = next(
            (value for value in build.get("overlay_items") or ()
             if str(value.get("name") or "") == overlay_name),
            None,
        )
        if not build or overlay is None:
            QMessageBox.information(self, "Weapon Skill ranking", "Create starting sets and choose a weapon layer first.")
            return
        overlay_items = overlay.get("gearset") or {}
        skill = str(self.dashboard_ws_overlay.currentData(Qt.ItemDataRole.UserRole + 1) or "")
        if skill not in WS_BY_SKILL:
            QMessageBox.warning(self, "Weapon Skill ranking", "The selected weapon layer has no modeled WS family.")
            return
        job_code = str(build.get("job") or "").casefold()
        job_name = next((name for name, code in JOBS.items() if code == job_code), "")
        if job_name:
            self.main_job.setCurrentText(job_name)
        preset = self._all_buff_presets().get(str(build.get("buff_preset") or ""))
        if preset:
            self._apply_buff_state(preset, preserve_job_abilities=True)
        default_enemy = str(optimizer_scenario("Tp_Default", self.profile_builder_tp.value())["enemy"])
        self.enemy_combo.setCurrentText(default_enemy)
        self._load_enemy(default_enemy)
        self.pdt.setValue(0)
        self.mdt.setValue(0)
        self.dt.setValue(0)
        self.tp_value.setValue(self.profile_builder_tp.value())
        base = dict((build.get("sets") or {}).get("Ws_Default") or {})
        if not base:
            first_default = next((
                set_name for set_name, details in (build.get("recipe_details") or {}).items()
                if details.get("section_type") == "Weapon skill"
                and details.get("variant") == "Default"
            ), "")
            base = dict((build.get("sets") or {}).get(first_default) or {})
        combined = {slot: base.get(slot, gear.Empty) for slot in SLOTS}
        specified = set(overlay.get("specified_slots") or ())
        combined.update({
            slot: overlay_items[slot] for slot in WEAPON_SLOTS
            if slot in specified and slot in overlay_items
        })
        self.quick_set.set_gearset(combined)
        self.select_all_candidates()
        self.optimize_action.setCurrentText("Rank weapon-type WS")
        self._refresh_ranking_weapon_types()
        self.ranking_weapon_type.setCurrentText(skill)
        self.seed.setText(str(build.get("seed") or ""))
        self._dashboard_ws_ranking_active = True
        self.dashboard_ws_run_button.setEnabled(False)
        self.dashboard_ws_status.setText(
            f"Ranking {len(WS_BY_SKILL[skill])} {skill} weapon skills against {default_enemy}…"
        )
        self.run_optimizer()

    def _apply_dashboard_ws_ranking(self, result: dict):
        """Apply one ranked TP tier and collapse its largest similar group."""
        build = getattr(self, "_profile_builder_result", None) or {}
        rankings = result.get("rankings") or {}
        if not build or not rankings:
            self.dashboard_ws_status.setText("WS ranking completed without usable optimized sets.")
            return
        target_tp = int(self.profile_builder_tp.value())
        tp_key = min(rankings, key=lambda value: abs(int(value) - target_tp))
        ws_sets = {}
        for entry in rankings.get(tp_key) or ():
            player = entry.get("player") if isinstance(entry, dict) else None
            gearset = getattr(player, "gearset", None)
            ws_name = str(entry.get("ws_name") or "") if isinstance(entry, dict) else ""
            if ws_name and isinstance(gearset, dict):
                ws_sets[ws_name] = {
                    slot: gearset.get(slot, gear.Empty) for slot in SET_SLOTS
                }
        groups = group_similar_ws_sets(ws_sets, max_slot_differences=2)
        if not groups:
            self.dashboard_ws_status.setText("WS ranking completed, but no legal gearsets were returned.")
            return
        details_by_name = build.get("recipe_details") or {}
        profile_names = {str(payload.get("name") or "") for payload in self._profile_payloads()}
        shared_supported = "Ws_Default" in profile_names
        largest = groups[0]
        if shared_supported:
            build["sets"]["Ws_Default"] = dict(largest["gearset"])
            details_by_name["Ws_Default"] = {
                "section_type": "Weapon skill",
                "family": "Shared WS",
                "variant": "Default",
                "optimization_state": "optimized",
                "objective": ("Ranked WS damage",),
                "ws_group": list(largest["members"]),
                "simulation_summary": (
                    f"Shared by {len(largest['members'])} similar WS at {int(tp_key):,} TP"
                ),
            }
        shared_members = set(largest["members"]) if shared_supported else set()
        for set_name, details in list(details_by_name.items()):
            optimizer = details.get("optimizer") or {}
            ws_name = str(optimizer.get("ws_name") or "")
            if details.get("section_type") != "Weapon skill" or details.get("variant") != "Default":
                continue
            if ws_name not in ws_sets:
                continue
            if ws_name in shared_members:
                build["sets"][set_name] = {slot: gear.Empty for slot in SET_SLOTS}
                details["inherits_from"] = "Ws_Default"
                details["simulation_summary"] = "Uses the shared Ws_Default ranking group"
            else:
                build["sets"][set_name] = dict(ws_sets[ws_name])
                details.pop("inherits_from", None)
            details["optimization_state"] = "optimized"
        build["ws_groups"] = [
            {"representative": group["representative"], "members": list(group["members"])}
            for group in groups
        ]
        build["published"] = False
        self._dashboard_ws_ranking_result = result
        self._populate_profile_builder_results(build)
        group_text = " · ".join(
            f"{group['representative']}: {len(group['members'])} WS" for group in groups
        )
        prefix = (
            f"Applied {len(ws_sets)} ranked WS at {int(tp_key):,} TP; "
            f"{len(largest['members'])} inherit Ws_Default. "
            if shared_supported else
            f"Ranked {len(ws_sets)} WS at {int(tp_key):,} TP; this profile has no Ws_Default inheritance point. "
        )
        self.dashboard_ws_status.setText(prefix + group_text)
        self._refresh_build_dashboard()

    @staticmethod
    def _set_workflow_step(label: QLabel, state: str):
        label.setProperty("state", state)
        label.style().unpolish(label)
        label.style().polish(label)

    def refresh_gear_readiness(self, *_args):
        if not hasattr(self, "dashboard_readiness"):
            return
        profile = self._profile_for_job() if hasattr(self, "profile_job_combo") else None
        if profile is None or not self.bridge_store.data:
            self.dashboard_readiness.setText(
                "No character/job profile is loaded. Select an Ashita folder and character first."
            )
            self._refresh_build_dashboard()
            return
        sources = self._profile_builder_sources()
        job = str(profile.get("job") or "").casefold()
        candidates = bridge_candidates(self.bridge_store, job, sources)
        missing_slots = [slot.upper() for slot, values in candidates.items() if len(values) <= 1]
        payloads = self._profile_payloads()
        unresolved = []
        incomplete = []
        pinned = []
        for payload in payloads:
            unresolved.extend(f"{payload['name']}: {value}" for value in payload.get("missing") or [])
            incomplete.extend(f"{payload['name']}: {value}" for value in payload.get("incomplete") or [])
            pinned.extend(f"{payload['name']}: {value}" for value in payload.get("unknown") or [])
        warning_items = [
            item_name(item) for item in self.bridge_store.catalog.values()
            if item.get("Model Warning") or item.get("Model Complete") is False
        ]
        aspirational = sorted(self.aspirational_selected)
        stale_results = sum(
            1 for record in self.result_history.list(self._history_character_key()) if record.get("stale")
        )
        modeled_slots = len(candidates) - len(missing_slots)
        incomplete_count = len(set(incomplete) | set(warning_items))
        issue_parts = []
        if missing_slots:
            issue_parts.append(f"No modeled owned item for {', '.join(missing_slots)}")
        if unresolved:
            issue_parts.append(f"{len(unresolved)} unresolved profile slot(s)")
        if incomplete_count:
            issue_parts.append(f"{incomplete_count} incomplete/model-warning item(s)")
        if pinned:
            issue_parts.append(f"{len(pinned)} specialty item(s) will remain pinned")
        detail = " · ".join(issue_parts) if issue_parts else "No blocking inventory or profile issues found."
        self.dashboard_readiness.setText(
            f"<b>{modeled_slots}/{len(candidates)} armor slots have modeled owned candidates.</b><br>"
            f"{escape(detail)}<br>"
            f"Aspirational items excluded from publish: {len(aspirational)} · stale saved results: {stale_results}"
        )
        self.dashboard_readiness.setToolTip("\n".join(unresolved[:20]))
        self._refresh_build_dashboard()

    def _favorite_weapon_character_key(self) -> str:
        return self._history_character_key()

    def _refresh_weapon_favorites(self):
        if not hasattr(self, "favorite_weapon_combo"):
            return
        character_favorites = self.favorite_weapon_setups.get(self._favorite_weapon_character_key(), {})
        if not isinstance(character_favorites, dict):
            character_favorites = {}
        current = self.favorite_weapon_combo.currentText()
        self.favorite_weapon_combo.blockSignals(True)
        self.favorite_weapon_combo.clear()
        self.favorite_weapon_combo.addItems(sorted(character_favorites, key=str.casefold))
        if current in character_favorites:
            self.favorite_weapon_combo.setCurrentText(current)
        self.favorite_weapon_combo.blockSignals(False)

    def _save_weapon_favorites(self):
        self.settings.setValue(
            "favorites/weapon_setups", json.dumps(_json_value(self.favorite_weapon_setups), ensure_ascii=False)
        )

    def save_favorite_weapon_setup(self):
        name = self.favorite_weapon_name.text().strip()
        if not name:
            QMessageBox.information(self, "Favorite weapon setup", "Enter a name first.")
            return
        character = self._favorite_weapon_character_key()
        self.favorite_weapon_setups.setdefault(character, {})[name] = {
            "quick": _gearset_payload(self.quick_set.items),
            "tp": _gearset_payload(self.tp_set.items),
            "ws": _gearset_payload(self.ws_set.items),
            "weapon_type": self.weapon_type_combo.currentText(),
            "ws_name": self.ws_combo.currentText(),
            "tp_value": self.tp_value.value(),
        }
        self._save_weapon_favorites()
        self._refresh_weapon_favorites()
        self.favorite_weapon_combo.setCurrentText(name)
        self.statusBar().showMessage(f"Saved favorite weapon setup: {name}", 5000)

    def load_favorite_weapon_setup(self):
        name = self.favorite_weapon_combo.currentText().strip()
        setup = self.favorite_weapon_setups.get(self._favorite_weapon_character_key(), {}).get(name)
        if not isinstance(setup, dict):
            return
        for key, editor in (("quick", self.quick_set), ("tp", self.tp_set), ("ws", self.ws_set)):
            saved = setup.get(key)
            if isinstance(saved, dict):
                editor.set_gearset({slot: self._resolve_saved_item(slot, saved.get(slot)) for slot in SLOTS})
        self._set_combo_value(self.weapon_type_combo, setup.get("weapon_type"), AUTO_WEAPON_TYPE)
        self._refresh_ws_choices()
        self._set_combo_value(self.ws_combo, setup.get("ws_name"), self.ws_combo.currentText())
        try:
            self.tp_value.setValue(int(setup.get("tp_value", self.tp_value.value())))
        except (TypeError, ValueError):
            pass
        self.workspace_mode.setCurrentText("TP → WS Cycle")
        self._select_tab("Gear Workspace")
        self.statusBar().showMessage(f"Loaded favorite weapon setup: {name}", 5000)

    def delete_favorite_weapon_setup(self):
        name = self.favorite_weapon_combo.currentText().strip()
        character = self._favorite_weapon_character_key()
        favorites = self.favorite_weapon_setups.get(character, {})
        if name not in favorites:
            return
        del favorites[name]
        self._save_weapon_favorites()
        self._refresh_weapon_favorites()
        self.statusBar().showMessage(f"Deleted favorite weapon setup: {name}", 4000)

    def _favorite_weapon_box(self) -> QGroupBox:
        """Keep reusable weapon setups beside the gear they affect."""
        box = QGroupBox("Saved weapon setups")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(6, 7, 6, 5)
        box_layout.setSpacing(0)
        self.favorite_weapon_name = QLineEdit()
        self.favorite_weapon_name.setPlaceholderText("Setup name")
        self.favorite_weapon_name.setMinimumWidth(150)
        self.favorite_weapon_name.setMaximumWidth(250)
        self.favorite_weapon_combo = QComboBox()
        self.favorite_weapon_combo.setMinimumWidth(150)
        self.favorite_weapon_combo.setMaximumWidth(250)
        save_weapon = QPushButton("Save")
        save_weapon.clicked.connect(self.save_favorite_weapon_setup)
        load_weapon = QPushButton("Load")
        load_weapon.clicked.connect(self.load_favorite_weapon_setup)
        delete_weapon = QPushButton("Delete")
        delete_weapon.clicked.connect(self.delete_favorite_weapon_setup)
        save_group = QWidget()
        save_row = QHBoxLayout(save_group)
        save_row.setContentsMargins(0, 0, 0, 0)
        save_row.setSpacing(4)
        save_row.addWidget(self.favorite_weapon_name)
        save_row.addWidget(save_weapon)
        save_row.addStretch(1)
        load_group = QWidget()
        load_row = QHBoxLayout(load_group)
        load_row.setContentsMargins(0, 0, 0, 0)
        load_row.setSpacing(4)
        load_row.addWidget(self.favorite_weapon_combo)
        load_row.addWidget(load_weapon)
        load_row.addWidget(delete_weapon)
        load_row.addStretch(1)
        box_layout.addWidget(
            ResponsiveControlStrip(save_group, load_group, stack_width=690)
        )
        self._refresh_weapon_favorites()
        return box

    def _gear_workspace_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        intro = QLabel(
            "Use one workspace for quick checks and complete TP → WS cycles. "
            "Long simulations are saved in Results and can be repeated exactly."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        controls = QGroupBox("Workspace controls")
        controls_box_layout = QVBoxLayout(controls)
        controls_box_layout.setContentsMargins(6, 7, 6, 5)
        controls_box_layout.setSpacing(0)
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(4)
        self.workspace_mode = QComboBox()
        self.workspace_mode.setMaximumWidth(190)
        self.workspace_mode.addItems(["Single Set", "TP → WS Cycle"])
        self.workspace_mode.setToolTip(
            "Single Set evaluates the current Quick Look gear. TP → WS Cycle keeps separate TP and WS armor sets."
        )
        self.workspace_seed = QLineEdit()
        self.workspace_seed.setPlaceholderText("Generated when a long run starts")
        self.workspace_seed.setMinimumWidth(135)
        self.workspace_seed.setMaximumWidth(220)
        self.workspace_seed.setToolTip(
            "A visible numeric seed makes two-hour DPS and WS distribution results exactly reproducible."
        )
        controls_layout.addWidget(QLabel("Mode"))
        controls_layout.addWidget(self.workspace_mode)
        self.workspace_seed.setVisible(False)
        controls_layout.addStretch(1)
        primary_group = QWidget()
        primary_group.setLayout(controls_layout)
        transfer_layout = QHBoxLayout()
        self.workspace_generated_label = QLabel("Generated set: none")
        self.workspace_generated_label.setObjectName("workspaceOrigin")
        self.workspace_update_generated_button = QPushButton("Update set")
        self.workspace_update_generated_button.setEnabled(False)
        self.workspace_update_generated_button.setToolTip(
            "Copy the current Single Set armor back to the generated catalog entry it came from."
        )
        self.workspace_update_generated_button.clicked.connect(self.update_generated_set_from_workspace)
        self.workspace_export_lac_button = QPushButton("Export set to LAC profile…")
        self.workspace_export_lac_button.setToolTip(
            "Review and write one workspace set to the selected character's LuAshitacast profile."
        )
        self.workspace_export_lac_button.clicked.connect(self.export_workspace_set_to_lac)
        self.workspace_export_lac_button.setText("Export to LAC...")
        transfer_layout.addWidget(self.workspace_generated_label, 1)
        transfer_layout.addWidget(self.workspace_update_generated_button)
        transfer_layout.addWidget(self.workspace_export_lac_button)
        transfer_layout.setContentsMargins(0, 0, 0, 0)
        transfer_layout.setSpacing(4)
        secondary_group = QWidget()
        secondary_group.setLayout(transfer_layout)
        controls_box_layout.addWidget(
            ResponsiveControlStrip(primary_group, secondary_group, stack_width=880)
        )
        layout.addWidget(controls)
        layout.addWidget(self._favorite_weapon_box())

        self.workspace_stack = QStackedWidget()
        self.workspace_stack.addWidget(self._quick_tab())
        self.workspace_stack.addWidget(self._cycle_workspace_tab())
        self.workspace_mode.currentIndexChanged.connect(self.workspace_stack.setCurrentIndex)
        layout.addWidget(self.workspace_stack, 1)
        return tab

    @staticmethod
    def _style_quick_chart_axis(axis):
        axis.set_facecolor("#17142e")
        axis.tick_params(colors="#c7c2d2", labelsize=8)
        axis.xaxis.label.set_color("#d0ccd6")
        axis.yaxis.label.set_color("#d0ccd6")
        axis.title.set_color("#fffaff")
        for spine in axis.spines.values():
            spine.set_color("#57536f")

    def _render_dps_comparison_graph(self, figure, canvas, summary: dict,
                                     current_name: str = "Current enemy",
                                     *, title: str = "Two-hour DPS comparison") -> bool:
        """Plot the active enemy and optional Profile Builder reference tiers."""
        if figure is None or canvas is None or not isinstance(summary, dict):
            return False
        primary = _dps_series_chart_data(summary)
        if primary is None:
            return False
        figure.clear()
        figure.set_facecolor("#17142e")
        axis = figure.add_subplot(111)
        self._style_quick_chart_axis(axis)
        colors = ("#fff0a8", "#72c7e8", "#d99bea", "#9de2a8")
        series = [(str(current_name or "Current enemy"), primary)]
        for name, reference in (summary.get("reference_summaries") or {}).items():
            chart = _dps_series_chart_data(reference)
            if chart is not None:
                series.append((str(name), chart))
        for index, (name, chart) in enumerate(series):
            times, values = chart["total"]
            axis.plot(
                times, values, color=colors[index % len(colors)],
                linewidth=2.4 if index == 0 else 1.5,
                label=f"{name} · {float(values[-1]):,.1f} DPS",
            )
        axis.set_xlim(0.0, 7200.0)
        axis.set_title(title, loc="left", fontsize=10, fontweight="bold", color="#fffaff")
        axis.set_xlabel("Elapsed time (seconds)", fontsize=8, color="#d0ccd6")
        axis.set_ylabel("DPS", fontsize=8, color="#d0ccd6")
        axis.grid(True, color="#302d4a", linewidth=0.8, alpha=0.8)
        handles, labels = axis.get_legend_handles_labels()
        if handles:
            figure.legend(
                handles, labels, loc="lower center", bbox_to_anchor=(0.5, 0.01),
                ncol=1, fontsize=6.5, facecolor="#17142e", edgecolor="#57536f",
                labelcolor="#fffaff", framealpha=0.96,
            )
        figure.subplots_adjust(left=0.13, right=0.97, bottom=0.31, top=0.84)
        canvas.draw_idle()
        return True

    def _render_quick_result_graph(self, action: str, output, dps_summary: dict | None = None):
        figure = getattr(self, "quick_result_figure", None)
        canvas = getattr(self, "quick_result_canvas", None)
        if figure is None or canvas is None:
            return

        figure.clear()
        figure.set_facecolor("#17142e")
        if action == "ws":
            axis = figure.add_subplot(111)
            axis.set_facecolor("#17142e")
            axis.text(
                0.5, 0.5, "Sampling 20,000 weapon skills...",
                color="#fff0a8", ha="center", va="center", fontsize=10,
                fontweight="bold", transform=axis.transAxes,
            )
            axis.set_axis_off()
            canvas.draw_idle()
            return
        chart = _quick_result_chart_data(
            action, output, tp_target=self.tp_value.value()
        )
        if chart is None:
            axis = figure.add_subplot(111)
            axis.set_facecolor("#17142e")
            axis.text(
                0.5, 0.5, "The evaluation graph will appear here.",
                color="#a9a4b5", ha="center", va="center", fontsize=9,
                transform=axis.transAxes,
            )
            axis.set_axis_off()
            figure.subplots_adjust(left=0.04, right=0.96, bottom=0.08, top=0.92)
            canvas.draw_idle()
            return

        if chart["kind"] == "tp_pace":
            if dps_summary is None:
                axis = figure.add_subplot(111)
                self._style_quick_chart_axis(axis)
                axis.text(
                    0.5, 0.5, "Run a 2-hour DPS comparison to populate this graph.",
                    color="#fff0a8", ha="center", va="center", fontsize=9,
                    fontweight="bold", transform=axis.transAxes,
                )
                axis.set_axis_off()
                canvas.draw_idle()
                return
            self._render_dps_comparison_graph(
                figure, canvas, dps_summary, self.enemy_combo.currentText(),
                title="Two-hour DPS comparison",
            )
            return
        else:
            axes = figure.subplots(1, 2)
            labels = (
                ("Average damage", chart["damage"], "#e6c983", "{:,.0f}"),
                ("TP return", chart["tp_return"], "#72c7e8", "{:,.1f}"),
            )
            for axis, (label, value, color, number_format) in zip(axes, labels):
                self._style_quick_chart_axis(axis)
                axis.barh([0], [value], height=0.42, color=color, alpha=0.92)
                axis.set_xlim(0.0, max(1.0, value * 1.18))
                axis.set_ylim(-0.7, 0.7)
                axis.set_yticks([])
                axis.set_title(
                    label, loc="left", fontsize=9, fontweight="bold", color="#fffaff"
                )
                axis.grid(True, axis="x", color="#302d4a", linewidth=0.8, alpha=0.8)
                axis.text(
                    value, 0, number_format.format(value), color="#fffaff",
                    ha="right" if value else "left", va="center", fontsize=10,
                    fontweight="bold",
                )
                axis.spines["left"].set_visible(False)
                axis.spines["right"].set_visible(False)
                axis.spines["top"].set_visible(False)
            title = "Weapon skill result" if chart["action"] == "ws" else "Spell result"
            figure.suptitle(title, color="#fffaff", fontsize=10, fontweight="bold", x=0.08, ha="left")
            figure.subplots_adjust(left=0.08, right=0.96, bottom=0.22, top=0.72, wspace=0.28)
        canvas.draw_idle()

    def _render_ws_distribution_graph(self, figure, canvas, distribution: dict,
                                      *, ws_name: str = "Weapon skill") -> bool:
        chart = _ws_distribution_chart_data(distribution)
        if figure is None or canvas is None or chart is None:
            return False
        figure.clear()
        figure.set_facecolor("#17142e")
        axis = figure.add_subplot(111)
        self._style_quick_chart_axis(axis)
        edges = chart["edges"]
        counts = chart["counts"]
        widths = np.diff(edges)
        density = counts / max(1.0, float(np.sum(counts)))
        axis.bar(
            edges[:-1], density, width=widths, align="edge",
            color="#72c7e8", edgecolor="#9de2fa", linewidth=0.35, alpha=0.82,
        )
        axis.axvline(
            chart["mean"], color="#fff0a8", linewidth=2,
            label=f"Mean {chart['mean']:,.0f}",
        )
        reference_colors = ("#72c7e8", "#d99bea", "#9de2a8")
        for index, (name, reference) in enumerate(
            (distribution.get("reference_distributions") or {}).items()
        ):
            reference_chart = _ws_distribution_chart_data(reference)
            if reference_chart is None:
                continue
            axis.axvline(
                reference_chart["mean"], color=reference_colors[index % len(reference_colors)],
                linewidth=1.4, linestyle="--",
                label=f"{name} mean {reference_chart['mean']:,.0f}",
            )
        axis.axvspan(chart["p05"], chart["p95"], color="#e6c983", alpha=0.13,
                    label=f"90% range {chart['p05']:,.0f}-{chart['p95']:,.0f}")
        axis.set_title(f"{ws_name} - {chart['samples']:,}-sample damage distribution",
                       loc="left", fontsize=9, fontweight="bold", color="#fffaff", pad=9)
        axis.set_xlabel("Weapon-skill damage", fontsize=8, color="#d0ccd6")
        axis.set_ylabel("Sample share", fontsize=8, color="#d0ccd6")
        axis.grid(True, axis="y", color="#302d4a", linewidth=0.8, alpha=0.8)
        handles, labels = axis.get_legend_handles_labels()
        if handles:
            figure.legend(
                handles, labels, loc="lower center", bbox_to_anchor=(0.5, 0.01),
                ncol=1, fontsize=6.2, facecolor="#17142e", edgecolor="#57536f",
                labelcolor="#fffaff", framealpha=0.96,
            )
        figure.subplots_adjust(left=0.13, right=0.97, bottom=0.35, top=0.84)
        canvas.draw_idle()
        return True

    def _render_cycle_dps_graph(self, summary: dict) -> bool:
        figure = getattr(self, "cycle_result_figure", None)
        canvas = getattr(self, "cycle_result_canvas", None)
        chart = _dps_series_chart_data(summary)
        if figure is None or canvas is None or chart is None:
            return False
        return self._render_dps_comparison_graph(
            figure, canvas, summary, self.enemy_combo.currentText(),
            title="TP → WS two-hour DPS comparison",
        )

    def _quick_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        quick_splitter = QSplitter(Qt.Orientation.Horizontal)
        quick_splitter.setObjectName("quickSplitter")
        quick_splitter.setOpaqueResize(False)
        left = QWidget()
        left.setMinimumWidth(310)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        left_layout.addWidget(self.quick_set)

        actions_box = QGroupBox("Actions")
        actions_layout = QGridLayout(actions_box)
        actions_layout.setContentsMargins(6, 9, 6, 6)
        actions_layout.setHorizontalSpacing(4)
        actions_layout.setVerticalSpacing(4)
        definitions = (
            ("Evaluate WS", lambda: self.evaluate("ws")),
            ("Evaluate TP round", lambda: self.evaluate("attack")),
            ("Evaluate spell", lambda: self.evaluate("spell")),
            ("Copy to TP set", lambda: self.tp_set.set_gearset(self.quick_set.items)),
            ("Copy to WS set", lambda: self.ws_set.set_gearset(self.quick_set.items)),
        )
        for index, (label, callback) in enumerate(definitions):
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions_layout.addWidget(button, index // 2, index % 2)
        save_quick = QPushButton("Save Quick Look result")
        save_quick.setToolTip("Quick evaluations stay out of history unless you explicitly save them.")
        save_quick.clicked.connect(self.save_quick_result)
        actions_layout.addWidget(save_quick, 2, 1)
        actions_layout.setColumnStretch(0, 1)
        actions_layout.setColumnStretch(1, 1)
        left_layout.addWidget(actions_box)
        result_panel = QFrame()
        result_panel.setObjectName("quickResultPanel")
        result_layout = QVBoxLayout(result_panel)
        result_layout.setContentsMargins(9, 7, 7, 7)
        result_layout.setSpacing(3)
        self.result_label = QLabel("Select equipment, then evaluate an action.")
        self.result_label.setObjectName("quickResult")
        self.result_label.setWordWrap(True)
        self.result_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.result_label.setMinimumHeight(38)
        self.result_label.setMaximumHeight(64)
        result_layout.addWidget(self.result_label)
        self.quick_reference_checkbox = QCheckBox("Reference enemies")
        self.quick_reference_checkbox.setObjectName("referenceEnemyToggle")
        self.quick_reference_checkbox.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        self.quick_reference_checkbox.setChecked(True)
        self.quick_reference_checkbox.setToolTip(
            "Include Apex Toad, Apex Knight Lugcrawler, and Apex Archaic Cogs in DPS and WS graphs."
        )
        self.quick_reference_checkbox.toggled.connect(self._quick_reference_toggle)
        result_layout.addWidget(self.quick_reference_checkbox)
        self.quick_result_figure = None
        self.quick_result_canvas = None
        if FigureCanvas is not None and Figure is not None:
            self.quick_result_figure = Figure(figsize=(4.4, 2.35), dpi=100)
            self.quick_result_canvas = FigureCanvas(self.quick_result_figure)
            self.quick_result_canvas.setObjectName("quickResultGraph")
            self.quick_result_canvas.setAccessibleName("Gear evaluation result graph")
            self.quick_result_canvas.setMinimumHeight(190)
            self.quick_result_canvas.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            result_layout.addWidget(self.quick_result_canvas, 1)
            self._render_quick_result_graph("", None)
        else:
            graph_unavailable = QLabel("Graph unavailable: Matplotlib Qt support is not installed.")
            graph_unavailable.setObjectName("quickResultGraphFallback")
            graph_unavailable.setWordWrap(True)
            graph_unavailable.setAlignment(Qt.AlignmentFlag.AlignCenter)
            result_layout.addWidget(graph_unavailable, 1)
        left_layout.addWidget(result_panel, 1)
        left_scroll = QScrollArea()
        left_scroll.setObjectName("quickEquipmentScroll")
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        left_scroll.setMinimumWidth(330)
        left_scroll.setMaximumWidth(350)
        left_scroll.setWidget(left)
        quick_splitter.addWidget(left_scroll)

        totals = QGroupBox("Live totals")
        totals.setMinimumWidth(390)
        totals_layout = QVBoxLayout(totals)
        totals_layout.setContentsMargins(7, 9, 7, 7)
        totals_layout.setSpacing(4)
        totals_legend = QLabel(
            "Uses selected gear and Buffs settings. "
            "<span style='color:#ffe2a8'><b>Amber</b></span> under · "
            "<span style='color:#b9f6cb'><b>Green</b></span> at · "
            "<span style='color:#ffc4c1'><b>Rose</b></span> over"
        )
        totals_legend.setWordWrap(True)
        totals_legend.setMinimumWidth(0)
        refresh_totals = QPushButton("Refresh totals")
        refresh_totals.clicked.connect(self.refresh_quick_stats)
        totals_header = ResponsiveTotalsHeader(totals_legend, refresh_totals)
        totals_layout.addWidget(totals_header)
        self.quick_stats_scroll = QScrollArea()
        self.quick_stats_scroll.setWidgetResizable(True)
        self.quick_stats_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.quick_stats_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.quick_stats_widget = QWidget()
        self.quick_stats_widget.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.quick_stats_layout = QGridLayout(self.quick_stats_widget)
        self.quick_stats_layout.setContentsMargins(0, 0, 0, 0)
        self.quick_stats_scroll.setWidget(self.quick_stats_widget)
        self.quick_stats_scroll.setMinimumHeight(220)
        totals_layout.addWidget(self.quick_stats_scroll)
        quick_splitter.addWidget(totals)
        quick_splitter.setStretchFactor(0, 0)
        quick_splitter.setStretchFactor(1, 1)
        quick_splitter.setSizes([330, 570])
        layout.addWidget(quick_splitter, 1)
        return tab

    def _cycle_workspace_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        heading = QLabel("TP → WS cycle workspace")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        note = QLabel(
            "Build the repeating cycle from two explicit sets. TP controls the melee phase; "
            "WS controls the selected weapon skill and its damage phase."
        )
        note.setObjectName("cycleIntro")
        note.setWordWrap(True)
        layout.addWidget(note)
        sets = QSplitter(Qt.Orientation.Horizontal)
        sets.setObjectName("cycleSetSplitter")
        sets.setChildrenCollapsible(False)
        set_column = QWidget()
        set_column.setObjectName("cycleSetColumn")
        set_column.setMinimumWidth(205)
        set_column.setMaximumWidth(260)
        set_column_layout = QVBoxLayout(set_column)
        set_column_layout.setContentsMargins(0, 0, 0, 0)
        set_column_layout.setSpacing(6)
        tp_panel = QGroupBox("TP phase · melee set")
        tp_panel.setObjectName("cycleSetPanel")
        tp_layout = QVBoxLayout(tp_panel)
        tp_layout.setContentsMargins(5, 7, 5, 5)
        tp_layout.addWidget(self.tp_set, 0, Qt.AlignmentFlag.AlignHCenter)
        self.cycle_tp_summary = QLabel()
        self.cycle_tp_summary.setObjectName("cycleSetSummary")
        self.cycle_tp_summary.setWordWrap(True)
        tp_layout.addWidget(self.cycle_tp_summary)
        ws_panel = QGroupBox("WS phase · weapon-skill set")
        ws_panel.setObjectName("cycleSetPanel")
        ws_layout = QVBoxLayout(ws_panel)
        ws_layout.setContentsMargins(5, 7, 5, 5)
        ws_layout.addWidget(self.ws_set, 0, Qt.AlignmentFlag.AlignHCenter)
        self.cycle_ws_summary = QLabel()
        self.cycle_ws_summary.setObjectName("cycleSetSummary")
        self.cycle_ws_summary.setWordWrap(True)
        ws_layout.addWidget(self.cycle_ws_summary)
        tp_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        ws_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        set_column_layout.addWidget(tp_panel)
        set_column_layout.addWidget(ws_panel)
        set_column_layout.addStretch(1)
        sets.addWidget(set_column)
        sets.setStretchFactor(0, 0)
        right_column = QWidget()
        right_column.setObjectName("cycleResultColumn")
        right_column.setMinimumWidth(320)
        right_layout = QVBoxLayout(right_column)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        actions_box = QGroupBox("Cycle actions")
        actions_box.setObjectName("cycleActionPanel")
        controls = QGridLayout(actions_box)
        controls.setContentsMargins(7, 8, 7, 7)
        controls.setHorizontalSpacing(6)
        controls.setVerticalSpacing(5)
        self.plot_dps_checkbox = QCheckBox("Keep legacy plot option")
        self.plot_dps_checkbox.setToolTip(
            "The normal Results view embeds plots. This option is retained for compatibility with saved settings."
        )
        self.plot_dps_checkbox.setVisible(False)
        self.simulate_button = QPushButton("Run two-hour DPS simulation")
        self.simulate_button.setText("Run 2-hour DPS")
        self.simulate_button.setObjectName("cycleRunAction")
        self.simulate_button.setMinimumHeight(30)
        self.simulate_button.clicked.connect(self.run_simulation)
        self.cancel_simulation_button = QPushButton("Stop")
        self.cancel_simulation_button.setObjectName("cycleStopAction")
        self.cancel_simulation_button.setMinimumHeight(30)
        self.cancel_simulation_button.setEnabled(False)
        self.cancel_simulation_button.clicked.connect(self.stop_simulation)
        distribution = QPushButton("Generate WS damage graph")
        distribution.setText("WS damage graph")
        distribution.setObjectName("cycleSecondaryAction")
        distribution.setMinimumHeight(30)
        distribution.setToolTip("Sample the default 20,000 weapon skills and save the compact histogram to Results.")
        distribution.clicked.connect(self.plot_ws_distribution)
        copy_tp_ws = QPushButton("Copy TP → WS")
        copy_tp_ws.clicked.connect(lambda: self.ws_set.set_gearset(self.tp_set.items))
        swap = QPushButton("Swap TP / WS")
        swap.clicked.connect(self.swap_tp_ws_sets)
        copy_tp_ws.setObjectName("cycleSecondaryAction")
        swap.setObjectName("cycleSecondaryAction")
        controls.addWidget(self.simulate_button, 0, 0, 1, 2)
        controls.addWidget(self.cancel_simulation_button, 0, 2)
        controls.addWidget(distribution, 1, 0)
        controls.addWidget(copy_tp_ws, 1, 1)
        controls.addWidget(swap, 1, 2)
        for column in range(3):
            controls.setColumnStretch(column, 1)
        right_layout.addWidget(actions_box)
        status_box = QGroupBox("Cycle status")
        status_box.setObjectName("cycleStatusPanel")
        status_layout = QVBoxLayout(status_box)
        status_layout.setContentsMargins(7, 8, 7, 7)
        self.cycle_reference_checkbox = QCheckBox("Reference enemies")
        self.cycle_reference_checkbox.setObjectName("referenceEnemyToggle")
        self.cycle_reference_checkbox.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        self.cycle_reference_checkbox.setChecked(True)
        self.cycle_reference_checkbox.setToolTip(
            "Include the three Profile Builder reference enemies in the 2-hour and WS graphs."
        )
        status_layout.addWidget(self.cycle_reference_checkbox)
        self.plot_status = QLabel("Ready · choose a WS and run a reproducible cycle.")
        self.plot_status.setObjectName("cycleStatus")
        self.plot_status.setWordWrap(True)
        status_layout.addWidget(self.plot_status)
        right_layout.addWidget(status_box)
        self.cycle_result_figure = None
        self.cycle_result_canvas = None
        graph_panel = QFrame()
        graph_panel.setObjectName("cycleResultPanel")
        graph_panel.setMinimumWidth(0)
        graph_layout = QVBoxLayout(graph_panel)
        graph_layout.setContentsMargins(7, 7, 7, 7)
        graph_title = QLabel("Simulation graph")
        graph_title.setObjectName("sectionTitle")
        graph_layout.addWidget(graph_title)
        if FigureCanvas is not None and Figure is not None:
            self.cycle_result_figure = Figure(figsize=(8.0, 4.5), dpi=100)
            self.cycle_result_canvas = FigureCanvas(self.cycle_result_figure)
            self.cycle_result_canvas.setObjectName("cycleResultGraph")
            self.cycle_result_canvas.setAccessibleName("Two-hour DPS or weapon-skill distribution graph")
            self.cycle_result_canvas.setMinimumHeight(300)
            self.cycle_result_canvas.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            graph_layout.addWidget(self.cycle_result_canvas, 1)
            self._render_cycle_graph_placeholder()
        else:
            graph_unavailable = QLabel("Graph unavailable: Matplotlib Qt support is not installed.")
            graph_unavailable.setObjectName("quickResultGraphFallback")
            graph_unavailable.setAlignment(Qt.AlignmentFlag.AlignCenter)
            graph_layout.addWidget(graph_unavailable, 1)
        right_layout.addWidget(graph_panel, 1)
        sets.addWidget(right_column)
        sets.setStretchFactor(1, 1)
        sets.setSizes([225, 720])
        layout.addWidget(sets, 1)
        self.tp_set.changed.connect(self._refresh_cycle_set_summaries)
        self.ws_set.changed.connect(self._refresh_cycle_set_summaries)
        self.ws_combo.currentTextChanged.connect(self._refresh_cycle_set_summaries)
        self.tp_value.valueChanged.connect(self._refresh_cycle_set_summaries)
        self._refresh_cycle_set_summaries()
        scroll = QScrollArea()
        scroll.setObjectName("cycleWorkspaceScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(tab)
        return scroll

    def _render_cycle_graph_placeholder(self, message: str = "Run the two-hour cycle or sample 20,000 weapon skills."):
        figure = getattr(self, "cycle_result_figure", None)
        canvas = getattr(self, "cycle_result_canvas", None)
        if figure is None or canvas is None:
            return
        figure.clear()
        figure.set_facecolor("#17142e")
        axis = figure.add_subplot(111)
        axis.set_facecolor("#17142e")
        axis.text(0.5, 0.5, message, color="#a9a4b5", ha="center", va="center",
                  fontsize=9, transform=axis.transAxes)
        axis.set_axis_off()
        canvas.draw_idle()

    def _refresh_cycle_set_summaries(self, *_args):
        """Keep the TP/WS cards readable without opening each gear picker."""
        if not hasattr(self, "cycle_tp_summary"):
            return
        tp_main = item_name(self.tp_set.items.get("main", gear.Empty))
        tp_sub = item_name(self.tp_set.items.get("sub", gear.Empty))
        ws_main = item_name(self.ws_set.items.get("main", gear.Empty))
        ws_sub = item_name(self.ws_set.items.get("sub", gear.Empty))
        self.cycle_tp_summary.setText(
            f"Main: {tp_main}  ·  Sub: {tp_sub}  ·  Threshold: {self.tp_value.value():,} TP"
        )
        ws_name = self.ws_combo.currentText().strip() or "None"
        self.cycle_ws_summary.setText(
            f"Main: {ws_main}  ·  Sub: {ws_sub}  ·  WS: {ws_name}"
        )

    def _magic_damage_tab(self) -> QWidget:
        """Provide a formula-backed spell damage workspace separate from WS Quick Look."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        note = QLabel(
            "Select a modeled magic action and evaluate it with the current Quick Look equipment, enemy, "
            "Self Buffs, and JA settings. The spell type is explicit so it is not inferred from the current weapon."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        controls = QGroupBox("Magic action")
        form = QFormLayout(controls)
        self.magic_damage_type_combo = QComboBox()
        self.magic_damage_type_combo.addItems(MAGIC_DAMAGE_TYPES)
        self.magic_damage_type_combo.currentTextChanged.connect(self._refresh_magic_damage_spell_choices)
        self.magic_damage_spell_combo = QComboBox()
        self.magic_damage_spell_combo.setMinimumWidth(220)
        self.magic_damage_metric_combo = QComboBox()
        self.magic_damage_metric_combo.addItems(["Damage dealt", "TP return"])
        form.addRow("Formula", self.magic_damage_type_combo)
        form.addRow("Spell / action", self.magic_damage_spell_combo)
        form.addRow("Metric", self.magic_damage_metric_combo)
        evaluate_button = QPushButton("Calculate magic result")
        evaluate_button.clicked.connect(self.evaluate_magic_damage)
        form.addRow(evaluate_button)
        layout.addWidget(controls)

        self.magic_damage_result = QLabel("Choose a spell, then calculate.")
        self.magic_damage_result.setObjectName("sectionTitle")
        self.magic_damage_result.setWordWrap(True)
        layout.addWidget(self.magic_damage_result)
        self.magic_damage_breakdown = QPlainTextEdit()
        self.magic_damage_breakdown.setReadOnly(True)
        self.magic_damage_breakdown.setPlaceholderText("The selected player and enemy magic stats will appear here.")
        layout.addWidget(self.magic_damage_breakdown, 1)
        self._refresh_magic_damage_spell_choices()
        return tab

    def _self_buffs_tab(self) -> QWidget:
        """Build casting sets for self-applied enhancing, GEO, BRD, and COR magic."""
        tab = QWidget()
        outer = QVBoxLayout(tab)
        note = QLabel(
            "Build a casting set from the currently selected optimizer candidates. "
            "The result is armor-only so the current weapon setup stays intact; use Copy to Quick Look to inspect it."
        )
        note.setWordWrap(True)
        outer.addWidget(note)
        controls = QGroupBox("Casting-set recipe")
        form = QFormLayout(controls)
        self.self_buff_family_combo = QComboBox()
        self.self_buff_family_combo.addItems(tuple(SELF_BUFF_FAMILIES))
        self.self_buff_family_combo.currentTextChanged.connect(self._refresh_self_buff_variants)
        self.self_buff_variant_combo = QComboBox()
        self.self_buff_variant_combo.setMinimumWidth(240)
        self.self_buff_recipe_note = QLabel()
        self.self_buff_recipe_note.setWordWrap(True)
        self.self_buff_optimize_button = QPushButton("Optimize casting set")
        self.self_buff_optimize_button.clicked.connect(self.optimize_self_buff_set)
        form.addRow("Buff family", self.self_buff_family_combo)
        form.addRow("Spell / action", self.self_buff_variant_combo)
        form.addRow("Priorities", self.self_buff_recipe_note)
        form.addRow(self.self_buff_optimize_button)
        outer.addWidget(controls)
        self.self_buff_result = QLabel("Choose a buff family, then optimize its casting set.")
        self.self_buff_result.setObjectName("sectionTitle")
        self.self_buff_result.setWordWrap(True)
        outer.addWidget(self.self_buff_result)
        self.self_buff_copy_button = QPushButton("Copy armor set to Quick Look")
        self.self_buff_copy_button.setEnabled(False)
        self.self_buff_copy_button.clicked.connect(self.copy_self_buff_set_to_quick_look)
        outer.addWidget(self.self_buff_copy_button)
        self.self_buff_preview = QScrollArea()
        self.self_buff_preview.setWidgetResizable(True)
        self.self_buff_preview.setFrameShape(QFrame.Shape.NoFrame)
        self.self_buff_preview_widget = QWidget()
        self.self_buff_preview_layout = QGridLayout(self.self_buff_preview_widget)
        self.self_buff_preview_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.self_buff_preview.setWidget(self.self_buff_preview_widget)
        outer.addWidget(self.self_buff_preview, 1)
        self._refresh_self_buff_variants()
        return tab

    def _refresh_self_buff_variants(self, *_args):
        if not hasattr(self, "self_buff_variant_combo"):
            return
        family = SELF_BUFF_FAMILIES[self.self_buff_family_combo.currentText()]
        current = self.self_buff_variant_combo.currentText()
        self.self_buff_variant_combo.blockSignals(True)
        self.self_buff_variant_combo.clear()
        self.self_buff_variant_combo.addItems(family["variants"])
        if current in family["variants"]:
            self.self_buff_variant_combo.setCurrentText(current)
        elif family["variants"]:
            self.self_buff_variant_combo.setCurrentIndex(0)
        self.self_buff_variant_combo.blockSignals(False)
        self.self_buff_recipe_note.setText(str(family["note"]))

    def _self_buff_candidate_pool(self) -> dict[str, list[dict]]:
        pool = {}
        for slot in ARMOR_SLOTS:
            selected = self.candidates.get(slot, set())
            items = [
                item for item in self.optimizer_items_for_slot(slot)
                if item_name(item) in selected
            ]
            if not any(item_name(item) == "Empty" for item in items):
                items.insert(0, gear.Empty)
            pool[slot] = items
        return pool

    def optimize_self_buff_set(self):
        family_name = self.self_buff_family_combo.currentText()
        family = SELF_BUFF_FAMILIES[family_name]
        recipe = ProfileRecipe(
            f"{family_name} · {self.self_buff_variant_combo.currentText()}",
            tuple(family["objective"]),
            tuple(family["caps"]),
        )
        try:
            built = build_stat_set(
                recipe.name,
                self._self_buff_candidate_pool(),
                recipe,
                weapons={slot: self.quick_set.items[slot] for slot in WEAPON_SLOTS},
                buffs=self._combat_context()["buffs"],
                abilities=self._combat_context()["abilities"],
            )
            self._self_buff_result_gear = dict(built.equipment)
            self._clear_self_buff_preview()
            for index, slot in enumerate(ARMOR_SLOTS):
                row, column = divmod(index, 3)
                self.self_buff_preview_layout.addWidget(
                    self._profile_builder_gear_tile(slot, built.equipment.get(slot, gear.Empty)),
                    row, column,
                )
            self.self_buff_result.setText(
                f"{recipe.name}: optimized {len(ARMOR_SLOTS)} armor slots using {len(family['objective'])} priorities. "
                + ("; ".join(built.warnings) if built.warnings else "No recipe warnings.")
            )
            self.self_buff_copy_button.setEnabled(True)
        except Exception as error:
            QMessageBox.critical(self, "Self Buff set optimization failed", str(error))

    def _clear_self_buff_preview(self):
        while self.self_buff_preview_layout.count():
            item = self.self_buff_preview_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def copy_self_buff_set_to_quick_look(self):
        gearset = dict(self.quick_set.items)
        gearset.update(getattr(self, "_self_buff_result_gear", {}))
        self.quick_set.set_gearset(gearset)
        self.statusBar().showMessage("Self Buff casting armor copied to Quick Look; weapon slots were preserved.", 5000)

    def _enfeebling_magic_tab(self) -> QWidget:
        """Show the current enfeebling accuracy model without inventing debuff potency formulas."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        note = QLabel(
            "This view evaluates enfeebling magic accuracy against the selected enemy. "
            "Individual debuff duration, potency, and resistance exceptions are not currently modeled by the combat engine."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        controls = QGroupBox("Enfeebling action")
        form = QFormLayout(controls)
        self.enfeebling_spell_combo = QComboBox()
        self.enfeebling_spell_combo.addItems(ENFEEBLING_SPELLS)
        self.enfeebling_spell_combo.setMinimumWidth(220)
        form.addRow("Spell", self.enfeebling_spell_combo)
        calculate = QPushButton("Calculate enfeebling accuracy")
        calculate.clicked.connect(self.evaluate_enfeebling_magic)
        form.addRow(calculate)
        layout.addWidget(controls)
        self.enfeebling_result = QLabel("Choose an enfeebling spell, then calculate.")
        self.enfeebling_result.setObjectName("sectionTitle")
        self.enfeebling_result.setWordWrap(True)
        layout.addWidget(self.enfeebling_result)
        self.enfeebling_breakdown = QPlainTextEdit()
        self.enfeebling_breakdown.setReadOnly(True)
        self.enfeebling_breakdown.setPlaceholderText("Magic accuracy, skill, target MEVA, and resist coefficient will appear here.")
        layout.addWidget(self.enfeebling_breakdown, 1)
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
        self.quick_ability_status.setWordWrap(True)
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
        self.abilities_json.setMaximumWidth(620)
        self.abilities_json.setMaximumHeight(60)
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

    def _quick_stat_row(self, label: str, value: str, accent: str = "",
                        state: str = "neutral") -> QFrame:
        accent_colors = {
            "neutral": "#8cf3b2",
            "under": "#ffe2a8",
            "at": "#36bde8",
            "over": "#ffc4c1",
        }
        row = QFrame()
        row.setObjectName("quickStatRow")
        row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(5, 1, 5, 1)
        row_layout.setSpacing(4)
        name = QLabel(label)
        name.setObjectName("quickStatName")
        amount = QLabel(value)
        amount.setObjectName("quickStatValue")
        amount.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        amount.setMinimumWidth(58)
        row_layout.addWidget(name, 1)
        # Attribute rows are a compact base + equipment pair.  Render those
        # as one right-aligned numeric group so every total and delta shares a
        # stable right edge instead of leaving a floating accent column.
        if re.fullmatch(r"[+-][\d,.]+(?:%|s)?", accent or ""):
            amount.setText(
                f"{escape(str(value))} "
                f"<span style='color:{accent_colors.get(state, accent_colors['neutral'])}'>"
                f"{escape(accent)}</span>"
            )
            amount.setMinimumWidth(136)
            row_layout.addWidget(amount)
        else:
            bonus = QLabel(accent)
            bonus.setObjectName("quickStatAccent")
            bonus.setStyleSheet(f"color: {accent_colors.get(state, accent_colors['neutral'])};")
            bonus.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            bonus.setMinimumWidth(74)
            row_layout.addWidget(amount)
            row_layout.addWidget(bonus)
        return row

    def _quick_section(self, title: str, rows: list[tuple[str, str, str, str]]) -> QGroupBox:
        return ResponsiveStatSection(
            title,
            [self._quick_stat_row(label, value, accent, state)
             for label, value, accent, state in rows],
        )

    def _render_quick_stats(self, player, enemy):
        stats = player.stats
        base_player = create_player.create_player(
            player.main_job,
            player.sub_job,
            player.master_level,
            gearset={slot: dict(gear.Empty) for slot in SLOTS},
            buffs=player.buffs,
            abilities=player.abilities,
        )
        base_stats = base_player.stats
        while self.quick_stats_layout.count():
            item = self.quick_stats_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        def value(name: str) -> str:
            return self._quick_stat_value(stats.get(name, 0))

        def pct(name: str) -> str:
            return self._quick_stat_value(stats.get(name, 0) / 100, percent=True)

        def base_bonus(name: str):
            base = float(base_stats.get(name, 0))
            total = float(stats.get(name, 0))
            bonus = total - base
            if abs(bonus) < 0.0005:
                colored = ""
            else:
                colored = f"{'+' if bonus > 0 else '-'}{self._quick_stat_value(abs(bonus))}"
            return name, self._quick_stat_value(base), colored, "neutral" if bonus >= 0 else "over"

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
            self._quick_section("Attributes · base + equipment", [
                base_bonus(name) for name in ("STR", "DEX", "VIT", "AGI", "INT", "MND", "CHR")
            ]),
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
        grid.setContentsMargins(10, 14, 10, 10)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        self.candidate_buttons = {}
        self.candidate_detail_labels = {}
        self.candidate_cards = {}
        self.candidate_slot_labels = {}
        for index, slot in enumerate(SLOTS):
            button = QPushButton("1 selected")
            button.setObjectName("candidateButton")
            button.setMinimumHeight(34)
            button.setIconSize(QSize(24, 24))
            button.clicked.connect(lambda _checked=False, name=slot: self.choose_candidates(name))
            row, column = divmod(index, 4)
            card = QFrame()
            card.setObjectName("candidateCard")
            card.setMinimumHeight(92)
            cell = QVBoxLayout(card)
            cell.setContentsMargins(10, 8, 10, 8)
            cell.setSpacing(4)
            label = QLabel(slot.upper())
            label.setObjectName("candidateSlot")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.addWidget(label)
            detail = QLabel()
            detail.setObjectName("candidatePlayer")
            detail.setWordWrap(False)
            cell.addWidget(detail)
            cell.addWidget(button)
            grid.addWidget(card, row, column)
            self.candidate_buttons[slot] = button
            self.candidate_detail_labels[slot] = detail
            self.candidate_cards[slot] = card
            self.candidate_slot_labels[slot] = label
        select_all = QPushButton("Select all gear in all slots")
        select_all.setMinimumHeight(34)
        select_all.setToolTip("Include every available item for every optimizer slot.")
        select_all.clicked.connect(self.select_all_candidates)
        grid.addWidget(select_all, 4, 0, 1, 4)
        self.exclude_under_119 = QCheckBox("Remove items under item level 119")
        self.exclude_under_119.setToolTip(
            "Deselect optimizer candidates below item level 119 in head, body, "
            "hands, legs, and feet. "
            "Items without item-level metadata are kept."
        )
        self.exclude_under_119.toggled.connect(self._candidate_filter_changed)
        grid.addWidget(self.exclude_under_119, 5, 0, 1, 4)
        self.include_shared_gear = QCheckBox("Show transferable gear in equipment pickers")
        self.include_shared_gear.setToolTip(
            "Opt-in: show only non-Ex gear exported as transferable by GearSetBuilder "
            "from the other discovered characters in the gear-slot picker."
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
            "Combined TP + WS", "Tradeoff optimization",
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
            "Maximum allowed primary-performance loss from the best primary set."
        )
        self.tradeoff_depth = QComboBox()
        self.tradeoff_depth.addItems(["Fast", "Standard"])
        self.tradeoff_depth.setToolTip(
            "Fast keeps the normal optimizer's tradeoffs. Deep explores additional "
            "primary-loss bands and is intended for overnight searches."
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
        self.restarts.setRange(1, 12)
        self.restarts.setValue(6)
        self.restarts.setToolTip(
            "Independent search runs from different starting points. More runs improve "
            "coverage but add work. Limited to 12 runs."
        )
        self.optimizer_quality = QComboBox()
        self.optimizer_quality.addItems([*SEARCH_QUALITY_NAMES, "Custom"])
        self.optimizer_quality.setCurrentText("Fast")
        self.optimizer_quality.setToolTip(
            "Fast uses 6 searches x 4 passes. Standard uses 10 x 10 and may reuse a "
            "validated prior winner. Deep uses 12 x 10 independent searches and never "
            "uses cross-character or cross-job starting knowledge."
        )
        self.optimizer_passes = QSpinBox()
        self.optimizer_passes.setRange(1, 20)
        self.optimizer_passes.setValue(4)
        self.optimizer_passes.setToolTip("Convergence passes performed by each independent search.")
        self._applying_search_quality = False
        self.optimizer_quality.currentTextChanged.connect(self._apply_optimizer_quality)
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
            self._optimizer_search_controls_changed
        )
        self.optimizer_passes.valueChanged.connect(self._optimizer_search_controls_changed)
        self.seed = QLineEdit()
        self.seed.setPlaceholderText("random")
        self.seed.setToolTip(
            "Optional repeatable seed. Enter a number to enable durable result caching; "
            "blank creates a new random search and is not cached."
        )
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
        self.tradeoff_depth.setVisible(False)
        for index, combo in enumerate(self.substat_combos, start=1):
            form.addRow(f"Secondary stat priority {index}", combo)
        form.addRow("Minimum PDT reduction %", self.pdt)
        form.addRow("Minimum MDT reduction %", self.mdt)
        form.addRow("Minimum DT reduction %", self.dt)
        form.addRow(self.combined_defense_both)
        form.addRow("Search quality", self.optimizer_quality)
        form.addRow("Worker mode", self.parallel_mode)
        form.addRow("Search runs", self.restarts)
        form.addRow("Passes per run", self.optimizer_passes)
        form.addRow("Parallel workers", self.workers)
        form.addRow(self.prune_candidates)
        self.optimize_button = QPushButton("Start optimization")
        self.optimize_button.setObjectName("optimizerStartAction")
        self.optimize_button.setMinimumHeight(42)
        self.optimize_button.clicked.connect(self.run_optimizer)
        self.stop_optimizer_button = QPushButton("Stop")
        self.stop_optimizer_button.setObjectName("optimizerStopAction")
        self.stop_optimizer_button.setMinimumHeight(42)
        self.stop_optimizer_button.setEnabled(False)
        self.stop_optimizer_button.setToolTip("Request a cooperative stop after the current candidate calculation.")
        self.stop_optimizer_button.clicked.connect(self.stop_optimizer)
        self.overnight_button = QPushButton("Run overnight simulations")
        self.overnight_button.setMinimumHeight(32)
        self.overnight_button.setToolTip(
            "Warm the normal Quick Look cache with current sets and one-item variants "
            "from selected optimizer candidates. Use Warm cache mode for optimizer rankings."
        )
        self.overnight_button.clicked.connect(self.run_overnight_simulations)
        self.stop_overnight_button = QPushButton("Stop overnight")
        self.stop_overnight_button.setMinimumHeight(32)
        self.stop_overnight_button.setEnabled(False)
        self.stop_overnight_button.clicked.connect(self.stop_overnight_simulations)
        self.equip_best_button = QPushButton("Equip best set")
        self.equip_best_button.setMinimumHeight(32)
        self.equip_best_button.setEnabled(False)
        self.equip_best_button.clicked.connect(self.equip_best)
        top.addWidget(options)

        self.optimizer_control_panel = QFrame()
        self.optimizer_control_panel.setObjectName("optimizerControlPanel")
        self.optimizer_control_panel.setProperty("state", "idle")
        control_layout = QVBoxLayout(self.optimizer_control_panel)
        self.optimizer_control_layout = control_layout
        control_layout.setContentsMargins(10, 8, 10, 8)
        control_layout.setSpacing(7)
        activity_row = QHBoxLayout()
        activity_row.setSpacing(10)
        self.optimizer_run_state_label = QLabel(OPTIMIZER_STATE_LABELS["idle"])
        self.optimizer_run_state_label.setObjectName("optimizerRunState")
        self.optimizer_run_state_label.setProperty("state", "idle")
        self.optimizer_run_summary = QLabel("Configure the search, then start optimization.")
        self.optimizer_run_summary.setObjectName("optimizerRunSummary")
        self.optimizer_run_summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        activity_row.addWidget(self.optimizer_run_state_label)
        activity_row.addWidget(self.optimizer_run_summary, 1)
        control_layout.addLayout(activity_row)
        self.optimizer_run_progress = QProgressBar()
        self.optimizer_run_progress.setObjectName("optimizerRunProgress")
        self.optimizer_run_progress.setRange(0, 1000)
        self.optimizer_run_progress.setValue(0)
        self.optimizer_run_progress.setFormat("Ready")
        control_layout.addWidget(self.optimizer_run_progress)

        primary_controls = QHBoxLayout()
        self.optimizer_primary_controls = primary_controls
        primary_controls.setSpacing(7)
        primary_controls.addWidget(self.optimize_button, 2)
        primary_controls.addWidget(self.stop_optimizer_button, 1)
        self.show_optimizer_status_button = QPushButton("Show simulation")
        self.show_optimizer_status_button.setObjectName("optimizerShowAction")
        self.show_optimizer_status_button.setMinimumHeight(42)
        self.show_optimizer_status_button.setToolTip(
            "Open the optimizer log, overall progress, and per-run status in a separate window."
        )
        self.show_optimizer_status_button.clicked.connect(self.show_optimizer_status)
        primary_controls.addWidget(self.show_optimizer_status_button, 1)
        self.show_top_sets_button = QPushButton("Show Gear")
        self.show_top_sets_button.setMinimumHeight(42)
        self.show_top_sets_button.setEnabled(False)
        self.show_top_sets_button.clicked.connect(self.show_top_sets)
        primary_controls.addWidget(self.show_top_sets_button, 1)
        control_layout.addLayout(primary_controls)

        secondary_controls = QHBoxLayout()
        secondary_controls.setSpacing(7)
        secondary_controls.addWidget(QLabel("Result"))
        secondary_controls.addWidget(self.equip_best_button)
        secondary_controls.addStretch(1)
        control_layout.addLayout(secondary_controls)
        layout.addWidget(self.optimizer_control_panel)
        layout.addLayout(top)
        layout.addStretch(1)
        self._build_optimizer_status_dialog()
        self._refresh_optimizer_metrics(self.optimize_action.currentText())
        self._refresh_ranking_weapon_types()
        self._refresh_combined_options(self.optimize_action.currentText())
        self._refresh_parallel_mode(self.parallel_mode.currentText())
        self._apply_optimizer_quality("Fast")
        self._refresh_candidate_preset_names()
        for slot in SLOTS:
            self._update_candidate_button(slot)
        return tab

    def _build_optimizer_status_dialog(self):
        """Keep progress details out of the candidate-selection layout."""
        self.optimizer_status_dialog = QDialog(self)
        self.optimizer_status_dialog.setObjectName("simulationStatusDialog")
        self.optimizer_status_dialog.setWindowTitle("Simulation status")
        self.optimizer_status_dialog.setMinimumSize(640, 460)
        self.optimizer_status_dialog.resize(960, 600)
        self.optimizer_status_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.optimizer_status_dialog.installEventFilter(self)
        status_layout = QVBoxLayout(self.optimizer_status_dialog)
        status_layout.setContentsMargins(12, 12, 12, 12)
        status_layout.setSpacing(10)
        log_controls = QHBoxLayout()
        log_controls.addWidget(QLabel("Log filter"))
        self.optimizer_log_filter = QLineEdit()
        self.optimizer_log_filter.setPlaceholderText("Filter messages, runs, or errors...")
        self.optimizer_log_filter.setClearButtonEnabled(True)
        self.optimizer_log_filter.textChanged.connect(self._render_optimizer_log)
        log_controls.addWidget(self.optimizer_log_filter, 1)
        clear_log = QPushButton("Clear log")
        clear_log.setToolTip("Remove the current live log entries without changing the simulation.")
        clear_log.clicked.connect(self._clear_optimizer_log)
        log_controls.addWidget(clear_log)
        latest_log = QPushButton("Latest")
        latest_log.setToolTip("Scroll the live log to the newest entry.")
        latest_log.clicked.connect(lambda: self.optimizer_log.verticalScrollBar().setValue(
            self.optimizer_log.verticalScrollBar().maximum()
        ))
        log_controls.addWidget(latest_log)
        status_layout.addLayout(log_controls)
        self.optimizer_log = QTextEdit()
        self.optimizer_log.setReadOnly(True)
        self.optimizer_log.setAcceptRichText(True)
        self.optimizer_log.setPlaceholderText("Optimizer progress appears here.")
        self.optimizer_log.setMinimumHeight(108)
        self.optimizer_log.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        status_layout.addWidget(self.optimizer_log, 1)
        status_box = QGroupBox("Search status")
        status_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        status_grid = QGridLayout(status_box)
        status_grid.setContentsMargins(12, 12, 12, 12)
        status_grid.setHorizontalSpacing(24)
        status_grid.setVerticalSpacing(10)
        self.optimizer_progress_value = QLabel("Overall progress: —")
        self.optimizer_eta_value = QLabel("Estimated time remaining: —")
        self.optimizer_best_value = QLabel("Best metric: —")
        self.optimizer_phase_value = QLabel("Current phase: —")
        for label in (
            self.optimizer_progress_value, self.optimizer_eta_value,
            self.optimizer_best_value, self.optimizer_phase_value,
        ):
            label.setMinimumWidth(0)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.optimizer_progress_value.setObjectName("simulationProgressValue")
        self.optimizer_eta_value.setObjectName("simulationEtaValue")
        self.optimizer_best_value.setObjectName("simulationResultValue")
        self.optimizer_phase_value.setObjectName("simulationPhaseValue")
        status_grid.addWidget(self.optimizer_progress_value, 0, 0)
        status_grid.addWidget(self.optimizer_eta_value, 0, 1)
        status_grid.addWidget(self.optimizer_best_value, 1, 0)
        status_grid.addWidget(self.optimizer_phase_value, 1, 1)
        status_layout.addWidget(status_box)
        self.optimizer_runs_box = QGroupBox("Per-run status")
        self.optimizer_runs_box.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        self.optimizer_runs_layout = QGridLayout(self.optimizer_runs_box)
        # Keep the run matrix compact: Standard has ten cards and Deep has
        # twelve, so a generous card gutter quickly makes the status window
        # taller than the useful log area.
        self.optimizer_runs_layout.setContentsMargins(6, 6, 6, 6)
        self.optimizer_runs_layout.setHorizontalSpacing(6)
        self.optimizer_runs_layout.setVerticalSpacing(6)
        self.optimizer_runs_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.optimizer_runs_placeholder = QLabel(
            "Run the optimizer to show a fixed status section for each search run."
        )
        self.optimizer_runs_layout.addWidget(self.optimizer_runs_placeholder, 0, 0)
        status_layout.addWidget(self.optimizer_runs_box)
        self.profile_builder_batch_box = QGroupBox("Profile Builder loadout batch")
        self.profile_builder_batch_box.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        profile_batch_layout = QGridLayout(self.profile_builder_batch_box)
        self.profile_builder_batch_section = QLabel("No Profile Builder batch is active.")
        self.profile_builder_batch_progress = QLabel()
        self.profile_builder_batch_depth = QLabel()
        for row, label in enumerate((
            self.profile_builder_batch_section,
            self.profile_builder_batch_progress,
            self.profile_builder_batch_depth,
        )):
            label.setWordWrap(True)
            profile_batch_layout.addWidget(label, row, 0)
        self.profile_builder_batch_box.setVisible(False)
        status_layout.addWidget(self.profile_builder_batch_box)
        activity_bar = QWidget()
        activity_bar.setObjectName("simulationFooter")
        activity_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        cache_controls = QHBoxLayout(activity_bar)
        cache_controls.setContentsMargins(0, 0, 0, 0)
        self.cache_status_value = QLabel()
        self.cache_status_value.setVisible(False)
        cache_controls.addStretch(1)
        self.optimizer_activity = QLabel("Idle")
        self.optimizer_activity.setObjectName("simulationActivity")
        self.optimizer_activity.setAlignment(Qt.AlignmentFlag.AlignRight)
        cache_controls.addWidget(self.optimizer_activity)
        status_layout.addWidget(activity_bar)
        self._refresh_cache_status()
        self.optimizer_close_when_done = QCheckBox("Close when all simulations finish")
        self.optimizer_close_when_done.setToolTip(
            "Keep this window open between Profile Builder loadouts, then close it after the full batch finishes."
        )
        self.optimizer_close_when_done.setChecked(
            self.settings.value("optimizer/close_status_when_done", False, bool)
        )
        self.optimizer_close_when_done.toggled.connect(
            lambda enabled: self.settings.setValue(
                "optimizer/close_status_when_done", bool(enabled)
            )
        )
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.optimizer_status_dialog.hide)
        bottom_bar = QWidget()
        bottom_bar.setObjectName("simulationFooter")
        bottom_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bottom_controls = QHBoxLayout(bottom_bar)
        bottom_controls.setContentsMargins(0, 0, 0, 0)
        bottom_controls.addWidget(self.optimizer_close_when_done)
        bottom_controls.addStretch(1)
        bottom_controls.addWidget(buttons)
        status_layout.addWidget(bottom_bar)

    def show_optimizer_status(self):
        self._refresh_cache_status()
        self._refresh_profile_builder_batch_status()
        self.optimizer_status_dialog.show()
        self.optimizer_status_dialog.raise_()
        self.optimizer_status_dialog.activateWindow()

    def show_results_workspace(self):
        """Open the persistent Results workspace from the global simulation strip."""
        self.refresh_results_history()
        self._select_tab("Results")

    def _schedule_optimizer_status_close(self):
        if hasattr(self, "optimizer_close_when_done") and self.optimizer_close_when_done.isChecked():
            QTimer.singleShot(250, self._close_optimizer_status_if_finished)

    def _close_optimizer_status_if_finished(self):
        if not self.optimizer_close_when_done.isChecked():
            return
        running = any(
            thread is not None and thread.isRunning()
            for thread in (self.optimizer_thread, self.overnight_thread)
        )
        batch_pending = bool(
            getattr(self, "_profile_builder_optimizer_active", None)
            or getattr(self, "_profile_builder_optimizer_queue", [])
        )
        if running or batch_pending:
            QTimer.singleShot(250, self._close_optimizer_status_if_finished)
            return
        self.optimizer_status_dialog.hide()

    def _set_optimizer_run_ui(self, state: str, summary: str | None = None,
                              progress: float | None = None):
        """Keep the Optimize tab's persistent run indicator synchronized."""
        if not hasattr(self, "optimizer_control_panel"):
            return
        state = state if state in OPTIMIZER_STATE_LABELS else "idle"
        self.optimizer_run_state_label.setText(OPTIMIZER_STATE_LABELS[state])
        if summary is not None:
            self.optimizer_run_summary.setText(str(summary))
        for widget in (self.optimizer_control_panel, self.optimizer_run_state_label):
            widget.setProperty("state", state)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        if hasattr(self, "simulation_header_panel"):
            self.simulation_header_panel.setProperty("state", state)
            self.simulation_header_panel.style().unpolish(self.simulation_header_panel)
            self.simulation_header_panel.style().polish(self.simulation_header_panel)
        if hasattr(self, "optimizer_header_eta"):
            self.optimizer_header_eta.setProperty("state", state)
            self.optimizer_header_eta.style().unpolish(self.optimizer_header_eta)
            self.optimizer_header_eta.style().polish(self.optimizer_header_eta)
            if state in {"completed", "restored"}:
                self._set_optimizer_header_eta(status="complete")
            elif state == "failed":
                self._set_optimizer_header_eta(status="unavailable")
            elif state in {"stopping", "stopped"}:
                self._set_optimizer_header_eta(status="stopped")
        if progress is None and state in {"starting", "running", "warming", "stopping"}:
            self.optimizer_run_progress.setRange(0, 0)
            self.optimizer_run_progress.setFormat("")
            return
        value = (
            100.0 if state in {"completed", "restored"} else 0.0
        ) if progress is None else max(0.0, min(100.0, float(progress)))
        self.optimizer_run_progress.setRange(0, 1000)
        self.optimizer_run_progress.setValue(round(value * 10))
        self.optimizer_run_progress.setFormat(
            f"{value:.1f}%" if state in {"starting", "running", "warming", "stopping"}
            else OPTIMIZER_STATE_LABELS[state].title()
        )

    def _set_optimizer_header_eta(
        self, remaining: float | None = None, status: str | None = None,
    ):
        """Show a short seconds-only ETA beside the persistent progress bar."""
        if not hasattr(self, "optimizer_header_eta"):
            return
        if status is not None:
            text = f"Est. Time Remaining: {status}"
        elif remaining is None:
            text = "Est. Time Remaining: --"
        else:
            text = f"Est. Time Remaining: {max(0, math.ceil(float(remaining)))}s"
        self.optimizer_header_eta.setText(text)

    def _refresh_profile_builder_batch_status(self):
        if hasattr(self, "dashboard_build_status"):
            self._refresh_build_dashboard()
        active = getattr(self, "_profile_builder_optimizer_active", None)
        queue = list(getattr(self, "_profile_builder_optimizer_queue", []))
        total = int(getattr(self, "_profile_builder_optimizer_total", 0) or 0)
        completed = int(getattr(self, "_profile_builder_optimizer_completed_count", 0) or 0)
        state = str(getattr(self, "_profile_builder_optimizer_batch_state", "") or "")
        if hasattr(self, "profile_stop_button"):
            self.profile_stop_button.setEnabled(bool(active or queue) and state == "running")
        if not hasattr(self, "profile_builder_batch_box"):
            return
        visible = bool(active or queue or total or state)
        self.profile_builder_batch_box.setVisible(visible)
        if not visible:
            return
        depth = self.profile_builder_depth.currentText() if hasattr(self, "profile_builder_depth") else "Fast"
        passes, restarts, _shared = _search_quality_settings(depth)
        if active:
            details = ((getattr(self, "_profile_builder_result", {}) or {}).get("recipe_details") or {}).get(active, {})
            scenario = details.get("optimizer") or {}
            floors = ", ".join(
                f"{label} {int(scenario.get(label.casefold(), 0) or 0)}%"
                for label in ("PDT", "MDT", "DT")
                if int(scenario.get(label.casefold(), 0) or 0) > 0
            ) or "no reduction floor"
            self.profile_builder_batch_section.setText(
                f"Current loadout: {active} · {scenario.get('enemy', 'current enemy')} · "
                f"{int(scenario.get('tp', 1000) or 1000):,} TP · {floors}"
            )
        elif state == "stopped":
            self.profile_builder_batch_section.setText("Profile Builder loadout batch stopped.")
        elif completed >= total and total:
            self.profile_builder_batch_section.setText("Profile Builder loadout batch complete.")
        else:
            self.profile_builder_batch_section.setText("Preparing next Profile Builder loadout...")
        self.profile_builder_batch_progress.setText(
            f"Loadouts: {completed}/{total or completed} complete · {len(queue)} queued"
        )
        self.profile_builder_batch_depth.setText(
            f"Search quality: {depth} · {restarts} search run(s) × {passes} passes per combat loadout."
        )

    def _aspirational_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        note = QLabel(
            "Modeled gear from the legacy GUI that is not in the current character's bridge inventory. "
            "Add an item to make it an optimizer candidate; aspirational items cannot be equipped in Quick Look."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        controls = QHBoxLayout()
        self.aspirational_filter = QLineEdit()
        self.aspirational_filter.setPlaceholderText("Filter item name or stats...")
        self.aspirational_filter.textChanged.connect(self._refresh_aspirational_table)
        self.aspirational_slot = QComboBox()
        self.aspirational_slot.addItem("All slots", "")
        for slot in SLOTS:
            self.aspirational_slot.addItem(slot.upper(), slot)
        self.aspirational_slot.currentIndexChanged.connect(self._refresh_aspirational_table)
        self.aspirational_rema_only = QCheckBox("REMA 119 III only")
        self.aspirational_rema_only.setToolTip(
            "Show the legacy REMA catalog, including base and R15 modeled variants."
        )
        self.aspirational_rema_only.toggled.connect(self._refresh_aspirational_table)
        controls.addWidget(self.aspirational_filter, 1)
        controls.addWidget(self.aspirational_slot)
        controls.addWidget(self.aspirational_rema_only)
        layout.addLayout(controls)
        self.aspirational_table = QTableWidget(0, 5)
        self.aspirational_table.setHorizontalHeaderLabels(
            ["Slot(s)", "Item", "Variant", "Candidate", "Modeled stats"]
        )
        self.aspirational_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.aspirational_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.aspirational_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.aspirational_table.setAlternatingRowColors(True)
        self.aspirational_table.setIconSize(QSize(32, 32))
        self.aspirational_table.verticalHeader().setVisible(False)
        self.aspirational_table.verticalHeader().setDefaultSectionSize(36)
        self.aspirational_table.setColumnWidth(0, 96)
        self.aspirational_table.setColumnWidth(1, 190)
        self.aspirational_table.setColumnWidth(2, 120)
        self.aspirational_table.setColumnWidth(3, 142)
        self.aspirational_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.aspirational_table, 1)
        actions = QHBoxLayout()
        add_selected = QPushButton("Add selected to optimizer")
        add_selected.clicked.connect(self._add_selected_aspirational_items)
        add_rema = QPushButton("Add all listed REMA")
        add_rema.setToolTip("Add every currently visible REMA base/R15 variant to the optimizer candidates.")
        add_rema.clicked.connect(self._add_visible_rema_items)
        remove_selected = QPushButton("Remove selected")
        remove_selected.clicked.connect(self._remove_selected_aspirational_items)
        self.aspirational_summary = QLabel()
        actions.addWidget(add_selected)
        actions.addWidget(add_rema)
        actions.addWidget(remove_selected)
        actions.addStretch(1)
        actions.addWidget(self.aspirational_summary)
        layout.addLayout(actions)
        self._refresh_aspirational_table()
        return tab

    def _aspirational_item_is_owned(self, item: dict) -> bool:
        """Treat a base item as owned at any rank; R15 requires an R15 bridge row."""
        name = _normalized_item_name(item.get("Name"))
        wants_r15 = _is_r15_variant(item)
        for owned in self.bridge_store.catalog.values():
            if _normalized_item_name(owned.get("Name")) != name:
                continue
            if not wants_r15 or _is_r15_variant(owned):
                return True
        return False

    def _aspirational_records(self) -> list[tuple[str, dict]]:
        job = JOBS.get(self.main_job.currentText(), "")
        slot_filter = str(self.aspirational_slot.currentData() or "")
        query = self.aspirational_filter.text().strip().casefold()
        rema_only = self.aspirational_rema_only.isChecked()
        records = []
        for name, record in self.aspirational_catalog.items():
            item = record["item"]
            jobs = [str(value).casefold() for value in item.get("Jobs", gear.all_jobs)]
            if job not in jobs or self._aspirational_item_is_owned(item):
                continue
            if slot_filter and slot_filter not in record["slots"]:
                continue
            is_rema = str(item.get("Name") or "") in REMA_WEAPON_NAMES
            if rema_only and not is_rema:
                continue
            if query and query not in item_tooltip(item).casefold():
                continue
            records.append((name, record))
        return sorted(records, key=lambda entry: (min(SLOTS.index(slot) for slot in entry[1]["slots"]), entry[0].casefold()))

    @staticmethod
    def _aspirational_stat_summary(item: dict) -> str:
        ignored = {"Name", "Name2", "Jobs", "Type", "Skill Type", "Rank"}
        parts = [
            f"{key} {value:+g}" for key, value in item.items()
            if key not in ignored and isinstance(value, (int, float)) and value
        ]
        return " · ".join(parts[:6]) + (" …" if len(parts) > 6 else "")

    def _refresh_aspirational_table(self, *_args):
        if not hasattr(self, "aspirational_table"):
            return
        records = self._aspirational_records()
        self.aspirational_table.setSortingEnabled(False)
        self.aspirational_table.setRowCount(len(records))
        for row, (name, record) in enumerate(records):
            item = record["item"]
            tooltip = item_tooltip(item)
            slots = ", ".join(slot.upper() for slot in SLOTS if slot in record["slots"])
            variant = "R15" if _is_r15_variant(item) else "Base"
            if str(item.get("Name") or "") in REMA_WEAPON_NAMES:
                variant = f"REMA 119 III · {variant}"
            cells = (
                QTableWidgetItem(slots),
                QTableWidgetItem(str(item.get("Name") or name)),
                QTableWidgetItem(variant),
                QTableWidgetItem("Added" if name in self.aspirational_selected else "Not added"),
                QTableWidgetItem(self._aspirational_stat_summary(item)),
            )
            cells[1].setIcon(self.icons.icon(item))
            for cell in cells:
                cell.setData(Qt.ItemDataRole.UserRole, name)
                cell.setToolTip(tooltip)
            for column, cell in enumerate(cells):
                self.aspirational_table.setItem(row, column, cell)
        self.aspirational_summary.setText(
            f"{len(records)} unowned modeled variants · {len(self.aspirational_selected)} added"
        )

    def _selected_aspirational_names(self) -> set[str]:
        return {
            str(self.aspirational_table.item(index.row(), 1).data(Qt.ItemDataRole.UserRole) or "")
            for index in self.aspirational_table.selectionModel().selectedRows()
        }

    def _set_aspirational_candidates(self, names: set[str], enabled: bool):
        changed = 0
        for name in names:
            record = self.aspirational_catalog.get(name)
            if record is None:
                continue
            if enabled:
                if name in self.aspirational_selected:
                    continue
                self.aspirational_selected.add(name)
            else:
                if name not in self.aspirational_selected:
                    continue
                self.aspirational_selected.discard(name)
            changed += 1
            for slot in record["slots"]:
                if enabled:
                    self.candidates[slot].add(name)
                else:
                    self.candidates[slot].discard(name)
                self._update_candidate_button(slot)
        if not changed:
            return
        self._refresh_locked_gear_options()
        self._refresh_aspirational_table()
        action = "Added" if enabled else "Removed"
        self.statusBar().showMessage(f"{action} {changed} aspirational item{'s' if changed != 1 else ''} in optimizer candidates.", 5000)

    def _add_selected_aspirational_items(self):
        self._set_aspirational_candidates(self._selected_aspirational_names(), True)

    def _add_visible_rema_items(self):
        names = {
            name for name, record in self._aspirational_records()
            if str(record["item"].get("Name") or "") in REMA_WEAPON_NAMES
        }
        self._set_aspirational_candidates(names, True)

    def _remove_selected_aspirational_items(self):
        self._set_aspirational_candidates(self._selected_aspirational_names(), False)

    def _refresh_optimizer_metrics(self, action: str):
        metrics = {
            "Weapon skill": ["Damage dealt", "TP return", "Magic accuracy"],
            "Rank weapon-type WS": ["Average damage at 1000 / 2000 / 3000 TP"],
            WARM_CACHE_ACTION: ["Average damage at 1000 / 2000 / 3000 TP"],
            "Attack round": ["Time to WS", "Damage dealt", "TP return", "DPS"],
            "Spell": ["Damage dealt", "TP return"],
            "Combined TP + WS": ["Combined DPS"],
            "Tradeoff optimization": {
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
        ranking = action in {"Rank weapon-type WS", WARM_CACHE_ACTION}
        self.ranking_weapon_type.setVisible(ranking)
        label = self.ranking_weapon_type.parentWidget().layout().labelForField(
            self.ranking_weapon_type
        )
        if label is not None:
            label.setVisible(ranking)
        substats = action in {"Tradeoff optimization", "Sub-stat optimization"}
        for widget in (self.substat_base_action, self.substat_loss_percent, self.tradeoff_depth, *self.substat_combos):
            widget.setVisible(substats)
        for widget in (self.substat_base_action, self.substat_loss_percent, self.tradeoff_depth, *self.substat_combos):
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

    def _apply_optimizer_quality(self, quality: str):
        if quality not in SEARCH_QUALITY or not hasattr(self, "optimizer_passes"):
            return
        passes, restarts, _shared = _search_quality_settings(quality)
        self._applying_search_quality = True
        try:
            self.restarts.setValue(restarts)
            self.optimizer_passes.setValue(passes)
            self.tradeoff_depth.setCurrentText("Fast" if quality == "Fast" else "Standard")
        finally:
            self._applying_search_quality = False
        self._refresh_parallel_mode(self.parallel_mode.currentText())

    def _optimizer_search_controls_changed(self, _value=None):
        if not getattr(self, "_applying_search_quality", False):
            expected = next((
                name for name, policy in SEARCH_QUALITY.items()
                if int(policy["restarts"]) == self.restarts.value()
                and int(policy["passes"]) == self.optimizer_passes.value()
            ), "Custom")
            self.optimizer_quality.blockSignals(True)
            self.optimizer_quality.setCurrentText(expected)
            self.optimizer_quality.blockSignals(False)
        self._refresh_parallel_mode(self.parallel_mode.currentText())

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

    def _results_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        heading.addWidget(QLabel("Completed simulations, rankings, optimizers, and saved Quick Look checks"))
        heading.addStretch(1)
        self.results_filter = QLineEdit()
        self.results_filter.setPlaceholderText("Filter title, job, enemy, or weapon skill...")
        self.results_filter.textChanged.connect(self.refresh_results_history)
        heading.addWidget(self.results_filter)
        self.results_favorites_only = QCheckBox("Pinned only")
        self.results_favorites_only.setToolTip("Treat pinned results as favorites and hide the rest.")
        self.results_favorites_only.toggled.connect(self.refresh_results_history)
        heading.addWidget(self.results_favorites_only)
        layout.addLayout(heading)

        self.results_table = QTableWidget(0, 7)
        self.results_table.setHorizontalHeaderLabels(
            ["Result", "Type", "Scenario", "Metric", "Age", "Pinned", "Status"]
        )
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setWordWrap(False)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.verticalHeader().setDefaultSectionSize(28)
        results_header = self.results_table.horizontalHeader()
        results_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        results_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        results_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for column in (3, 4, 5, 6):
            results_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.results_table.setColumnWidth(0, 230)
        self.results_table.itemSelectionChanged.connect(self._result_selection_changed)
        layout.addWidget(self.results_table, 2)

        detail_box = QGroupBox("Result details")
        detail_layout = QVBoxLayout(detail_box)
        self.results_detail = QPlainTextEdit()
        self.results_detail.setObjectName("resultDetailsText")
        self.results_detail.setReadOnly(True)
        self.results_detail.setFixedHeight(116)
        self.results_detail.setPlaceholderText("Select a result to see its scenario, metrics, warnings, and gear.")
        detail_layout.addWidget(self.results_detail)
        self.results_gear_table = QTableWidget(0, 4)
        self.results_gear_table.setHorizontalHeaderLabels(["Set", "Slot", "Gear", "Relevant stats"])
        self.results_gear_table.setIconSize(QSize(30, 30))
        self.results_gear_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results_gear_table.setWordWrap(False)
        self.results_gear_table.verticalHeader().setVisible(False)
        self.results_gear_table.verticalHeader().setDefaultSectionSize(34)
        gear_header = self.results_gear_table.horizontalHeader()
        gear_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        gear_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        gear_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        gear_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.results_gear_table.setColumnWidth(2, 260)
        self.results_gear_table.setFixedHeight(42)
        detail_layout.addWidget(self.results_gear_table)
        layout.addWidget(detail_box)

        buttons = QGridLayout()
        buttons.setHorizontalSpacing(6)
        buttons.setVerticalSpacing(6)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_results_history)
        load = QPushButton("Load into workspace")
        load.clicked.connect(self.load_history_result)
        rerun = QPushButton("Repeat exact run")
        rerun.setToolTip("Repeat the saved calculation using its internally retained reproducibility data.")
        rerun.clicked.connect(self.rerun_history_result)
        compare = QPushButton("Compare selected")
        compare.clicked.connect(self.compare_history_results)
        graphs = QPushButton("View graphs")
        graphs.setToolTip("Open the selected result's DPS convergence and WS distribution graphs.")
        graphs.clicked.connect(self.view_history_graphs)
        compare_pinned = QPushButton("Compare pinned")
        compare_pinned.clicked.connect(self.compare_pinned_results)
        pin = QPushButton("Pin / unpin")
        pin.clicked.connect(self.toggle_history_pin)
        rename = QPushButton("Rename")
        rename.clicked.connect(self.rename_history_result)
        delete = QPushButton("Delete")
        delete.clicked.connect(self.delete_history_result)
        clear = QPushButton("Clear unpinned")
        clear.clicked.connect(self.clear_unpinned_history)
        action_buttons = (refresh, load, rerun, graphs, compare, compare_pinned, pin, rename, delete, clear)
        for index, button in enumerate(action_buttons):
            buttons.addWidget(button, index // 5, index % 5)
        for column in range(5):
            buttons.setColumnStretch(column, 1)
        layout.addLayout(buttons)
        self.refresh_results_history()
        return tab

    def _history_character_key(self) -> str:
        character = self.bridge_store.data.get("character") if isinstance(self.bridge_store.data, dict) else {}
        bridge_key = character.get("key") if isinstance(character, dict) else ""
        return str(self._active_character_key or bridge_key or "default")

    def _history_scenario(self, *, action: str, ws_name: str = "", tp: int = 0, seed=None) -> dict:
        buff_preset = self.buff_preset_combo.currentText() if hasattr(self, "buff_preset_combo") else ""
        return {
            "character": self._history_character_key(),
            "job": f"{self.main_job.currentText()}/{self.sub_job.currentText()}",
            "enemy": self.enemy_combo.currentText(),
            "ws": ws_name or self.ws_combo.currentText(),
            "tp": int(tp or 0),
            "action": action,
            "buff_preset": buff_preset,
            "seed": seed,
        }

    @staticmethod
    def _history_relevant_stats(item: dict) -> str:
        keys = (
            "Attack", "Accuracy", "Store TP", "Double Attack", "Triple Attack",
            "Quadruple Attack", "Fast Cast", "Magic Accuracy", "Magic Attack",
            "PDT", "MDT", "DT", "Evasion", "Magic Evasion", "HP",
        )
        values = []
        for key in keys:
            value = item.get(key)
            if value not in (None, "", 0, 0.0):
                values.append(f"{key} {value}")
        return ", ".join(values[:7])

    def _history_gearsets(self, **sets) -> dict:
        return {
            str(name): _gearset_payload(value)
            for name, value in sets.items()
            if isinstance(value, dict)
        }

    @staticmethod
    def _history_player_snapshot(player) -> dict:
        """Persist only the compact display stats needed by legacy-style plots."""
        stats = getattr(player, "stats", {}) or {}
        display_stats = (
            "STR", "DEX", "VIT", "AGI", "INT", "MND", "CHR",
            "Accuracy1", "Accuracy2", "Attack1", "Attack2",
            "Ranged Accuracy", "Ranged Attack", "TP Bonus",
        )
        return {
            "main_job": str(getattr(player, "main_job", "") or ""),
            "sub_job": str(getattr(player, "sub_job", "") or ""),
            "master_level": int(getattr(player, "master_level", 0) or 0),
            "stats": _json_value({key: stats.get(key, 0) for key in display_stats}),
        }

    def _add_history(self, kind: str, title: str, payload: dict, *, pinned: bool = False):
        result_id = self.result_history.add(
            self._history_character_key(), kind, title, payload, pinned=pinned
        )
        self._history_selected_id = result_id
        if hasattr(self, "results_table"):
            self.refresh_results_history()
        return result_id

    @staticmethod
    def _history_metric_text(payload: dict) -> str:
        metrics = payload.get("metrics") or {}
        for key in ("total_dps", "dps", "metric", "average_ws_damage", "time_to_ws"):
            if metrics.get(key) not in (None, ""):
                value = metrics[key]
                try:
                    return f"{key.replace('_', ' ')} {float(value):,.1f}"
                except (TypeError, ValueError):
                    return f"{key.replace('_', ' ')} {value}"
        return "—"

    def refresh_results_history(self, *_args):
        if not hasattr(self, "results_table"):
            return
        records = self.result_history.list(self._history_character_key())
        if self.results_favorites_only.isChecked():
            records = [record for record in records if record.get("pinned")]
        query = self.results_filter.text().strip().casefold()
        if query:
            records = [
                record for record in records
                if query in json.dumps(record, ensure_ascii=False, default=str).casefold()
            ]
        self._history_records = records
        self.results_table.blockSignals(True)
        self.results_table.setRowCount(0)
        selected_row = -1
        for row, record in enumerate(records):
            self.results_table.insertRow(row)
            payload = record.get("payload") or {}
            scenario = payload.get("scenario") or {}
            values = (
                record.get("title", ""), record.get("kind", ""),
                f"{scenario.get('enemy', '')} · {scenario.get('ws', '')}",
                self._history_metric_text(payload),
                _result_age_text(record.get("created_at", time.time())),
                "Yes" if record.get("pinned") else "No",
                "Stale" if record.get("stale") else "Current",
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setToolTip(str(value))
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, int(record["id"]))
                self.results_table.setItem(row, column, cell)
            if record.get("id") == self._history_selected_id:
                selected_row = row
        self.results_table.blockSignals(False)
        if selected_row >= 0:
            self.results_table.selectRow(selected_row)
        elif records:
            self.results_table.selectRow(0)
        else:
            self._render_history_detail(None)

    def _selected_history_records(self) -> list[dict]:
        ids = set()
        for item in self.results_table.selectedItems():
            value = item.data(Qt.ItemDataRole.UserRole)
            if value is not None:
                ids.add(int(value))
        return [record for record in self._history_records if record.get("id") in ids]

    def _result_selection_changed(self):
        records = self._selected_history_records()
        self._history_selected_id = records[0].get("id") if records else None
        self._render_history_detail(records[0] if records else None)

    def _render_history_detail(self, record: dict | None):
        self.results_detail.clear()
        self.results_gear_table.setRowCount(0)
        if not record:
            self.results_detail.setFixedHeight(80)
            self.results_gear_table.setFixedHeight(42)
            return
        payload = record.get("payload") or {}
        scenario = payload.get("scenario") or {}
        metrics = payload.get("metrics") or {}
        try:
            scenario_tp = int(scenario.get("tp", 0) or 0)
        except (TypeError, ValueError):
            scenario_tp = 0
        lines = [
            f"{record.get('title', '')} · {record.get('kind', '')}",
            f"Saved {_result_age_text(record.get('created_at', time.time()))}",
            f"Scenario: {scenario.get('job', '')} · {scenario.get('enemy', '')} · "
            f"{scenario.get('ws', '')} at {scenario_tp:,} TP",
            "Metrics: " + " · ".join(
                f"{key.replace('_', ' ')}={value:,.2f}" if isinstance(value, (int, float))
                else f"{key.replace('_', ' ')}={value}"
                for key, value in metrics.items()
                if key not in {"dps_series", "histogram"}
            ),
        ]
        if record.get("stale"):
            lines.append("WARNING: this result was created with an older formula/source fingerprint.")
        if record.get("corrupt"):
            lines.append("WARNING: the saved payload was corrupt; only its history row is available.")
        for warning in payload.get("warnings") or []:
            lines.append(f"WARNING: {warning}")
        self.results_detail.setPlainText("\n".join(lines))
        visible_lines = max(4, min(7, len(lines)))
        detail_height = (
            visible_lines * self.results_detail.fontMetrics().lineSpacing()
            + 18
        )
        self.results_detail.setFixedHeight(detail_height)
        row = 0
        for set_name, gearset in (payload.get("gearsets") or {}).items():
            if not isinstance(gearset, dict):
                continue
            for slot in SLOTS:
                item = gearset.get(slot)
                if not isinstance(item, dict):
                    continue
                self.results_gear_table.insertRow(row)
                self.results_gear_table.setItem(row, 0, QTableWidgetItem(str(set_name)))
                self.results_gear_table.setItem(row, 1, QTableWidgetItem(slot.upper()))
                gear_cell = QTableWidgetItem(item_name(item))
                gear_cell.setIcon(self.icons.icon(item))
                gear_cell.setToolTip(item_tooltip(item))
                self.results_gear_table.setItem(row, 2, gear_cell)
                self.results_gear_table.setItem(row, 3, QTableWidgetItem(self._history_relevant_stats(item)))
                row += 1
        visible_rows = min(row, 5)
        header_height = max(26, self.results_gear_table.horizontalHeader().sizeHint().height())
        rows_height = visible_rows * self.results_gear_table.verticalHeader().defaultSectionSize()
        self.results_gear_table.setFixedHeight(header_height + rows_height + 4)

    def compare_history_results(self):
        records = self._selected_history_records()
        if not 2 <= len(records) <= 6:
            QMessageBox.information(self, "Compare results", "Select two to six results first.")
            return
        self.result_comparison_dialog = ResultComparisonDialog(records, self.icons, self)
        self.result_comparison_dialog.show()

    def view_history_graphs(self):
        """Show the graph-ready detail for one saved simulation result."""
        record = self._history_record_by_selection()
        if not record:
            QMessageBox.information(self, "View graphs", "Select one completed result first.")
            return
        self.result_comparison_dialog = ResultComparisonDialog([record], self.icons, self)
        self.result_comparison_dialog.show()

    def compare_pinned_results(self):
        records = [
            record for record in self.result_history.list(self._history_character_key())
            if record.get("pinned")
        ][:6]
        if len(records) < 2:
            QMessageBox.information(self, "Compare pinned", "Pin at least two results first.")
            return
        self.result_comparison_dialog = ResultComparisonDialog(records, self.icons, self)
        self.result_comparison_dialog.show()

    def _history_record_by_selection(self) -> dict | None:
        records = self._selected_history_records()
        return records[0] if records else None

    def load_history_result(self):
        record = self._history_record_by_selection()
        if not record:
            return
        payload = record.get("payload") or {}
        gearsets = payload.get("gearsets") or {}
        for key, editor in (("single", self.quick_set), ("tp", self.tp_set), ("ws", self.ws_set)):
            saved = gearsets.get(key)
            if not isinstance(saved, dict):
                continue
            editor.set_gearset({slot: self._resolve_saved_item(slot, saved.get(slot)) for slot in SLOTS})
        scenario = payload.get("scenario") or {}
        self._set_combo_value(self.ws_combo, scenario.get("ws"), self.ws_combo.currentText())
        try:
            self.tp_value.setValue(int(scenario.get("tp", self.tp_value.value())))
        except (TypeError, ValueError):
            pass
        if gearsets.get("tp") or gearsets.get("ws"):
            self.workspace_mode.setCurrentText("TP → WS Cycle")
        else:
            self.workspace_mode.setCurrentText("Single Set")
        self._select_tab("Gear Workspace")
        self.statusBar().showMessage(f"Loaded {record.get('title', 'result')} into Gear Workspace", 5000)

    def rerun_history_result(self):
        record = self._history_record_by_selection()
        if not record:
            return
        self.load_history_result()
        kind = str(record.get("kind") or "")
        payload = record.get("payload") or {}
        saved_seed = payload.get("seed")
        if saved_seed not in (None, ""):
            self.workspace_seed.setText(str(saved_seed))
        if kind == "optimizer" and isinstance(payload.get("optimizer"), dict):
            scenario = payload.get("scenario") or {}
            action = str(scenario.get("action") or "optimizer").removeprefix("optimizer: ")
            self._optimizer_action_in_progress = action
            context = self._optimizer_cache_context((
                JOBS[self.main_job.currentText()], JOBS.get(self.sub_job.currentText(), "None"),
                self.master_level.value(), self._combat_context()["buffs"],
                self._combat_context()["abilities"],
            ))
            restored = self._restore_optimizer_payload(payload["optimizer"], context)
            self._optimizer_done(restored)
            self._select_tab("Optimizer")
        elif kind == "distribution":
            self.plot_ws_distribution()
        elif kind in {"cycle", "optimizer"} and {
            key for key in (record.get("payload") or {}).get("gearsets", {})
        } & {"tp", "ws"}:
            self.run_simulation()
        else:
            self.evaluate("ws" if record.get("payload", {}).get("scenario", {}).get("ws") else "tp")

    def toggle_history_pin(self):
        record = self._history_record_by_selection()
        if record:
            self.result_history.update(record["id"], pinned=not record.get("pinned", False))
            self.refresh_results_history()

    def rename_history_result(self):
        record = self._history_record_by_selection()
        if not record:
            return
        title, accepted = QInputDialog.getText(self, "Rename result", "Title", text=record.get("title", ""))
        if accepted and title.strip():
            self.result_history.update(record["id"], title=title.strip())
            self.refresh_results_history()

    def delete_history_result(self):
        record = self._history_record_by_selection()
        if record:
            self.result_history.delete(record["id"])
            self._history_selected_id = None
            self.refresh_results_history()

    def clear_unpinned_history(self):
        removed = self.result_history.clear(self._history_character_key(), pinned=True)
        self._history_selected_id = None
        self.refresh_results_history()
        self.statusBar().showMessage(f"Removed {removed} unpinned result(s)", 4000)

    def _buffs_tab(self) -> QWidget:
        """Build the structured equivalent of the legacy Active Buffs pane."""
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)
        note = QLabel(
            "Enable only buffs currently active. These controls feed the existing "
            "calculation engine. Save named variations when you need to switch quickly."
        )
        note.setObjectName("buffIntro")
        note.setWordWrap(True)
        outer.addWidget(note)

        preset_box = QGroupBox("Buff presets")
        preset_layout = QHBoxLayout(preset_box)
        preset_layout.setContentsMargins(8, 10, 8, 7)
        preset_layout.setSpacing(6)
        self.buff_preset_combo = QComboBox()
        self.buff_preset_combo.setMinimumWidth(200)
        self.buff_preset_combo.setMaximumWidth(360)
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
        preset_layout.addWidget(self.buff_preset_combo)
        preset_layout.addStretch(1)
        preset_layout.addWidget(load_preset)
        preset_layout.addWidget(save_preset)
        preset_layout.addWidget(delete_preset)
        outer.addWidget(preset_box)
        preset_note = QLabel(
            "BG Wiki presets: Mid-buff (standard songs/rolls) and High-buff "
            "(Marcato plus GEO Fury/Frailty). Both include the 1350 evasion / "
            "1500 defense / 340 VIT and AGI / 280 INT and MND test enemy."
        )
        preset_note.setObjectName("buffPresetNote")
        preset_note.setWordWrap(True)
        outer.addWidget(preset_note)

        def compact_form(form: QFormLayout):
            form.setContentsMargins(8, 11, 8, 8)
            form.setHorizontalSpacing(8)
            form.setVerticalSpacing(4)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            form.setFormAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        def compact_combo(combo: QComboBox, width: int = 250):
            combo.setMaximumWidth(width)
            combo.setMinimumContentsLength(12)
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        def compact_spin(spin: QSpinBox, width: int = 145):
            spin.setMaximumWidth(width)
            spin.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        whm = QGroupBox("White Magic and food")
        whm_form = QFormLayout(whm)
        compact_form(whm_form)
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
        for combo in (
            self.dia_combo, self.haste_combo, self.boost_combo,
            self.storm_combo, self.food_combo,
        ):
            compact_combo(combo)
        compact_spin(self.enhancing_skill)
        whm_form.addRow(self.whm_enabled)
        whm_form.addRow(self.shell_v)
        whm_form.addRow("Dia", self.dia_combo)
        whm_form.addRow("Haste", self.haste_combo)
        whm_form.addRow("Boost", self.boost_combo)
        whm_form.addRow("Storm", self.storm_combo)
        whm_form.addRow("Enhancing skill", self.enhancing_skill)
        whm_form.addRow("Food", self.food_combo)

        bard = QGroupBox("Bard songs")
        bard_form = QFormLayout(bard)
        compact_form(bard_form)
        self.bard_enabled = QCheckBox("Enable Bard songs")
        self.song_bonus = QSpinBox()
        self.song_bonus.setRange(0, 9)
        self.song_bonus.setPrefix("Songs +")
        compact_spin(self.song_bonus)
        self.song_combos = []
        bard_form.addRow(self.bard_enabled)
        bard_form.addRow("Instrument bonus", self.song_bonus)
        song_names = ["None", *buff_data.brd]
        for index in range(5):
            combo = QComboBox()
            combo.addItems(song_names)
            compact_combo(combo)
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

        corsair = QGroupBox("Corsair rolls")
        cor_form = QFormLayout(corsair)
        compact_form(cor_form)
        self.cor_enabled = QCheckBox("Enable Corsair rolls")
        self.roll_bonus = QSpinBox()
        self.roll_bonus.setRange(0, 8)
        self.roll_bonus.setPrefix("Rolls +")
        compact_spin(self.roll_bonus)
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
            compact_combo(potency, 72)
            combo = QComboBox()
            combo.addItems(roll_names)
            compact_combo(combo, 230)
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

        geo = QGroupBox("Geomancy bubbles")
        geo_form = QFormLayout(geo)
        compact_form(geo_form)
        self.geo_enabled = QCheckBox("Enable Geomancy")
        self.geo_bonus = QSpinBox()
        self.geo_bonus.setRange(0, 10)
        self.geo_bonus.setPrefix("Geomancy +")
        compact_spin(self.geo_bonus)
        bubble_names = ["None", *sorted(set(buff_data.geo) | set(buff_data.geo_debuffs))]
        self.indi_combo = QComboBox()
        self.geo_combo = QComboBox()
        self.entrust_combo = QComboBox()
        for combo, prefix in ((self.indi_combo, "Indi-"), (self.geo_combo, "Geo-"),
                              (self.entrust_combo, "Entrust-")):
            combo.addItems(["None", *[prefix + name for name in bubble_names[1:]]])
            compact_combo(combo)
            combo.currentTextChanged.connect(self._clear_duplicate_bubbles)
        self.geo_potency = QSpinBox()
        self.geo_potency.setRange(0, 100)
        self.geo_potency.setValue(100)
        self.geo_potency.setSuffix("%")
        compact_spin(self.geo_potency)
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
        content = ResponsiveBuffGrid([whm, bard, corsair, geo])
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        # Job abilities are part of the active combat state, so keep their
        # controls with the other buffs instead of exposing a separate tab.
        outer.addWidget(self._quick_abilities_tab(), 1)
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

    def _apply_buff_state(self, state: dict, *, preserve_job_abilities: bool = False):
        """Apply a saved preset while suppressing intermediate recalculations.

        Profile Builder presets describe party/general buffs. They must not
        silently clear checked job abilities such as Hasso or Impetus.
        """
        active_abilities = (
            self.abilities_json.toPlainText()
            if preserve_job_abilities and hasattr(self, "abilities_json") else None
        )
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
                self.abilities_json.setPlainText(
                    active_abilities if active_abilities is not None
                    else str(state.get("abilities_json", "{}"))
                )
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

    def _lac_editor_tab(self) -> QWidget:
        tab = QWidget()
        tab.setObjectName("lacEditorTab")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(5)

        toolbar = QFrame()
        toolbar.setObjectName("lacEditorToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(7, 6, 7, 6)
        toolbar_layout.setSpacing(6)
        toolbar_layout.addWidget(QLabel("Profile"))
        self.lac_editor_job_combo = QComboBox()
        self.lac_editor_job_combo.setMinimumWidth(150)
        self.lac_editor_job_combo.setMaximumWidth(240)
        self.lac_editor_job_combo.currentTextChanged.connect(self._lac_editor_job_changed)
        toolbar_layout.addWidget(self.lac_editor_job_combo)
        self.lac_editor_path_label = QLabel("Load a character to open its current LAC file.")
        self.lac_editor_path_label.setObjectName("lacEditorPath")
        self.lac_editor_path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.lac_editor_path_label.setMinimumWidth(0)
        self.lac_editor_path_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        toolbar_layout.addWidget(self.lac_editor_path_label, 1)
        self.lac_editor_reload_button = QPushButton("Reload from disk")
        self.lac_editor_reload_button.setEnabled(False)
        self.lac_editor_reload_button.clicked.connect(
            lambda: self._refresh_lac_editor(force=True)
        )
        self.lac_editor_save_button = QPushButton("Save LAC file")
        self.lac_editor_save_button.setObjectName("lacEditorSave")
        self.lac_editor_save_button.setEnabled(False)
        self.lac_editor_save_button.clicked.connect(self.save_lac_editor)
        toolbar_layout.addWidget(self.lac_editor_reload_button)
        toolbar_layout.addWidget(self.lac_editor_save_button)
        layout.addWidget(toolbar)

        self.lac_editor = LuaCodeEditor()
        self.lac_editor.setPlaceholderText(
            "Select an imported character and LAC job profile to open the Lua source."
        )
        self.lac_editor.setEnabled(False)
        self.lac_editor.document().modificationChanged.connect(
            self._lac_editor_modified_changed
        )
        layout.addWidget(self.lac_editor, 1)
        self.lac_editor_status = QLabel(
            "Read and edit the live character profile. Saving creates a timestamped backup first."
        )
        self.lac_editor_status.setObjectName("lacEditorStatus")
        self.lac_editor_status.setWordWrap(True)
        layout.addWidget(self.lac_editor_status)

        save_action = QAction("Save LAC file", tab)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        save_action.triggered.connect(self.save_lac_editor)
        tab.addAction(save_action)
        self._lac_editor_path: Path | None = None
        self._lac_editor_source_hash = ""
        self._lac_editor_job_label = ""
        self._refresh_lac_editor_jobs(load=False)
        return tab

    def _refresh_lac_editor_jobs(self, *, load: bool = True):
        if not hasattr(self, "lac_editor_job_combo"):
            return
        jobs = [
            self.profile_job_combo.itemText(index)
            for index in range(self.profile_job_combo.count())
        ]
        preferred = self.profile_job_combo.currentText()
        self.lac_editor_job_combo.blockSignals(True)
        self.lac_editor_job_combo.clear()
        self.lac_editor_job_combo.addItems(jobs)
        if preferred in jobs:
            self.lac_editor_job_combo.setCurrentText(preferred)
        self.lac_editor_job_combo.blockSignals(False)
        self.lac_editor_job_combo.setEnabled(bool(jobs))
        if load:
            self._refresh_lac_editor()

    def _profile_job_for_editor_changed(self, job_label: str):
        if not hasattr(self, "lac_editor_job_combo"):
            return
        self.lac_editor_job_combo.blockSignals(True)
        self.lac_editor_job_combo.setCurrentText(job_label)
        self.lac_editor_job_combo.blockSignals(False)
        self._refresh_lac_editor()

    def _lac_editor_job_changed(self, job_label: str):
        if job_label and self.profile_job_combo.currentText() != job_label:
            self.profile_job_combo.setCurrentText(job_label)
        else:
            self._refresh_lac_editor()

    def _confirm_lac_editor_transition(self) -> bool:
        if not hasattr(self, "lac_editor") or not self.lac_editor.document().isModified():
            return True
        answer = QMessageBox.warning(
            self, "Unsaved LAC changes",
            "Save the current LAC changes before opening another profile?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save_lac_editor()
        if answer == QMessageBox.StandardButton.Discard:
            self.lac_editor.document().setModified(False)
            return True
        return False

    def _restore_lac_editor_job_choice(self):
        if not self._lac_editor_job_label:
            return
        self.lac_editor_job_combo.blockSignals(True)
        self.lac_editor_job_combo.setCurrentText(self._lac_editor_job_label)
        self.lac_editor_job_combo.blockSignals(False)

    def _refresh_lac_editor(self, *, force: bool = False) -> bool:
        if not hasattr(self, "lac_editor"):
            return False
        label = self.lac_editor_job_combo.currentText().strip()
        if not label or not self.bridge_store.data:
            if not self.lac_editor.document().isModified():
                self.lac_editor.clear()
                self.lac_editor.setEnabled(False)
                self.lac_editor_path_label.setText("No current LAC profile loaded")
                self.lac_editor_reload_button.setEnabled(False)
                self.lac_editor_save_button.setEnabled(False)
            return False
        job = JOBS.get(label, label).casefold()
        try:
            path = self.bridge_store.profile_path(job)
            changed_profile = self._lac_editor_path is not None and path != self._lac_editor_path
            if path == self._lac_editor_path and not force:
                return True
            if (force or changed_profile) and not self._confirm_lac_editor_transition():
                self._restore_lac_editor_job_choice()
                return False
            if not path.is_file():
                raise FileNotFoundError(f"LAC profile does not exist: {path}")
            source = path.read_text(encoding="utf-8")
            self.lac_editor.blockSignals(True)
            self.lac_editor.setPlainText(source)
            self.lac_editor.document().setModified(False)
            self.lac_editor.blockSignals(False)
            self._lac_editor_path = path
            self._lac_editor_source_hash = bridge_hash(source)
            self._lac_editor_job_label = label
            self.lac_editor.setEnabled(True)
            self.lac_editor_reload_button.setEnabled(True)
            self.lac_editor_save_button.setEnabled(False)
            self.lac_editor_path_label.setText(str(path))
            self.lac_editor_path_label.setToolTip(str(path))
            self.lac_editor_status.setText(
                f"{len(source.splitlines()):,} lines · UTF-8 · no unsaved changes"
            )
            self._set_lac_editor_tab_modified(False)
            return True
        except Exception as error:
            if not self.lac_editor.document().isModified():
                self.lac_editor.blockSignals(True)
                self.lac_editor.clear()
                self.lac_editor.document().setModified(False)
                self.lac_editor.blockSignals(False)
                self.lac_editor.setEnabled(False)
                self._lac_editor_path = None
                self._lac_editor_source_hash = ""
                self.lac_editor_path_label.setText("LAC profile unavailable")
                self._set_lac_editor_tab_modified(False)
            self.lac_editor_status.setText(str(error))
            self.lac_editor_reload_button.setEnabled(False)
            self.lac_editor_save_button.setEnabled(False)
            return False

    def _lac_editor_disk_changed(self, path: Path):
        if not hasattr(self, "lac_editor") or self._lac_editor_path != path:
            return
        if self.lac_editor.document().isModified():
            self.lac_editor_status.setText(
                "The LAC file changed on disk while this editor has unsaved changes. "
                "Save is blocked by conflict protection; review and reload first."
            )
            return
        self._refresh_lac_editor(force=True)

    def _set_lac_editor_tab_modified(self, modified: bool):
        if not hasattr(self, "tabs"):
            return
        for index in range(self.tabs.count()):
            if self.tabs.widget(index) is self.lac_editor.parentWidget():
                self.tabs.setTabText(index, "LAC Editor *" if modified else "LAC Editor")
                break

    def _lac_editor_modified_changed(self, modified: bool):
        available = self._lac_editor_path is not None and self.lac_editor.isEnabled()
        self.lac_editor_save_button.setEnabled(bool(modified and available))
        self._set_lac_editor_tab_modified(bool(modified))
        if modified:
            self.lac_editor_status.setText(
                "Unsaved changes · Ctrl+S saves atomically and creates a backup."
            )

    def save_lac_editor(self) -> bool:
        path = getattr(self, "_lac_editor_path", None)
        if path is None or not hasattr(self, "lac_editor"):
            return False
        try:
            source = self.lac_editor.toPlainText()
            backup, new_hash = write_profile_source(
                path, source, expected_hash=self._lac_editor_source_hash,
            )
            self._lac_editor_source_hash = new_hash
            self.lac_editor.document().setModified(False)
            profile = self._profile_for_job()
            if profile is not None:
                profile["source_hash"] = new_hash
            write_reload_request(self.bridge_store.bridge_path.parent, {
                "schema_version": 3,
                "character_key": (self.bridge_store.data.get("character") or {}).get("key"),
                "job": JOBS.get(self._lac_editor_job_label, self._lac_editor_job_label).casefold(),
                "profile": path.name,
                "profile_hash": new_hash,
                "set": "Manual LAC editor save",
            })
            self.lac_editor_status.setText(
                f"Saved {path.name} · backup: {backup.name}"
            )
            self.statusBar().showMessage(
                f"Saved {path.name}; backup created in {backup.parent.name}", 6000
            )
            return True
        except Exception as error:
            QMessageBox.critical(self, "Save LAC file", str(error))
            return False

    def _profile_builder_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        heading = QLabel("Create, inspect, and publish LAC starting sets")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        profile_controls = QHBoxLayout()
        self.profile_job_combo = QComboBox()
        self.profile_job_combo.currentTextChanged.connect(self._refresh_profile_jobs)
        self.profile_job_combo.currentTextChanged.connect(self.refresh_gear_readiness)
        self.profile_job_combo.currentTextChanged.connect(self._profile_builder_settings_changed)
        refresh = QPushButton("Refresh profile data")
        refresh.clicked.connect(self.refresh_bridge)
        profile_controls.addWidget(QLabel("LAC job"))
        profile_controls.addWidget(self.profile_job_combo)
        profile_controls.addWidget(refresh)
        profile_controls.addStretch(1)
        profile_help = QLabel("Base generation is quick and read-only. Combat improvement can run afterward.")
        profile_help.setWordWrap(True)
        profile_controls.addWidget(profile_help, 1)
        layout.addLayout(profile_controls)

        builder = QGroupBox("Build settings")
        builder_grid = QGridLayout(builder)
        builder_grid.setContentsMargins(6, 7, 6, 6)
        builder_grid.setHorizontalSpacing(6)
        builder_grid.setVerticalSpacing(3)
        self.profile_source_accessible = QCheckBox("Accessible owned gear")
        self.profile_source_accessible.setChecked(True)
        self.profile_source_porter = QCheckBox("Porter / stored owned gear")
        self.profile_source_porter.setChecked(True)
        self.profile_source_transferable = QCheckBox("Transferable gear")
        for checkbox in (
            self.profile_source_accessible,
            self.profile_source_porter,
            self.profile_source_transferable,
        ):
            checkbox.toggled.connect(self.refresh_gear_readiness)
            checkbox.toggled.connect(self._profile_builder_settings_changed)
        self.profile_builder_depth = QComboBox()
        self.profile_builder_depth.addItems(SEARCH_QUALITY_NAMES)
        if hasattr(self, "dashboard_search_quality"):
            self.profile_builder_depth.setCurrentText(self.dashboard_search_quality.currentText())
            self.profile_builder_depth.currentTextChanged.connect(
                lambda quality: self.dashboard_search_quality.setCurrentText(quality)
            )
        self.profile_builder_depth.setToolTip(
            "Fast: 6 searches x 4 passes. Standard: 10 x 10 with validated shared starting "
            "knowledge. Deep: 12 x 10 independent searches with exact-result reuse only."
        )
        depth_help = QPushButton("?")
        depth_help.setFixedWidth(28)
        depth_help.setToolTip("Explain Profile Builder search depth.")
        depth_help.clicked.connect(self.show_profile_builder_depth_help)
        depth_row = QHBoxLayout()
        depth_row.addWidget(self.profile_builder_depth, 1)
        depth_row.addWidget(depth_help)
        self.profile_builder_seed = QLineEdit()
        self.profile_builder_seed.setPlaceholderText("generate deterministic batch seed")
        self.profile_builder_tp = QSpinBox()
        self.profile_builder_tp.setRange(1000, 3000)
        self.profile_builder_tp.setSingleStep(1000)
        self.profile_builder_tp.setValue(1000)
        self.profile_builder_buff = QComboBox()
        self.profile_builder_buff.addItems(self._all_buff_presets().keys())
        self.profile_builder_buff.currentTextChanged.connect(self._profile_builder_settings_changed)
        self.profile_builder_depth.currentTextChanged.connect(self._profile_builder_settings_changed)
        self.profile_builder_tp.valueChanged.connect(self._profile_builder_settings_changed)
        self.profile_builder_seed.textChanged.connect(self._profile_builder_settings_changed)
        self.profile_build_button = QPushButton("Create starting sets")
        self.profile_build_button.setObjectName("primaryAction")
        self.profile_build_button.clicked.connect(self.build_complete_lac_profile)
        self.profile_optimize_all_button = QPushButton("Improve all combat sets")
        self.profile_optimize_all_button.setEnabled(False)
        self.profile_optimize_all_button.setToolTip(
            "Run the normal simulator/optimizer sequentially for every generated TP and weapon-skill section. "
            "Each section applies its own Apex enemy tier, TP target, metric, and PDT/MDT/DT floors. "
            "Direct-stat utility and defense sets are already complete after base generation."
        )
        self.profile_optimize_all_button.clicked.connect(self.optimize_all_profile_builder_sections)
        self.profile_optimize_direct_button = QPushButton("Refresh direct-stat sets")
        self.profile_optimize_direct_button.setVisible(False)
        self.profile_optimize_direct_button.clicked.connect(self.optimize_direct_profile_builder_sections)
        self.profile_stop_button = QPushButton("Stop")
        self.profile_stop_button.setEnabled(False)
        self.profile_stop_button.clicked.connect(self.stop_optimizer)
        self.profile_publish_button = QPushButton("Review changes and publish")
        self.profile_publish_button.setEnabled(False)
        self.profile_publish_button.clicked.connect(self.publish_profile_builder_result)
        self.profile_builder_status = QLabel(
            "No starting sets created."
        )
        self.profile_builder_status.setObjectName("dashboardStatus")
        self.profile_builder_status.setWordWrap(True)
        builder_grid.addWidget(self.profile_source_accessible, 0, 0)
        builder_grid.addWidget(self.profile_source_porter, 0, 1)
        builder_grid.addWidget(self.profile_source_transferable, 0, 2)
        builder_grid.addWidget(QLabel("Buff preset"), 1, 0)
        builder_grid.addWidget(self.profile_builder_buff, 2, 0)
        builder_grid.addWidget(QLabel("WS TP"), 1, 1)
        builder_grid.addWidget(self.profile_builder_tp, 2, 1)
        builder_grid.addWidget(QLabel("Combat search"), 1, 2)
        builder_grid.addLayout(depth_row, 2, 2)
        self.profile_builder_seed.setVisible(False)
        actions = QHBoxLayout()
        actions.addWidget(self.profile_build_button)
        actions.addWidget(self.profile_optimize_all_button)
        actions.addWidget(self.profile_stop_button)
        actions.addWidget(self.profile_publish_button)
        actions.addStretch(1)
        builder_grid.addLayout(actions, 3, 0, 1, 4)
        builder_grid.addWidget(self.profile_builder_status, 4, 0, 1, 4)
        for column in range(4):
            builder_grid.setColumnStretch(column, 1)
        layout.addWidget(builder)

        generated = QGroupBox("Generated set catalog")
        generated_layout = QVBoxLayout(generated)
        generated_layout.setContentsMargins(6, 7, 6, 6)
        generated_layout.setSpacing(4)
        catalog_toolbar = QHBoxLayout()
        catalog_toolbar.setSpacing(5)
        self.profile_builder_catalog_summary = QLabel("Create starting sets to populate the catalog.")
        self.profile_builder_filter = QComboBox()
        self.profile_builder_filter.addItems([
            "All sets", "Needs improvement", "Warnings", "TP", "Weapon skill", "Utility / defense",
        ])
        self.profile_builder_filter.currentTextChanged.connect(self._populate_profile_builder_results)
        catalog_toolbar.addWidget(self.profile_builder_catalog_summary, 1)
        catalog_toolbar.addWidget(QLabel("Show"))
        catalog_toolbar.addWidget(self.profile_builder_filter)
        generated_layout.addLayout(catalog_toolbar)

        catalog_splitter = ResponsiveCatalogSplitter()
        catalog_splitter.setObjectName("profileCatalogSplitter")
        self.profile_builder_table = QTableWidget(0, 4)
        self.profile_builder_table.setHorizontalHeaderLabels(["Set", "Type", "Variant", "Status"])
        self.profile_builder_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.profile_builder_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.profile_builder_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.profile_builder_table.setAlternatingRowColors(True)
        self.profile_builder_table.setWordWrap(False)
        self.profile_builder_table.setMinimumWidth(280)
        self.profile_builder_table.setMinimumHeight(190)
        self.profile_builder_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.profile_builder_table.verticalHeader().setVisible(False)
        self.profile_builder_table.verticalHeader().setDefaultSectionSize(28)
        self.profile_builder_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            self.profile_builder_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.profile_builder_table.currentCellChanged.connect(self._profile_builder_selection_changed)
        catalog_splitter.addWidget(self.profile_builder_table)

        detail = QWidget()
        detail.setMinimumWidth(300)
        detail.setMinimumHeight(240)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(6, 2, 2, 2)
        detail_layout.setSpacing(4)
        self.profile_builder_selected_title = QLabel("Select a generated set")
        self.profile_builder_selected_title.setObjectName("sectionTitle")
        self.profile_builder_selected_title.setWordWrap(True)
        self.profile_builder_selected_title.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.profile_builder_selected_optimize = QPushButton("Improve selected set")
        self.profile_builder_selected_optimize.setEnabled(False)
        self.profile_builder_selected_optimize.clicked.connect(self._optimize_selected_profile_builder_set)
        self.profile_builder_load_workspace = QPushButton("Load in Gear Workspace")
        self.profile_builder_load_workspace.setEnabled(False)
        self.profile_builder_load_workspace.clicked.connect(self.load_generated_set_into_workspace)
        detail_layout.addWidget(self.profile_builder_selected_title)
        detail_actions = QHBoxLayout()
        detail_actions.setSpacing(5)
        detail_actions.addWidget(self.profile_builder_load_workspace)
        detail_actions.addWidget(self.profile_builder_selected_optimize)
        detail_actions.addStretch(1)
        detail_layout.addLayout(detail_actions)
        self.profile_builder_selected_recipe = QLabel(
            "The selected set's objective, scenario, warnings, and equipment appear here."
        )
        self.profile_builder_selected_recipe.setObjectName("profileRecipe")
        self.profile_builder_selected_recipe.setTextFormat(Qt.TextFormat.RichText)
        self.profile_builder_selected_recipe.setWordWrap(True)
        detail_layout.addWidget(self.profile_builder_selected_recipe)
        self.profile_builder_preview = ProfileGearPreview(self)
        detail_layout.addWidget(self.profile_builder_preview)
        self.profile_builder_alternative_row = QWidget()
        alternative_row = QHBoxLayout(self.profile_builder_alternative_row)
        alternative_row.setContentsMargins(0, 0, 0, 0)
        alternative_row.setSpacing(5)
        alternative_row.addWidget(QLabel("Optimizer choice"))
        self.profile_builder_alternative_combo = QComboBox()
        self.profile_builder_alternative_combo.setEnabled(False)
        self.profile_builder_alternative_combo.setToolTip(
            "After improvement, choose one of the three best legal optimizer sets for this catalog entry."
        )
        self.profile_builder_apply_alternative = QPushButton("Apply choice")
        self.profile_builder_apply_alternative.setEnabled(False)
        self.profile_builder_apply_alternative.clicked.connect(self.apply_profile_builder_alternative)
        alternative_row.addWidget(self.profile_builder_alternative_combo, 1)
        alternative_row.addWidget(self.profile_builder_apply_alternative)
        self.profile_builder_alternative_row.setVisible(False)
        detail_layout.addWidget(self.profile_builder_alternative_row)
        self.profile_builder_selected_warning = QLabel()
        self.profile_builder_selected_warning.setObjectName("profileWarning")
        self.profile_builder_selected_warning.setWordWrap(True)
        self.profile_builder_selected_warning.setVisible(False)
        detail_layout.addWidget(self.profile_builder_selected_warning)
        detail_layout.addStretch(1)
        catalog_splitter.addWidget(detail)
        catalog_splitter.setHandleWidth(4)
        catalog_splitter.setStretchFactor(0, 0)
        catalog_splitter.setStretchFactor(1, 1)
        catalog_splitter.setSizes([430, 620])
        generated_layout.addWidget(catalog_splitter, 1)
        layout.addWidget(generated, 1)
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

    def _profile_builder_sources(self) -> GearSources:
        return GearSources(
            accessible=self.profile_source_accessible.isChecked(),
            porter=self.profile_source_porter.isChecked(),
            transferable=self.profile_source_transferable.isChecked(),
        )

    def _profile_builder_settings_changed(self, *_args):
        build = getattr(self, "_profile_builder_result", None)
        if not build or getattr(self, "_profile_builder_optimizer_active", None):
            return
        build["settings_stale"] = True
        self.profile_builder_status.setText(
            "Build settings changed. Create starting sets again before improving or publishing."
        )
        self._refresh_build_dashboard()

    def show_profile_builder_depth_help(self):
        QMessageBox.information(
            self,
            "Profile Builder search depth",
            "Fast runs six optimizer searches with four passes for each TP or WS section.\n\n"
            "Standard runs ten searches with ten passes and may use one fully validated prior winner "
            "as a starting path.\n\n"
            "Deep runs twelve independent searches with ten passes. It can reuse only exact completed "
            "calculations and never uses cross-character or cross-job starting knowledge.\n\n"
            "All modes keep the selected weapon overlay locked and manage reproducibility automatically. "
            "Direct-stat specialty recipes such as Fast Cast, SIR, DT, Evasion, and MEVA do not use combat searches.",
        )

    def _clear_profile_builder_results(self):
        self.profile_builder_table.setRowCount(0)
        self.profile_builder_selected_title.setText("Select a generated set")
        self.profile_builder_selected_recipe.setText(
            "The selected set's objective, scenario, warnings, and equipment appear here."
        )
        self.profile_builder_selected_warning.clear()
        self.profile_builder_selected_warning.setVisible(False)
        self.profile_builder_selected_optimize.setEnabled(False)
        self.profile_builder_load_workspace.setEnabled(False)
        self.profile_builder_alternative_combo.clear()
        self.profile_builder_alternative_combo.setEnabled(False)
        self.profile_builder_apply_alternative.setEnabled(False)
        self.profile_builder_alternative_row.setVisible(False)
        self.profile_builder_preview.set_gearset({})

    def _profile_builder_gear_tile(self, slot: str, item: dict) -> QWidget:
        tile = QFrame()
        tile.setFrameShape(QFrame.Shape.StyledPanel)
        tile.setToolTip(item_tooltip(item))
        tile_layout = QHBoxLayout(tile)
        tile_layout.setContentsMargins(5, 3, 5, 3)
        icon_label = QLabel()
        icon_label.setFixedSize(38, 38)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = self.icons.icon(item)
        if not icon.isNull():
            icon_label.setPixmap(icon.pixmap(QSize(36, 36)))
        tile_layout.addWidget(icon_label)
        name = QLabel(f"{slot.upper()}\n{item_name(item)}")
        name.setWordWrap(True)
        name.setToolTip(item_tooltip(item))
        tile_layout.addWidget(name, 1)
        return tile

    @staticmethod
    def _profile_builder_set_status(details: dict) -> str:
        if details.get("optimization_state") == "running":
            return "Running"
        if details.get("optimization_state") == "workspace":
            status = "Workspace edited"
        elif details.get("optimizer"):
            status = "Optimized" if details.get("optimization_state") == "optimized" else "Starting set"
        else:
            status = "Ready"
        has_warning = bool(details.get("direct_warnings")) or bool(
            (details.get("simulation_defense") or {}).get("fallback")
        )
        return f"{status} · warning" if has_warning else status

    @staticmethod
    def _profile_builder_set_label(set_name: str, details: dict) -> str:
        """Translate internal LAC identifiers into scan-friendly catalog labels."""
        section_type = str(details.get("section_type") or "Utility")
        family = str(details.get("family") or set_name).replace("_", " ")
        raw_variant = str(details.get("variant") or "Default")
        variant = {
            "Acc": "Accuracy",
            "HighAcc": "High accuracy",
            "Hybrid": "Hybrid · Default",
            "HybridAcc": "Hybrid · Accuracy",
            "HybridHighAcc": "Hybrid · High accuracy",
        }.get(raw_variant, raw_variant)
        if section_type == "TP":
            label = f"TP · {variant}"
            overlay = str(details.get("weapon_overlay") or "")
            if overlay:
                overlay = re.sub(r"^(Weapon|Gun|Range|Ranged)_?", "", overlay)
                label += f" · {overlay}"
            return label
        if section_type == "Weapon skill":
            label = f"{family} · {variant}"
            overlay = str(details.get("weapon_overlay") or "")
            if overlay:
                overlay = re.sub(r"^(Weapon|Gun|Range|Ranged)_?", "", overlay)
                label += f" · {overlay}"
            return label
        return family

    def _profile_builder_filter_match(self, details: dict, status: str) -> bool:
        selected = self.profile_builder_filter.currentText()
        section_type = str(details.get("section_type") or "Utility")
        has_warning = bool(details.get("direct_warnings")) or bool(
            (details.get("simulation_defense") or {}).get("fallback")
        )
        if selected == "Needs improvement":
            return bool(
                details.get("optimizer")
                and details.get("optimization_state") != "optimized"
            )
        if selected == "Warnings":
            return has_warning
        if selected in {"TP", "Weapon skill"}:
            return section_type == selected
        if selected == "Utility / defense":
            return section_type in {"Utility", "Defense"}
        return True

    def _populate_profile_builder_results(self, build=None):
        """Populate a scan-friendly catalog and one selected-set detail pane."""
        if not isinstance(build, dict):
            build = getattr(self, "_profile_builder_result", None) or {}
        current_item = self.profile_builder_table.item(self.profile_builder_table.currentRow(), 0)
        current_name = current_item.data(Qt.ItemDataRole.UserRole) if current_item else ""
        self.profile_builder_table.blockSignals(True)
        self.profile_builder_table.setRowCount(0)
        sets = build.get("sets") or {}
        overlays = build.get("overlay_items") or []
        details_by_name = build.get("recipe_details") or {}
        shown_names = []
        for set_name, equipment in sets.items():
            details = details_by_name.get(set_name, {})
            status = self._profile_builder_set_status(details)
            if not self._profile_builder_filter_match(details, status):
                continue
            row = self.profile_builder_table.rowCount()
            self.profile_builder_table.insertRow(row)
            values = (
                self._profile_builder_set_label(str(set_name), details),
                str(details.get("section_type") or "Utility"),
                str(details.get("variant") or "Default"),
                status,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, str(set_name))
                if status.startswith("Optimized"):
                    item.setForeground(QColor("#8cf3b2"))
                elif status.startswith("Starting") and details.get("optimizer"):
                    item.setForeground(QColor("#ffe2a8"))
                if details.get("direct_warnings"):
                    item.setToolTip("\n".join(details["direct_warnings"]))
                self.profile_builder_table.setItem(row, column, item)
            shown_names.append(str(set_name))
        self.profile_builder_table.blockSignals(False)
        warnings = len(build.get("warnings") or [])
        self.profile_builder_catalog_summary.setText(
            f"{len(sets)} sets · {len(overlays)} fixed weapon overlays · {warnings} warning(s) · "
            "automatic reproducibility"
            if sets else "Create starting sets to populate the catalog."
        )
        if shown_names:
            selected_name = current_name if current_name in shown_names else shown_names[0]
            selected_row = shown_names.index(selected_name)
            self.profile_builder_table.selectRow(selected_row)
            self.profile_builder_table.setCurrentCell(selected_row, 0)
            self._show_profile_builder_set(selected_name)
        else:
            self._clear_profile_builder_results()
            if sets:
                self.profile_builder_catalog_summary.setText(
                    f"No sets match {self.profile_builder_filter.currentText().lower()}."
                )

    def _profile_builder_selection_changed(self, current_row: int, _current_column: int,
                                           _previous_row: int, _previous_column: int):
        item = self.profile_builder_table.item(current_row, 0)
        if item:
            self._show_profile_builder_set(str(item.data(Qt.ItemDataRole.UserRole) or item.text()))

    def _show_profile_builder_set(self, set_name: str):
        build = getattr(self, "_profile_builder_result", None) or {}
        equipment = (build.get("sets") or {}).get(set_name)
        details = (build.get("recipe_details") or {}).get(set_name, {})
        if equipment is None:
            return
        optimizer_info = details.get("optimizer") or {}
        overlay = self._profile_builder_overlay_for_set(
            set_name,
            build.get("overlay_items") or [],
            str(optimizer_info.get("ws_name") or ""),
        )
        self.profile_builder_preview.set_gearset(equipment, overlay)
        status = self._profile_builder_set_status(details)
        label = self._profile_builder_set_label(set_name, details)
        self.profile_builder_selected_title.setText(f"{label} · {status}")
        priorities = list(details.get("objective") or ())
        priority_cells = []
        for index, stat in enumerate(priorities, start=1):
            priority_cells.append(
                f"<td width='28' style='color:#e6c983'><b>{index:02d}</b></td>"
                f"<td width='190' style='color:#f5f1ff'>{escape(str(stat))}</td>"
            )
        priority_rows = []
        for index in range(0, len(priority_cells), 2):
            cells = priority_cells[index:index + 2]
            if len(cells) == 1:
                cells.append("<td width='28'></td><td width='190'></td>")
            priority_rows.append("<tr>" + "".join(cells) + "</tr>")
        priority_html = (
            "<table cellspacing='0' cellpadding='1'>" + "".join(priority_rows) + "</table>"
            if priority_rows else "<span style='color:#918ca0'>No stat priorities recorded</span>"
        )
        sections = [
            "<div style='margin-bottom:5px'><b style='color:#e6c983'>STAT PRIORITY</b>"
            f"{priority_html}</div>"
        ]
        cap_results = list(details.get("cap_results") or ())
        if cap_results:
            cap_html = " &nbsp; ".join(
                f"<b>{escape(str(row.get('stat') or 'Stat'))}</b> "
                f"{float(row.get('reached') or 0):g} / {float(row.get('target') or 0):g}"
                for row in cap_results
            )
            sections.append(f"<div><b>Modeled gear:</b> {cap_html}</div>")
        if details.get("require_damage_cap"):
            sections.append(
                f"<div><b>Defense floor:</b> PDT {float(details.get('pdt_target') or 50):g}% &nbsp; "
                f"MDT {float(details.get('mdt_target') or 50):g}%</div>"
            )
        if optimizer_info:
            floors = ", ".join(
                f"{label} {int(optimizer_info.get(label.casefold(), 0) or 0)}%"
                for label in ("PDT", "MDT", "DT")
                if int(optimizer_info.get(label.casefold(), 0) or 0) > 0
            ) or "no defensive floor"
            sections.append(
                f"<div><b>Combat test:</b> {escape(str(optimizer_info.get('enemy', 'current enemy')))} &nbsp; "
                f"{int(optimizer_info.get('tp', 1000) or 1000):,} TP &nbsp; {escape(floors)}</div>"
            )
        else:
            sections.append("<div><b>Method:</b> direct stat/cap search</div>")
        if details.get("simulation_summary"):
            sections.append(f"<div><b>Result:</b> {escape(str(details['simulation_summary']))}</div>")
        if overlay:
            fixed = []
            overlay_items = overlay.get("gearset") or {}
            for slot in WEAPON_SLOTS:
                item = overlay_items.get(slot, gear.Empty)
                if item_name(item) != "Empty":
                    fixed.append(f"{slot.upper()}: {item_name(item)}")
            sections.append(
                f"<div><b>Fixed weapon layer:</b> {escape(' · '.join(fixed) or str(overlay.get('name', 'profile overlay')))}</div>"
            )
        self.profile_builder_selected_recipe.setText("".join(sections))

        warnings = list(details.get("direct_warnings") or [])
        defense = details.get("simulation_defense") or {}
        if defense.get("fallback"):
            warnings.append("Defensive minimum was unavailable; this is the best legal set found.")
        self.profile_builder_selected_warning.setText("\n".join(f"⚠ {warning}" for warning in warnings))
        self.profile_builder_selected_warning.setVisible(bool(warnings))
        self.profile_builder_selected_optimize.setProperty("set_name", set_name)
        self.profile_builder_selected_optimize.setText(
            "Improve selected set" if optimizer_info else "Refresh selected set"
        )
        running = bool(self.optimizer_thread and self.optimizer_thread.isRunning())
        self.profile_builder_selected_optimize.setEnabled(not running)
        self.profile_builder_load_workspace.setProperty("set_name", set_name)
        self.profile_builder_load_workspace.setEnabled(not running)
        alternatives = list(details.get("optimizer_alternatives") or ())[:3]
        self.profile_builder_alternative_combo.blockSignals(True)
        self.profile_builder_alternative_combo.clear()
        for index, choice in enumerate(alternatives, start=1):
            label = str(choice.get("label") or f"Optimizer set {index}")
            metric = choice.get("metric")
            if isinstance(metric, (int, float)):
                label += f" · {float(metric):,.3f}"
            self.profile_builder_alternative_combo.addItem(label, index - 1)
        self.profile_builder_alternative_combo.blockSignals(False)
        self.profile_builder_alternative_combo.setEnabled(bool(alternatives) and not running)
        self.profile_builder_apply_alternative.setProperty("set_name", set_name)
        self.profile_builder_apply_alternative.setEnabled(bool(alternatives) and not running)
        self.profile_builder_alternative_row.setVisible(bool(alternatives))

    def load_generated_set_into_workspace(self):
        set_name = str(self.profile_builder_load_workspace.property("set_name") or "")
        build = getattr(self, "_profile_builder_result", None) or {}
        equipment = (build.get("sets") or {}).get(set_name)
        details = (build.get("recipe_details") or {}).get(set_name, {})
        if not set_name or not isinstance(equipment, dict):
            return
        combined = dict(equipment)
        overlay = self._profile_builder_overlay_for_set(
            set_name, build.get("overlay_items") or [],
            str((details.get("optimizer") or {}).get("ws_name") or ""),
        )
        if overlay:
            overlay_items = overlay.get("gearset") or {}
            specified = set(overlay.get("specified_slots") or ())
            combined.update({
                slot: overlay_items[slot] for slot in WEAPON_SLOTS
                if slot in specified and slot in overlay_items
            })
        self.quick_set.set_gearset(combined)
        self._workspace_generated_set_name = set_name
        self.workspace_generated_label.setText(f"Generated set: {set_name}")
        self.workspace_update_generated_button.setEnabled(True)
        self.workspace_mode.setCurrentText("Single Set")
        self._select_tab("Gear Workspace")
        self.statusBar().showMessage(
            f"Loaded {set_name}. Edit it, then use Update generated set to return it to the catalog.",
            7000,
        )

    def update_generated_set_from_workspace(self):
        set_name = str(getattr(self, "_workspace_generated_set_name", "") or "")
        build = getattr(self, "_profile_builder_result", None) or {}
        if not set_name or set_name not in (build.get("sets") or {}):
            self.workspace_update_generated_button.setEnabled(False)
            return
        build["sets"][set_name] = {
            slot: self.quick_set.items.get(slot, gear.Empty) for slot in SET_SLOTS
        }
        details = (build.get("recipe_details") or {}).get(set_name, {})
        details["workspace_edited"] = True
        details["optimization_state"] = "workspace"
        build["published"] = False
        self._populate_profile_builder_results(build)
        self.statusBar().showMessage(f"Updated generated set {set_name} from Gear Workspace", 6000)

    def export_workspace_set_to_lac(self):
        """Review and atomically write one workspace set to the active profile."""
        profile = self._profile_for_job()
        if profile is None or not self.bridge_store.data:
            QMessageBox.information(self, "Export workspace set", "Load a character LAC profile first.")
            return
        editor = self.quick_set
        source_label = "Single Set"
        if self.workspace_mode.currentText() == "TP → WS Cycle":
            source_label, accepted = QInputDialog.getItem(
                self, "Export workspace set", "Workspace source:",
                ["TP Set", "WS Set"], 0, False,
            )
            if not accepted:
                return
            editor = self.tp_set if source_label == "TP Set" else self.ws_set
        default_name = str(getattr(self, "_workspace_generated_set_name", "") or "")
        if not default_name:
            default_name = "Tp_Default" if source_label == "TP Set" else "Ws_Default"
        set_name, accepted = QInputDialog.getText(
            self, "Export workspace set", "LAC set name:", text=default_name,
        )
        set_name = set_name.strip()
        if not accepted or not set_name:
            return
        try:
            job = str(profile.get("job") or "").upper()
            path = self.bridge_store.profile_path(job)
            source = path.read_text(encoding="utf-8")
            exported = {set_name: {slot: editor.items.get(slot, gear.Empty) for slot in SLOTS}}
            updated = prepare_managed_update(source, exported, add_wsdist_cycle=False)
            diff = "".join(difflib.unified_diff(
                source.splitlines(keepends=True), updated.splitlines(keepends=True),
                fromfile=f"{path.name} (current)", tofile=f"{path.name} ({set_name})",
            ))
            if not diff:
                QMessageBox.information(self, "Export workspace set", f"{set_name} already matches the workspace.")
                return
            if not self._confirm_profile_diff(f"Export {set_name} to LAC profile", diff):
                return
            backup, new_hash = write_managed_sets(
                path, exported, expected_hash=str(profile.get("source_hash") or ""),
                add_wsdist_cycle=False,
            )
            profile["source_hash"] = new_hash
            build = getattr(self, "_profile_builder_result", None) or {}
            if (build.get("profile") or {}) is profile:
                build["profile"]["source_hash"] = new_hash
            write_reload_request(self.bridge_store.bridge_path.parent, {
                "schema_version": 3,
                "character_key": (self.bridge_store.data.get("character") or {}).get("key"),
                "job": job, "profile": path.name, "profile_hash": new_hash,
                "set": set_name,
            })
            self._lac_editor_disk_changed(path)
            QMessageBox.information(
                self, "Export workspace set",
                f"Exported {set_name} to {path.name}.\nBackup: {backup.name}",
            )
        except Exception as error:
            QMessageBox.critical(self, "Export workspace set", str(error))

    def apply_profile_builder_alternative(self):
        set_name = str(self.profile_builder_apply_alternative.property("set_name") or "")
        build = getattr(self, "_profile_builder_result", None) or {}
        details = (build.get("recipe_details") or {}).get(set_name, {})
        alternatives = list(details.get("optimizer_alternatives") or ())
        index = int(self.profile_builder_alternative_combo.currentData() or 0)
        if not set_name or not 0 <= index < len(alternatives):
            return
        gearset = alternatives[index].get("gearset") or {}
        build["sets"][set_name] = {
            slot: gearset.get(slot, gear.Empty) for slot in SET_SLOTS
        }
        details["selected_alternative"] = index
        details["optimization_state"] = "optimized"
        build["published"] = False
        self._populate_profile_builder_results(build)
        self.statusBar().showMessage(
            f"Applied optimizer choice {index + 1} to {set_name}", 5000
        )

    def _optimize_selected_profile_builder_set(self):
        set_name = str(self.profile_builder_selected_optimize.property("set_name") or "")
        build = getattr(self, "_profile_builder_result", None) or {}
        details = (build.get("recipe_details") or {}).get(set_name, {})
        if not set_name or set_name not in (build.get("sets") or {}):
            return
        if details.get("optimizer"):
            self.run_profile_builder_section_optimizer(set_name)
        else:
            self.optimize_direct_profile_builder_sections([set_name])

    @staticmethod
    def _profile_builder_overlay_for_set(set_name: str, overlays: list[dict],
                                         ws_name: str = "") -> dict | None:
        """Select the matching fixed weapon cycle for a generated override."""
        for overlay in overlays:
            suffix = re.sub(r"^(Weapon|Gun|Range|Ranged)_?", "", str(overlay.get("name") or ""))
            if suffix and str(set_name).endswith(f"_{suffix}"):
                return overlay
        required_skill = next(
            (skill for skill, names in WS_BY_SKILL.items() if ws_name in names),
            "",
        )
        if required_skill:
            slot = "ranged" if required_skill in RANGED_WEAPON_TYPES else "main"
            matching = [
                overlay for overlay in overlays
                if str((overlay.get("gearset") or {}).get(slot, {}).get("Skill Type") or "")
                == required_skill
            ]
            if slot == "main":
                matching.sort(key=lambda overlay: item_name(
                    (overlay.get("gearset") or {}).get("ranged", gear.Empty)
                ) != "Empty")
            if matching:
                return matching[0]
        for overlay in overlays:
            category = weapon_category(overlay)
            if str(set_name).endswith(f"_{category}"):
                return overlay
        # Bridge profile sets are name-sorted, so a specialized Weapon_Bow can
        # appear before the normal melee cycle. For an unsuffixed TP/WS set,
        # prefer the first layer without an equipped ranged weapon. This maps
        # Malware's normal SAM sets to Weapon_Masamune instead of Weapon_Bow.
        for overlay in overlays:
            ranged = (overlay.get("gearset") or {}).get("ranged", gear.Empty)
            if item_name(ranged) == "Empty":
                return overlay
        return overlays[0] if overlays else None

    def _configure_profile_builder_set_for_optimizer(self, set_name: str, *, show_optimizer: bool) -> bool:
        """Load one generated combat section as an optimizer-ready starting point."""
        build = getattr(self, "_profile_builder_result", None) or {}
        equipment = (build.get("sets") or {}).get(set_name)
        details = (build.get("recipe_details") or {}).get(set_name) or {}
        optimizer_info = details.get("optimizer") or {}
        if equipment is None or not optimizer_info:
            QMessageBox.information(
                self, "Profile Builder",
                f"{set_name} is a direct-stat specialty recipe and has no combat optimizer action.",
            )
            return False
        job_code = str(build.get("job") or "").casefold()
        job_name = next((name for name, code in JOBS.items() if code == job_code), None)
        if job_name is None:
            QMessageBox.warning(self, "Profile Builder", f"Cannot map job {build.get('job')!r} to the optimizer.")
            return False
        self.main_job.setCurrentText(job_name)
        scenario = optimizer_scenario(set_name, self.profile_builder_tp.value())
        scenario.update({
            key: value for key, value in optimizer_info.items()
            if key in {"enemy", "tp", "pdt", "mdt", "dt", "metric"}
        })
        preset_name = str(build.get("buff_preset") or self.profile_builder_buff.currentText())
        preset = self._all_buff_presets().get(preset_name)
        if preset:
            self._apply_buff_state(preset, preserve_job_abilities=True)
        enemy_name = str(scenario.get("enemy") or "")
        if enemy_name in enemies.preset_enemies:
            self.enemy_combo.setCurrentText(enemy_name)
            # Saved buff presets suppress combo signals while applying their
            # own enemy. Re-apply the recipe's fixed scenario afterwards.
            self._load_enemy(enemy_name)
        self.tp_value.setValue(int(scenario.get("tp") or 1000))
        self.pdt.setValue(int(scenario.get("pdt") or 0))
        self.mdt.setValue(int(scenario.get("mdt") or 0))
        self.dt.setValue(int(scenario.get("dt") or 0))
        combined = dict(equipment)
        overlays = build.get("overlay_items") or []
        overlay = self._profile_builder_overlay_for_set(
            set_name, overlays, str(optimizer_info.get("ws_name") or "")
        )
        if overlay:
            # Armor-only generated sets intentionally inherit the first fixed
            # weapon cycle so the optimizer starts from the real profile setup.
            overlay_gearset = overlay.get("gearset") or {}
            specified_slots = set(overlay.get("specified_slots") or ())
            combined.update({
                slot: overlay_gearset[slot]
                for slot in WEAPON_SLOTS
                if slot in specified_slots and slot in overlay_gearset
            })
        self.quick_set.set_gearset(combined)
        self.select_all_candidates()
        for slot in WEAPON_SLOTS:
            item = combined.get(slot, gear.Empty)
            self.locked_gear[slot] = "" if item_name(item) == "Empty" else item_name(item)
        self._refresh_candidate_buttons()
        action = str(optimizer_info.get("action") or "")
        if action == "attack round":
            self.optimize_action.setCurrentText("Attack round")
        elif action == "weapon skill":
            self.optimize_action.setCurrentText("Weapon skill")
            ws_name = str(optimizer_info.get("ws_name") or "")
            if ws_name:
                self.ws_combo.setCurrentText(ws_name)
        self._set_combo_value(
            self.metric_combo, scenario.get("metric"), self.metric_combo.currentText()
        )
        self.seed.setText(str(build.get("seed") or ""))
        if show_optimizer:
            self._select_tab("Optimizer")
        self.statusBar().showMessage(
            f"Prepared {set_name}: {enemy_name or 'current enemy'}, {self.tp_value.value():,} TP, "
            f"PDT {self.pdt.value()}% / MDT {self.mdt.value()}% / DT {self.dt.value()}%, fixed weapon slots.", 7000,
        )
        return True

    def run_profile_builder_section_optimizer(self, set_name: str):
        if self.optimizer_thread and self.optimizer_thread.isRunning():
            QMessageBox.information(self, "Profile Builder", "Wait for the current optimizer run to finish.")
            return
        if not self._configure_profile_builder_set_for_optimizer(set_name, show_optimizer=False):
            return
        self._profile_builder_optimizer_active = str(set_name)
        self._profile_builder_optimizer_queue = []
        self._profile_builder_optimizer_total = 1
        self._profile_builder_optimizer_completed_count = 0
        self._profile_builder_optimizer_batch_state = "running"
        details = ((getattr(self, "_profile_builder_result", {}) or {}).get("recipe_details") or {}).get(set_name, {})
        details["previous_optimization_state"] = details.get("optimization_state", "base")
        details["optimization_state"] = "running"
        self._populate_profile_builder_results()
        self._refresh_profile_builder_batch_status()
        self.profile_builder_status.setText(f"Simulating {set_name} through the optimizer...")
        self.run_optimizer()

    def optimize_direct_profile_builder_sections(self, set_names: list[str] | None = None):
        """Re-run the bounded, cap-aware optimizer for non-combat recipes.

        These recipes intentionally do not call the combat simulator: their
        objectives are explicit stat/cap priorities.  Keeping this as a
        separate action makes that distinction visible while allowing a user
        to refresh all specialty sets after changing gear sources or the
        bridge catalog.
        """
        if self.optimizer_thread and self.optimizer_thread.isRunning():
            QMessageBox.information(self, "Profile Builder", "Wait for the current optimizer run to finish.")
            return
        build = getattr(self, "_profile_builder_result", None) or {}
        details_by_name = build.get("recipe_details") or {}
        requested = {str(name) for name in set_names} if set_names else None
        queue = [
            str(name) for name, details in details_by_name.items()
            if name in (build.get("sets") or {})
            and not details.get("optimizer")
            and (requested is None or str(name) in requested)
        ]
        if not queue:
            QMessageBox.information(
                self, "Profile Builder",
                "Build the profile first, or select a generated direct-stat section to optimize.",
            )
            return
        started = time.perf_counter()
        job = str(build.get("job") or "").casefold()
        sources = self._profile_builder_sources()
        candidates = bridge_candidates(self.bridge_store, job, sources)
        context = self._combat_context()
        payloads = self._profile_payloads()
        overlays = build.get("overlay_items") or []
        warnings = list(build.get("warnings") or [])
        old_direct_warnings = set()
        for name in queue:
            old_direct_warnings.update(details_by_name[name].get("direct_warnings") or [])
        warnings = [warning for warning in warnings if warning not in old_direct_warnings]
        completed = 0
        for set_name in queue:
            details = details_by_name[set_name]
            recipe = ProfileRecipe(
                set_name,
                tuple(details.get("objective") or ()),
                tuple(tuple(cap) for cap in details.get("caps") or ()),
                require_damage_cap=bool(details.get("require_damage_cap")),
                pdt_target=float(details.get("pdt_target") or 50),
                mdt_target=float(details.get("mdt_target") or 50),
            )
            pinned = pin_unmodeled_slots(payloads, set_name)
            overlay = self._profile_builder_overlay_for_set(
                set_name, overlays,
                str((details.get("optimizer") or {}).get("ws_name") or ""),
            )
            weapons = {}
            if overlay:
                overlay_items = overlay.get("gearset") or {}
                specified = set(overlay.get("specified_slots") or ())
                weapons = {
                    slot: overlay_items[slot]
                    for slot in WEAPON_SLOTS
                    if slot in specified and slot in overlay_items
                }
            built = build_stat_set(
                set_name,
                candidates,
                recipe,
                weapons=weapons,
                pinned=pinned,
                starting=(build.get("sets") or {}).get(set_name),
                buffs=context["buffs"],
                abilities=context["abilities"],
            )
            build["sets"][set_name] = built.equipment
            direct_warnings = [
                f"{set_name}: {warning}" for warning in built.warnings
            ]
            details["direct_warnings"] = direct_warnings
            details["cap_results"] = list(built.cap_results)
            details["direct_optimized"] = True
            details["optimization_state"] = "ready"
            details["direct_runtime"] = time.perf_counter() - started
            warnings.extend(direct_warnings)
            completed += 1
        build["warnings"] = warnings
        build["sources"] = sources
        build["published"] = False
        build["runtime"] = float(build.get("runtime") or 0.0) + (time.perf_counter() - started)
        self._populate_profile_builder_results(build)
        self.profile_builder_status.setText(
            f"Optimized {completed} direct-stat section(s) in {time.perf_counter() - started:.2f}s. "
            "Review the refreshed armor preview before publishing."
        )
        self.profile_publish_button.setEnabled(True)
        self.profile_optimize_direct_button.setEnabled(
            any(not details.get("optimizer") for details in details_by_name.values())
        )
        self._refresh_build_dashboard()

    def optimize_all_profile_builder_sections(self):
        if self.optimizer_thread and self.optimizer_thread.isRunning():
            QMessageBox.information(self, "Profile Builder", "Wait for the current optimizer run to finish.")
            return
        build = getattr(self, "_profile_builder_result", None) or {}
        queue = [
            str(name) for name, details in (build.get("recipe_details") or {}).items()
            if details.get("optimizer")
            and details.get("optimization_state") != "optimized"
            and name in (build.get("sets") or {})
        ]
        if not queue:
            message = (
                "Every combat set is already improved. Select one set to run it again."
                if build else "Generate base sets before improving combat sections."
            )
            QMessageBox.information(self, "Profile Builder", message)
            return
        self._profile_builder_optimizer_queue = queue
        self._profile_builder_optimizer_active = None
        self._profile_builder_optimizer_total = len(queue)
        self._profile_builder_optimizer_completed_count = 0
        self._profile_builder_optimizer_batch_state = "running"
        self._profile_builder_optimizer_batch_started_at = time.monotonic()
        self._optimizer_progress_samples = []
        self.profile_optimize_all_button.setEnabled(False)
        self.profile_optimize_direct_button.setEnabled(False)
        self.profile_stop_button.setEnabled(True)
        self._refresh_profile_builder_batch_status()
        self._start_next_profile_builder_optimizer_section()

    def _start_next_profile_builder_optimizer_section(self):
        queue = getattr(self, "_profile_builder_optimizer_queue", [])
        if not queue:
            self._profile_builder_optimizer_active = None
            self._profile_builder_optimizer_batch_state = "complete"
            self.profile_optimize_all_button.setEnabled(True)
            self.profile_optimize_direct_button.setEnabled(True)
            self.profile_stop_button.setEnabled(False)
            self.profile_builder_status.setText("All requested loadouts were optimized. Review the updated gear preview before publishing.")
            self._refresh_profile_builder_batch_status()
            return
        set_name = queue.pop(0)
        if not self._configure_profile_builder_set_for_optimizer(set_name, show_optimizer=False):
            QTimer.singleShot(0, self._start_next_profile_builder_optimizer_section)
            return
        self._profile_builder_optimizer_active = set_name
        details = ((getattr(self, "_profile_builder_result", {}) or {}).get("recipe_details") or {}).get(set_name, {})
        details["previous_optimization_state"] = details.get("optimization_state", "base")
        details["optimization_state"] = "running"
        self._populate_profile_builder_results()
        total = len(queue) + 1
        self._refresh_profile_builder_batch_status()
        self.profile_builder_status.setText(
            f"Simulating {set_name} against {self.enemy_combo.currentText()} at {self.tp_value.value():,} TP "
            f"with PDT {self.pdt.value()}% / MDT {self.mdt.value()}% / DT {self.dt.value()}% "
            f"({total} section(s) remaining)..."
        )
        self.run_optimizer()

    def _profile_builder_optimizer_completed(self, metric: float):
        """Store one real optimizer winner, then advance an optional all-section queue."""
        set_name = getattr(self, "_profile_builder_optimizer_active", None)
        if not set_name:
            return
        build = getattr(self, "_profile_builder_result", None) or {}
        player = self.best_player
        if player is not None and set_name in (build.get("sets") or {}):
            build["sets"][set_name] = {
                slot: player.gearset.get(slot, gear.Empty) for slot in SET_SLOTS
            }
            details = (build.get("recipe_details") or {}).get(set_name)
            if details is not None:
                action = str((details.get("optimizer") or {}).get("action") or "")
                ws_name = str((details.get("optimizer") or {}).get("ws_name") or "")
                details["simulation_summary"] = _optimizer_result_summary(
                    action, getattr(self, "_last_optimizer_output", None), float(metric),
                    self.metric_combo.currentText(), ws_name,
                )
                details["simulation_defense"] = _optimizer_defense_summary(player)
                alternatives = []
                for index, result in enumerate(self.optimizer_top_results[:3], start=1):
                    candidate = result.get("player")
                    if candidate is None:
                        candidate = (
                            result.get("ws_player") if action == "weapon skill"
                            else result.get("tp_player")
                        )
                    candidate_gearset = getattr(candidate, "gearset", None)
                    if not isinstance(candidate_gearset, dict):
                        continue
                    alternatives.append({
                        "label": str(result.get("label") or f"Top set {index}"),
                        "metric": result.get("metric"),
                        "seed": result.get("seed"),
                        "gearset": {
                            slot: candidate_gearset.get(slot, gear.Empty) for slot in SET_SLOTS
                        },
                    })
                details["optimizer_alternatives"] = alternatives
                details["optimization_state"] = "optimized"
                details.pop("previous_optimization_state", None)
                build["published"] = False
            self._populate_profile_builder_results(build)
        self._profile_builder_optimizer_active = None
        self._profile_builder_optimizer_completed_count = (
            int(getattr(self, "_profile_builder_optimizer_completed_count", 0)) + 1
        )
        self._refresh_profile_builder_batch_status()
        if getattr(self, "_profile_builder_optimizer_queue", []):
            QTimer.singleShot(0, self._start_next_profile_builder_optimizer_section)
        else:
            self.profile_optimize_all_button.setEnabled(True)
            self.profile_optimize_direct_button.setEnabled(True)
            self.profile_stop_button.setEnabled(False)
            self._profile_builder_optimizer_batch_state = "complete"
            self.profile_builder_status.setText(
                f"Optimizer result applied to {set_name}. Review the updated preview before publishing."
            )
            self._refresh_profile_builder_batch_status()

    def _cancel_profile_builder_optimizer_queue(self, reason: str):
        active = getattr(self, "_profile_builder_optimizer_active", None)
        if not active:
            return
        details = ((getattr(self, "_profile_builder_result", {}) or {}).get("recipe_details") or {}).get(active, {})
        if details.get("optimization_state") == "running":
            details["optimization_state"] = details.pop("previous_optimization_state", "base")
        self._profile_builder_optimizer_active = None
        self._profile_builder_optimizer_queue = []
        self._profile_builder_optimizer_batch_state = "stopped"
        self.profile_optimize_all_button.setEnabled(True)
        self.profile_optimize_direct_button.setEnabled(True)
        self.profile_stop_button.setEnabled(False)
        self.profile_builder_status.setText(f"Profile Builder optimization stopped: {reason}")
        self._populate_profile_builder_results()
        self._refresh_profile_builder_batch_status()

    def build_complete_lac_profile(self):
        """Build a deterministic owned-gear catalog before any profile write."""
        started = time.perf_counter()
        profile = self._profile_for_job()
        if profile is None or not self.bridge_store.data:
            QMessageBox.information(self, "Profile Builder", "Load a character-specific LAC profile first.")
            return False
        try:
            job = str(profile.get("job") or "").casefold()
            job_name = next((name for name, code in JOBS.items() if code == job), "")
            if job_name:
                self.main_job.setCurrentText(job_name)
            payloads = self._profile_payloads()
            sources = self._profile_builder_sources()
            candidates = bridge_candidates(self.bridge_store, job, sources)
            if not any(len(values) > 1 for values in candidates.values()):
                raise ValueError("The selected gear sources contain no modeled armor for this job.")
            seed_text = self.profile_builder_seed.text().strip()
            batch_seed = int(seed_text) if seed_text else secrets.randbits(31)
            self.profile_builder_seed.setText(str(batch_seed))
            preset = self._all_buff_presets().get(self.profile_builder_buff.currentText())
            if preset:
                self._apply_buff_state(preset, preserve_job_abilities=True)
            context = self._combat_context()
            catalog = build_profile_catalog(
                job,
                payloads,
                candidates,
                self.profile_builder_tp.value(),
                buffs=context["buffs"],
                abilities=context["abilities"],
            )
            self._profile_builder_result = {
                **catalog,
                "seed": batch_seed, "job": job.upper(), "profile": profile,
                "runtime": time.perf_counter() - started,
                "sources": sources,
                "buff_preset": self.profile_builder_buff.currentText(),
                "published": False,
            }
            self._profile_builder_optimizer_active = None
            self._profile_builder_optimizer_queue = []
            self._profile_builder_optimizer_total = 0
            self._profile_builder_optimizer_completed_count = 0
            self._profile_builder_optimizer_batch_state = ""
            self._refresh_profile_builder_batch_status()
            self._populate_profile_builder_results(self._profile_builder_result)
            overlay_text = ", ".join(name for name, _category in self._profile_builder_result["overlays"]) or "no weapon overlays"
            self.profile_builder_status.setText(
                f"Created {len(catalog['sets'])} owned-gear starting sets in "
                f"{self._profile_builder_result['runtime']:.2f}s · fixed overlays: {overlay_text}. "
                + (f"{len(catalog['warnings'])} warning(s); review highlighted sets." if catalog["warnings"] else
                   "Ready to publish or optionally improve combat sets.")
            )
            self.profile_publish_button.setEnabled(True)
            self.profile_optimize_all_button.setEnabled(
                any(details.get("optimizer") for details in catalog["recipe_details"].values())
            )
            self.profile_optimize_direct_button.setEnabled(
                any(not details.get("optimizer") for details in catalog["recipe_details"].values())
            )
            self._refresh_build_dashboard()
            return True
        except Exception as error:
            QMessageBox.critical(self, "Profile Builder", str(error))
            return False

    def publish_profile_builder_result(self):
        build = getattr(self, "_profile_builder_result", None)
        if not build:
            return
        profile = build["profile"]
        try:
            path = self.bridge_store.profile_path(build["job"])
            source = path.read_text(encoding="utf-8")
            updated = prepare_profile_builder_update(source, build["sets"])
            diff = "".join(difflib.unified_diff(
                source.splitlines(keepends=True), updated.splitlines(keepends=True),
                fromfile=f"{path.name} (current)", tofile=f"{path.name} (Profile Builder)",
            ))
            if not self._confirm_profile_diff("Publish complete Profile Builder catalog", diff):
                return
            backup, new_hash = write_profile_builder_sets(
                path, build["sets"], expected_hash=str(profile.get("source_hash") or ""),
            )
            profile["source_hash"] = new_hash
            write_reload_request(self.bridge_store.bridge_path.parent, {
                "schema_version": 3,
                "character_key": (self.bridge_store.data.get("character") or {}).get("key"),
                "job": build["job"], "profile": path.name, "profile_hash": new_hash,
                "set": "Profile Builder managed catalog", "seed": build["seed"],
            })
            self._lac_editor_disk_changed(path)
            QMessageBox.information(
                self, "Profile Builder",
                f"Published {len(build['sets'])} managed armor sets to {path.name}.\nBackup: {backup.name}",
            )
            self.profile_publish_button.setEnabled(False)
            build["published"] = True
            self._refresh_build_dashboard()
        except Exception as error:
            QMessageBox.critical(self, "Profile Builder", str(error))

    def _profile_for_job(self) -> dict | None:
        selected = self.profile_job_combo.currentText().casefold()
        selected_code = JOBS.get(self.profile_job_combo.currentText(), self.profile_job_combo.currentText()).casefold()
        for profile in self.bridge_store.profile_records():
            job = str(profile.get("job", ""))
            if job.casefold() in {selected, selected_code}:
                return profile
        return None

    def _refresh_profile_jobs(self):
        """Refresh the Profile Builder job selector from bridge profiles."""
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
        if hasattr(self, "lac_editor_job_combo"):
            self._refresh_lac_editor_jobs()

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

    def _combat_context(self) -> dict:
        structured, debuffs = self._structured_buffs()
        custom_buffs = self._json_object(self.buffs_json, "Additional buffs") if hasattr(self, "buffs_json") else {}
        abilities = self._json_object(self.abilities_json, "Abilities") if hasattr(self, "abilities_json") else {}
        buffs = self._merge_buff_sources(structured, custom_buffs)
        abilities["Aftermath"] = self.aftermath.value()
        abilities.setdefault("Enhancing Skill", self.enhancing_skill.value())
        abilities.setdefault("Storm spell", self.storm_combo.currentText() if self.whm_enabled.isChecked() else "None")
        abilities.setdefault("Enemy Resist Rank", "100%")
        abilities.setdefault("99999", False)
        main_job_name = self.main_job.currentText()
        main_job = JOBS[main_job_name]
        master_level = self.master_level.value()
        return {
            "main_job": main_job,
            "sub_job": JOBS.get(self.sub_job.currentText(), "None"),
            "master_level": master_level, "buffs": buffs,
            "abilities": abilities,
            "enemy": {name: spin.value() for name, spin in self.enemy_spins.items()},
            "debuffs": debuffs, "tp_value": self.tp_value.value(),
        }

    def items_for_slot(self, slot: str) -> list[dict]:
        job = JOBS[self.main_job.currentText()]
        result, seen = [], set()
        items = [gear.Empty, *self.equipment.get(slot, [])]
        items.extend(
            item for item in self.shared_catalog.values()
            if slot in item.get("Slots", ())
        )
        for item in items:
            if slot == "ear1" and is_right_ear_only(item):
                continue
            jobs = [str(value).lower() for value in item.get("Jobs", gear.all_jobs)]
            name = item_name(item)
            if job not in jobs or name in seen or _blacklist_matches(item, self.gear_blacklist):
                continue
            seen.add(name)
            result.append(item)
        return result

    def aspirational_items_for_slot(self, slot: str) -> list[dict]:
        """Return opted-in unowned models for optimizer use only."""
        result = []
        for name in sorted(self.aspirational_selected, key=str.casefold):
            record = self.aspirational_catalog.get(name)
            if record is None or slot not in record["slots"]:
                continue
            item = copy.deepcopy(record["item"])
            item["Aspirational Only"] = True
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
        # Transferable gear can be equipped from a normal picker; opted-in
        # aspirational gear is candidate-only and never enters Quick Look.
        items = [*self.items_for_slot(slot), *self.porter_items_for_slot(slot),
                 *self.aspirational_items_for_slot(slot)]
        items = [
            item for item in items
            if not _blacklist_matches(item, self.gear_blacklist)
        ]
        unique = {}
        for item in items:
            unique.setdefault(item_name(item), item)
        items = list(unique.values())
        if slot == "ear1":
            items = [item for item in items if not is_right_ear_only(item)]
        if not getattr(self, "exclude_under_119", None) or not self.exclude_under_119.isChecked():
            return items
        return [
            item for item in items
            if _item_level_candidate_allowed(slot, self._item_level(item), True)
        ]

    def blacklisted_optimizer_items_for_slot(self, slot: str) -> list[dict]:
        """Return applicable blocked gear for the picker's read-only footer."""
        job = JOBS[self.main_job.currentText()]
        items = [*self.equipment.get(slot, [])]
        items.extend(
            item for item in self.shared_catalog.values()
            if slot in item.get("Slots", ())
        )
        items.extend(self.porter_items_for_slot(slot))
        items.extend(self.aspirational_items_for_slot(slot))
        result = []
        seen = set()
        for item in items:
            name = item_name(item)
            jobs = [str(value).lower() for value in item.get("Jobs", gear.all_jobs)]
            if (
                name in seen
                or job not in jobs
                or (slot == "ear1" and is_right_ear_only(item))
                or not _blacklist_matches(item, self.gear_blacklist)
            ):
                continue
            seen.add(name)
            result.append(item)
        return sorted(result, key=lambda item: item_name(item).casefold())

    def porter_items_for_slot(self, slot: str) -> list[dict]:
        """Return selected-job Porter inventory as optimizer-only candidates."""
        if slot not in SET_SLOTS or not self.bridge_store.data:
            return []
        if hasattr(self, "profile_source_porter") and not self.profile_source_porter.isChecked():
            return []
        job = JOBS[self.main_job.currentText()]
        pools = bridge_candidates(
            self.bridge_store, job,
            GearSources(accessible=False, porter=True, transferable=False),
        )
        result = []
        accessible_names = {item_name(item) for item in self.items_for_slot(slot)}
        for source_item in pools.get(slot, ()):
            if item_name(source_item) == "Empty" or item_name(source_item) in accessible_names:
                continue
            item = copy.deepcopy(source_item)
            item["Porter Only"] = True
            result.append(item)
        return result

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
        self.statusBar().showMessage(
            "Deselected candidates below item level 119 in head, body, hands, legs, and feet.",
            5000,
        )

    def _shared_gear_changed(self, enabled: bool):
        self._refresh_shared_gear()
        self._refresh_locked_gear_options()
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
            return
        if not self.bridge_store.bridge_path or not self.bridge_store.ashita_root:
            return
        current_path = self.bridge_store.bridge_path.resolve()
        known = {item_name(item) for values in self.equipment.values() for item in values}
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
                if name in known:
                    continue
                shared = copy.deepcopy(item)
                shared["Shared Only"] = True
                shared["Shared Characters"] = [label]
                self.shared_catalog[name] = shared
                known.add(name)

    def _capture_candidate_state(self) -> dict:
        return {
            "exclude_under_119": bool(self.exclude_under_119.isChecked()),
            "include_shared_gear": bool(self.include_shared_gear.isChecked()),
            "aspirational": sorted(self.aspirational_selected),
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
        aspirational = state.get("aspirational", [])
        self.aspirational_selected = {
            str(name) for name in aspirational
            if isinstance(name, str) and name in self.aspirational_catalog
        } if isinstance(aspirational, list) else set()
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
        self._refresh_aspirational_table()
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
            blacklisted_items=self.blacklisted_optimizer_items_for_slot(slot),
        )
        dialog.blacklist_requested.connect(
            lambda name: self.set_optimizer_item_blacklisted(name, True)
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
        button = self.candidate_buttons[slot]
        locked_name = str(self.locked_gear.get(slot) or "")
        button.setProperty("locked", bool(locked_name))
        button.setText(
            f"LOCKED · {locked_name}" if locked_name
            else f"{len(self.candidates[slot])} selected"
        )
        if hasattr(self, "candidate_cards"):
            card = self.candidate_cards[slot]
            label = self.candidate_slot_labels[slot]
            card.setProperty("locked", bool(locked_name))
            label.setProperty("locked", bool(locked_name))
            label.setText(f"{slot.upper()} · LOCKED" if locked_name else slot.upper())
            for widget in (card, label, button):
                widget.style().unpolish(widget)
                widget.style().polish(widget)
        if hasattr(self, "candidate_detail_labels"):
            player_item = self.quick_set.items.get(slot, gear.Empty)
            button.setIcon(self.icons.icon(player_item))
            detail_label = self.candidate_detail_labels[slot]
            detail = f"Player: {item_name(player_item)}"
            width = max(150, detail_label.width() - 4)
            detail_label.setText(QFontMetrics(detail_label.font()).elidedText(
                detail, Qt.TextElideMode.ElideRight, width
            ))
            tooltip = item_tooltip(player_item)
            if locked_name:
                tooltip += f"\nLOCKED: {locked_name}"
            detail_label.setToolTip(tooltip)
            button.setToolTip("Choose optimizer candidates for this slot.\n" + tooltip)

    def _refresh_candidate_buttons(self):
        for slot in SLOTS:
            self._update_candidate_button(slot)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "candidate_buttons"):
            QTimer.singleShot(0, self._refresh_candidate_buttons)

    def eventFilter(self, watched, event):
        if (
            watched is getattr(self, "optimizer_status_dialog", None)
            and event.type() == QEvent.Type.Resize
            and hasattr(self, "_optimizer_run_cards")
        ):
            QTimer.singleShot(0, self._reflow_optimizer_run_cards)
        return super().eventFilter(watched, event)

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
        self._sort_character_choices()
        self.character_combo.setEnabled(bool(characters))
        previous = self.settings.value("character", "", str)
        if previous in self.character_paths:
            self.character_combo.setCurrentText(previous)
        self.character_combo.blockSignals(False)
        self._refresh_favorite_character_button()
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
            self._profile_builder_result = None
            self._profile_builder_optimizer_active = None
            self._profile_builder_optimizer_queue = []
            self._profile_builder_optimizer_total = 0
            self._profile_builder_optimizer_completed_count = 0
            self._profile_builder_optimizer_batch_state = ""
            if hasattr(self, "profile_builder_table"):
                self._clear_profile_builder_results()
                self.profile_builder_catalog_summary.setText("Create starting sets to populate the catalog.")
                self.profile_builder_status.setText("No starting sets created for this refreshed profile.")
            self._last_completed_optimizer_action = ""
            self.icons.set_bridge_icon_dir(path.parent / "icons32")
            self.icons.set_bridge_icon_dirs(
                character_path.parent / "icons32" for character_path in self.character_paths.values()
            )
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
            self._refresh_profile_jobs()
            self._apply_bridge_master_level()
            self._active_character_key = character_key
            self._load_character_state(character_key)
            self._refresh_aspirational_table()
            self.refresh_quick_stats()
            self.refresh_results_history()
            self._refresh_favorite_character_button()
            self._refresh_weapon_favorites()
            self.refresh_gear_readiness()
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
            "version": 2,
            "player": {
                "main_job": self.main_job.currentText(),
                "sub_job": self.sub_job.currentText(),
                "master_level": self.master_level.value(),
                "hoxne_mastery_rank": self.hoxne_mastery_rank.value(),
                "tp_value": self.tp_value.value(),
                "aftermath": self.aftermath.value(),
                "weapon_type": self.weapon_type_combo.currentText(),
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
                "tradeoff_depth": self.tradeoff_depth.currentText(),
                "substat_stats": [combo.currentText() for combo in self.substat_combos],
                "pdt": self.pdt.value(), "mdt": self.mdt.value(), "dt": self.dt.value(),
                "combined_defense_both": self.combined_defense_both.isChecked(),
                "quality": self.optimizer_quality.currentText(),
                "passes": self.optimizer_passes.value(),
                "restarts": self.restarts.value(), "workers": self.workers.value(),
                "parallel_mode": self.parallel_mode.currentText(), "seed": self.seed.text(),
                "prune_candidates": self.prune_candidates.isChecked(),
                "candidates": self._capture_candidate_state(),
            },
            "simulation": {
                "plot_dps": self.plot_dps_checkbox.isChecked(),
                "workspace_mode": self.workspace_mode.currentText(),
                "workspace_seed": self.workspace_seed.text(),
            },
            "report": {
                "job": self.profile_job_combo.currentText(),
                "builder_accessible": self.profile_source_accessible.isChecked(),
                "builder_porter": self.profile_source_porter.isChecked(),
                "builder_transferable": self.profile_source_transferable.isChecked(),
                "builder_buff": self.profile_builder_buff.currentText(),
                "builder_tp": self.profile_builder_tp.value(),
                "builder_depth": self.profile_builder_depth.currentText(),
                "builder_seed": self.profile_builder_seed.text(),
            },
            "tab": self.tabs.currentIndex(),
            "tab_name": self._current_tab_name(),
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
        legacy_search_names = int(state.get("version", 1) or 1) < 2
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
        self._set_combo_value(self.weapon_type_combo, player.get("weapon_type"), AUTO_WEAPON_TYPE)
        self._refresh_ws_choices()
        self._set_combo_value(self.ws_combo, player.get("weapon_skill"), "None")
        self._set_combo_value(self.spell_combo, player.get("spell"), "None")

        optimizer = state.get("optimizer") if isinstance(state.get("optimizer"), dict) else {}
        saved_action = optimizer.get("action")
        # Saved profiles predate the renamed user-facing mode.
        if saved_action == "Sub-stat optimization":
            saved_action = "Tradeoff optimization"
        self._set_combo_value(self.optimize_action, saved_action, self.optimize_action.currentText())
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
        saved_tradeoff = optimizer.get("tradeoff_depth")
        if legacy_search_names and saved_tradeoff == "Deep":
            saved_tradeoff = "Standard"
        self._set_combo_value(self.tradeoff_depth, saved_tradeoff, "Fast")
        saved_substats = optimizer.get("substat_stats")
        if isinstance(saved_substats, list):
            for combo, value in zip(self.substat_combos, saved_substats):
                self._set_combo_value(combo, value, "None")
        self._set_combo_value(self.parallel_mode, optimizer.get("parallel_mode"), self.parallel_mode.currentText())
        saved_quality = _normalized_search_quality(
            optimizer.get("quality", "Fast"), legacy_deep=legacy_search_names
        )
        self.optimizer_quality.setCurrentText(saved_quality)
        self._apply_optimizer_quality(saved_quality)
        for control, key in ((self.pdt, "pdt"), (self.mdt, "mdt"), (self.dt, "dt"), (self.restarts, "restarts"), (self.optimizer_passes, "passes"), (self.workers, "workers")):
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
            aspirational = candidate_state.get("aspirational", [])
            self.aspirational_selected = {
                str(name) for name in aspirational
                if isinstance(name, str) and name in self.aspirational_catalog
            } if isinstance(aspirational, list) else set()
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
        self._refresh_aspirational_table()
        simulation = state.get("simulation") if isinstance(state.get("simulation"), dict) else {}
        self.plot_dps_checkbox.setChecked(bool(simulation.get("plot_dps", False)))
        self._set_combo_value(self.workspace_mode, simulation.get("workspace_mode"), "Single Set")
        self.workspace_seed.setText(str(simulation.get("workspace_seed", "")))

        report = state.get("report") if isinstance(state.get("report"), dict) else {}
        self._set_combo_value(self.profile_job_combo, report.get("job"), self.profile_job_combo.currentText())
        self._refresh_profile_jobs()
        self.profile_source_accessible.setChecked(bool(report.get("builder_accessible", True)))
        self.profile_source_porter.setChecked(bool(report.get("builder_porter", True)))
        self.profile_source_transferable.setChecked(bool(report.get("builder_transferable", False)))
        self._set_combo_value(self.profile_builder_buff, report.get("builder_buff"), self.profile_builder_buff.currentText())
        builder_depth = _normalized_search_quality(
            report.get("builder_depth", "Fast"), legacy_deep=legacy_search_names
        )
        self._set_combo_value(self.profile_builder_depth, builder_depth, "Fast")
        try:
            self.profile_builder_tp.setValue(int(report.get("builder_tp", self.profile_builder_tp.value())))
        except (TypeError, ValueError):
            pass
        self.profile_builder_seed.setText(str(report.get("builder_seed", "")))
        tab_name = str(state.get("tab_name") or "").strip()
        if tab_name:
            if tab_name in {"Quick Look", "TP / WS Sets", "TP / WS Sets (legacy)"}:
                self.workspace_mode.setCurrentText(
                    "TP → WS Cycle" if "TP / WS" in tab_name else "Single Set"
                )
                self._select_tab("Gear Workspace")
                return
            self._select_tab(tab_name)
        else:
            # Preserve pre-specialized-tab saved layouts by translating the
            # old tab positions to their new names.
            # Fallback for state saved before tab-name persistence.  Named
            # tabs above take precedence and need no positional translation.
            old_to_new = {0: 0, 1: 2, 2: 3, 3: 4, 4: 5, 5: 7, 6: 6, 7: 8, 8: 8, 9: 8, 10: 9, 11: 10}
            try:
                old_index = int(state.get("tab", 0))
                if 7 <= old_index <= 9:
                    self.calculator_tabs.setCurrentIndex(old_index - 7)
                self.tabs.setCurrentIndex(old_to_new.get(old_index, 0))
            except (TypeError, ValueError):
                self.tabs.setCurrentIndex(0)

    def closeEvent(self, event):
        if (
            hasattr(self, "lac_editor")
            and self.lac_editor.document().isModified()
            and not self._confirm_lac_editor_transition()
        ):
            event.ignore()
            return
        thread_candidates = [
            thread for thread in (
                self.optimizer_thread, self.overnight_thread,
                getattr(self, "simulation_thread", None),
                getattr(self, "plot_thread", None),
                getattr(self, "quick_distribution_thread", None),
            )
        ]
        thread_candidates.extend(getattr(self, "_quick_distribution_threads", ()))
        running_threads = []
        seen_threads = set()
        for thread in thread_candidates:
            if thread is None or id(thread) in seen_threads or not thread.isRunning():
                continue
            seen_threads.add(id(thread))
            running_threads.append(thread)
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
        self._refresh_magic_damage_spell_choices()
        self._refresh_self_buff_variants()
        self._apply_bridge_master_level()
        self._rebuild_quick_ability_controls()
        self._refresh_aspirational_table()
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
        self._refresh_aspirational_table()
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
        selected_type = self.weapon_type_combo.currentText() if hasattr(self, "weapon_type_combo") else AUTO_WEAPON_TYPE
        values = weapon_skill_choices(selected_type, self.quick_set.items)
        skills = values[1:]
        self.ws_combo.clear()
        self.ws_combo.addItems(values)
        if current in values:
            self.ws_combo.setCurrentText(current)
        elif skills:
            self.ws_combo.setCurrentIndex(1)
        else:
            self.ws_combo.setCurrentIndex(0)

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
        context = self._combat_context()
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

    def _reference_enemy_cases(self, enabled: bool = True) -> list[tuple[str, object]]:
        """Build the Profile Builder reference enemies with the active debuffs."""
        if not enabled:
            return []
        context = self._combat_context()
        current_name = self.enemy_combo.currentText().strip()
        cases = []
        for name in _reference_enemy_names(current_name, True):
            if name == current_name:
                continue
            preset = enemies.preset_enemies.get(name)
            if preset is not None:
                cases.append((name, _report_enemy(dict(preset), context["debuffs"])))
        return cases

    def _cache_lookup(self, kind: str, request: dict) -> tuple[str | None, dict | None]:
        """Return a saved deterministic result only while caching is enabled."""
        if not self.cache_enabled:
            return None, None
        key = self.simulation_cache.key_for(kind, request)
        return key, self.simulation_cache.get(key, kind)

    def _cache_store(self, active: dict | None, payload: dict, runtime_seconds: float):
        if active is None or not self.cache_enabled:
            return
        if self.simulation_cache.put(
            active["key"], active["kind"], payload, runtime_seconds,
            request_summary=active.get("request_summary"), batch_id=active.get("batch_id", ""),
        ):
            self._refresh_cache_status()

    def _saved_work_choice(self) -> str:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Previous calculation available")
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setText("Matching optimizer work is already saved.")
        dialog.setInformativeText(
            "Reuse it immediately, or explore fresh independent search paths?"
        )
        reuse = dialog.addButton("Reuse saved work", QMessageBox.ButtonRole.AcceptRole)
        fresh = dialog.addButton("Explore fresh paths", QMessageBox.ButtonRole.ActionRole)
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(reuse)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is reuse:
            return "reuse"
        if clicked is fresh:
            return "fresh"
        return "cancel"

    @staticmethod
    def _cache_age_text(created_at: float) -> str:
        return MainWindow._format_duration(max(0, time.time() - created_at)) + " ago"

    @staticmethod
    def _optimizer_cache_context(args: tuple) -> dict:
        return {
            "main_job": args[0], "sub_job": args[1], "master_level": args[2],
            "buffs": args[3], "abilities": args[4],
        }

    def _optimizer_cache_request(self, mode: str, args: tuple, kwargs: dict) -> dict:
        """Fingerprint only the effective post-filter/post-prune engine inputs."""
        semantic_kwargs = dict(kwargs)
        if semantic_kwargs.get("parallel_mode", "search_runs") != "single_run":
            semantic_kwargs.pop("workers", None)
        return {
            "operation": mode,
            "engine_args": args,
            "engine_kwargs": semantic_kwargs,
        }

    @staticmethod
    def _restart_cache_request(args: tuple, kwargs: dict, index: int, seed: int,
                               warm_starting_gearset: dict | None = None) -> dict:
        semantic_kwargs = {
            key: value for key, value in kwargs.items()
            if key not in {
                "workers", "restarts", "seed", "return_details", "return_top_results",
                "cached_restarts", "restart_callback", "progress_callback",
                "progress_queue", "stop_event", "warm_starting_gearset",
            }
        }
        return {
            "operation": "optimizer-restart",
            "engine_args": args,
            "engine_kwargs": semantic_kwargs,
            "index": int(index),
            "seed": int(seed),
            "warm_start": _gearset_payload(warm_starting_gearset) if warm_starting_gearset else None,
        }

    @staticmethod
    def _serialize_restart_result(result: dict, warm_start: bool = False) -> dict:
        saved = _serialize_top_results([result])[0]
        saved["warm_start"] = bool(warm_start)
        saved.pop("log", None)
        return saved

    @staticmethod
    def _restore_restart_result(payload: dict, context: dict) -> dict:
        restored = _restore_top_results([payload], context)[0]
        restored.setdefault("log", "")
        return restored

    @staticmethod
    def _item_cache_identity(item: dict) -> str:
        return canonical_json(item if isinstance(item, dict) else gear.Empty)

    def _validated_shared_optimizer_start(self, check_gear: dict, starting_gearset: dict) -> dict | None:
        """Return one fully legal saved winner; partial or approximate sets are rejected."""
        candidate_lookup = {
            slot: {
                self._item_cache_identity(item): item
                for item in check_gear.get(slot, ())
                if isinstance(item, dict)
            }
            for slot in SLOTS
        }
        expected_weapons = {
            slot: self._item_cache_identity(starting_gearset.get(slot, gear.Empty))
            for slot in WEAPON_SLOTS
        }
        records = self.result_history.list("", include_all_characters=True)
        for record in records:
            if record.get("kind") != "optimizer" or record.get("stale") or record.get("corrupt"):
                continue
            saved = ((record.get("payload") or {}).get("gearsets") or {}).get("single")
            if not isinstance(saved, dict):
                continue
            resolved = {}
            valid = True
            for slot in SLOTS:
                identity = self._item_cache_identity(saved.get(slot, gear.Empty))
                if slot in WEAPON_SLOTS and identity != expected_weapons[slot]:
                    valid = False
                    break
                item = candidate_lookup.get(slot, {}).get(identity)
                if item is None:
                    valid = False
                    break
                resolved[slot] = item
            if valid:
                return resolved
        return None

    @staticmethod
    def _optimizer_cache_summary(mode: str, args: tuple, kwargs: dict) -> dict:
        """Small human-readable metadata for cache inspection, not cache identity."""
        return {
            "mode": mode, "job": f"{args[0]}/{args[1]}",
            "action": str(args[8]) if len(args) > 8 else mode,
            "tp": int(args[9]) if len(args) > 9 and isinstance(args[9], (int, float)) else None,
            "seed": kwargs.get("seed"), "search_mode": kwargs.get("search_mode", ""),
        }

    def _serialize_optimizer_payload(self, result, *, ranking: bool = False) -> dict:
        if ranking:
            saved = dict(result)
            rankings = {}
            for tp_value, rows in (result.get("rankings") or {}).items():
                rankings[str(tp_value)] = _serialize_top_results(list(rows))
            saved["rankings"] = rankings
            return saved
        substat_summary = result[5] if isinstance(result, (tuple, list)) and len(result) > 5 else []
        return {
            "player": _serialize_optimizer_player(result[0]),
            "output": result[1], "metric": result[2], "seed": result[3],
            "top_results": _serialize_top_results(list(result[4] or [])),
            "substat_summary": substat_summary or [],
        }

    def _restore_optimizer_payload(self, payload: dict, context: dict, *, ranking: bool = False):
        if ranking:
            restored = dict(payload)
            restored["rankings"] = {
                int(tp_value): _restore_top_results(rows, context)
                for tp_value, rows in (payload.get("rankings") or {}).items()
            }
            return restored
        return (
            _cached_player(payload["player"], context), payload["output"], payload["metric"], payload["seed"],
            _restore_top_results(payload.get("top_results") or [], context),
            payload.get("substat_summary") or [],
        )

    def _ws_type(self) -> str:
        selected_type = self.weapon_type_combo.currentText()
        if selected_type in RANGED_WEAPON_TYPES:
            return "ranged"
        if selected_type != AUTO_WEAPON_TYPE:
            return "melee"
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

    def _refresh_magic_damage_spell_choices(self, *_args):
        if not hasattr(self, "magic_damage_spell_combo"):
            return
        current = self.magic_damage_spell_combo.currentText()
        values = magic_damage_spell_choices(self.magic_damage_type_combo.currentText())
        self.magic_damage_spell_combo.blockSignals(True)
        self.magic_damage_spell_combo.clear()
        self.magic_damage_spell_combo.addItems(values)
        if current in values:
            self.magic_damage_spell_combo.setCurrentText(current)
        elif len(values) > 1:
            self.magic_damage_spell_combo.setCurrentIndex(1)
        else:
            self.magic_damage_spell_combo.setCurrentIndex(0)
        self.magic_damage_spell_combo.blockSignals(False)

    def evaluate_magic_damage(self):
        spell_name = self.magic_damage_spell_combo.currentText().strip()
        spell_type = self.magic_damage_type_combo.currentText()
        if not spell_name or spell_name == "None":
            QMessageBox.information(self, "Magic Damage", "Choose a modeled spell or ranged action first.")
            return
        try:
            player, enemy, _buffs, _abilities = self._context()
            metric = self.magic_damage_metric_combo.currentText()
            output = actions.cast_spell(player, enemy, spell_name, spell_type, metric)
            self.magic_damage_result.setText(
                f"{spell_name} ({spell_type}) · {metric}: {float(output[0]):,.1f} · "
                f"TP return: {float(output[1][1]):,.1f}"
            )
            stats = player.stats
            self.magic_damage_breakdown.setPlainText("\n".join((
                f"Formula: {spell_type}",
                f"Player: {player.main_job.upper()}/{player.sub_job.upper()}",
                f"Magic Accuracy: {float(stats.get('Magic Accuracy', 0)):,.0f}",
                f"Magic Attack: {float(stats.get('Magic Attack', 0)):,.0f}",
                f"Magic Damage: {float(stats.get('Magic Damage', 0)):,.0f}",
                f"INT / MND: {float(stats.get('INT', 0)):,.0f} / {float(stats.get('MND', 0)):,.0f}",
                f"Enemy Magic Evasion / Defense: {float(enemy.stats.get('Magic Evasion', 0)):,.0f} / "
                f"{float(enemy.stats.get('Magic Defense', 0)):,.0f}",
                f"Enemy Magic Damage Taken: {float(enemy.stats.get('Magic Damage Taken', 0)):,.1f}%",
                "",
                "This result uses the same spell formula as Quick Look and the optimizer.",
            )))
        except Exception as error:
            QMessageBox.critical(self, "Magic Damage calculation failed", str(error))

    def evaluate_enfeebling_magic(self):
        spell_name = self.enfeebling_spell_combo.currentText().strip()
        if not spell_name:
            return
        try:
            player, enemy, _buffs, _abilities = self._context()
            stats = player.stats
            skill = float(
                stats.get("Enfeebling Magic Skill", stats.get("Enfeebling Skill", 0))
            )
            magic_accuracy = float(stats.get("Magic Accuracy", 0)) + skill
            enemy_meva = float(enemy.stats.get("Magic Evasion", 0))
            hit_rate = float(actions.get_magic_hit_rate(magic_accuracy, enemy_meva))
            resist_coefficient = float(actions.get_resist_state_average(hit_rate))
            self.enfeebling_result.setText(
                f"{spell_name} · estimated magic hit rate: {hit_rate * 100:.1f}% · "
                f"average resist coefficient: {resist_coefficient:.3f}"
            )
            self.enfeebling_breakdown.setPlainText("\n".join((
                f"Spell: {spell_name}",
                f"Magic Accuracy: {magic_accuracy:,.0f}",
                f"  Gear / traits Magic Accuracy: {float(stats.get('Magic Accuracy', 0)):,.0f}",
                f"  Enfeebling skill contribution: {skill:,.0f}",
                f"Target Magic Evasion: {enemy_meva:,.0f}",
                f"Accuracy margin: {magic_accuracy - enemy_meva:+,.0f}",
                f"Estimated hit rate: {hit_rate * 100:.1f}% (engine cap 95%)",
                f"Average resist coefficient: {resist_coefficient:.3f}",
                "",
                "Duration, potency, immunities, and spell-specific resistance are not yet modeled.",
            )))
        except Exception as error:
            QMessageBox.critical(self, "Enfeebling calculation failed", str(error))

    def evaluate(self, action: str):
        try:
            player, enemy, _buffs, _abilities = self._context()
            spell_name = self.spell_combo.currentText()
            spell_type = self._spell_type(spell_name)
            request = _quick_cache_request(
                action, player.gearset,
                main_job=player.main_job, sub_job=player.sub_job,
                master_level=player.master_level, buffs=player.buffs,
                abilities=player.abilities, enemy=enemy.stats,
                tp=self.tp_value.value(), ws_name=self.ws_combo.currentText(),
                ws_type=self._ws_type(), spell_name=spell_name,
                spell_type=spell_type,
            )
            cache_key = self.simulation_cache.key_for("quick-look", request)
            cached = self._quick_lookup_cache.get(cache_key)
            if cached is not None:
                self._quick_lookup_cache.move_to_end(cache_key)
                saved = cached
                self.result_label.setText(str(saved["text"]))
                self._render_quick_result_graph(action, saved.get("output"))
                self._render_quick_stats(player, enemy)
                self._last_quick_result = {
                    "action": action, "output": _json_value(saved.get("output")),
                    "text": str(saved.get("text", "")), "gearset": _gearset_payload(player.gearset),
                    "scenario": self._history_scenario(
                        action=action, ws_name=self.ws_combo.currentText(), tp=self.tp_value.value(),
                    ),
                }
                if action == "ws":
                    self._start_quick_ws_distribution(player, enemy)
                elif action == "attack":
                    self._start_quick_dps_comparison(player, enemy)
                self.statusBar().showMessage("Quick Look restored from this session's cache", 4000)
                return
            started_at = time.monotonic()
            output, text = _evaluate_quick_result(
                player, enemy, action, tp=self.tp_value.value(),
                ws_name=self.ws_combo.currentText(), ws_type=self._ws_type(),
                spell_name=spell_name, spell_type=spell_type,
            )
            self.result_label.setText(text)
            self._render_quick_result_graph(action, output)
            self._render_quick_stats(player, enemy)
            self._last_quick_result = {
                "action": action, "output": _json_value(output), "text": text,
                "gearset": _gearset_payload(player.gearset),
                "scenario": self._history_scenario(
                    action=action, ws_name=self.ws_combo.currentText(), tp=self.tp_value.value(),
                ),
            }
            if action == "ws":
                self._start_quick_ws_distribution(player, enemy)
            elif action == "attack":
                self._start_quick_dps_comparison(player, enemy)
            self._quick_lookup_cache[cache_key] = {"text": text, "output": output}
            self._quick_lookup_cache.move_to_end(cache_key)
            while len(self._quick_lookup_cache) > self._quick_lookup_cache_limit:
                self._quick_lookup_cache.popitem(last=False)
        except Exception as error:
            QMessageBox.critical(self, "Evaluation failed", str(error))

    def _quick_reference_toggle(self, enabled: bool):
        """Rebuild the active Quick Look graph when reference comparison changes."""
        action = str((self._last_quick_result or {}).get("action") or "")
        if action not in {"attack", "ws"}:
            return
        try:
            player, enemy, _buffs, _abilities = self._context()
            if action == "attack":
                self._start_quick_dps_comparison(player, enemy)
            else:
                self._start_quick_ws_distribution(player, enemy)
        except Exception as error:
            self.statusBar().showMessage(f"Reference graph update failed: {error}", 5000)

    def _start_quick_dps_comparison(self, player, enemy):
        """Run the two-hour cycle for Quick Look's active and reference enemies."""
        if self._last_quick_result is not None:
            self._last_quick_result.pop("dps_summary", None)
        previous = getattr(self, "quick_dps_thread", None)
        if previous is not None and previous.isRunning():
            previous.request_stop()
        ws_name = self.ws_combo.currentText().strip()
        if not ws_name or ws_name == "None":
            self._render_quick_result_graph("attack", self._last_quick_result.get("output") if self._last_quick_result else None)
            return
        thread = SimulationThread(
            player, player, enemy, self.tp_value.value(), ws_name, self._ws_type(),
            self._simulation_seed(),
            reference_enemies=self._reference_enemy_cases(
                self.quick_reference_checkbox.isChecked()
            ),
            parent=self,
        )
        self.quick_dps_thread = thread
        thread.completed.connect(
            lambda summary, worker=thread: self._quick_dps_comparison_done(worker, summary)
        )
        thread.failed.connect(
            lambda message, worker=thread: self._quick_dps_comparison_failed(worker, message)
        )
        thread.stopped.connect(
            lambda message, worker=thread: self._quick_dps_comparison_failed(worker, message)
        )
        self.statusBar().showMessage("Building two-hour DPS comparison graph...", 4000)
        self._render_quick_result_graph(
            "attack", self._last_quick_result.get("output") if self._last_quick_result else None
        )
        thread.start()

    def _quick_dps_comparison_done(self, worker, summary: dict):
        if worker is not getattr(self, "quick_dps_thread", None):
            return
        if not self._render_dps_comparison_graph(
            self.quick_result_figure, self.quick_result_canvas, summary,
            self.enemy_combo.currentText(), title="Two-hour DPS comparison",
        ):
            self._quick_dps_comparison_failed(worker, "The DPS comparison returned no valid series.")
            return
        if self._last_quick_result is not None:
            self._last_quick_result["dps_summary"] = _json_value(summary)
        self.statusBar().showMessage("Two-hour DPS comparison graph updated", 5000)

    def _quick_dps_comparison_failed(self, worker, message: str):
        if worker is not getattr(self, "quick_dps_thread", None):
            return
        self._render_quick_result_graph(
            "attack", self._last_quick_result.get("output") if self._last_quick_result else None
        )
        self.statusBar().showMessage(f"DPS comparison failed: {message}", 5000)

    def _start_quick_ws_distribution(self, player, enemy):
        """Replace the deterministic WS bars with the requested 20k sample graph."""
        previous = getattr(self, "quick_distribution_thread", None)
        if previous is not None and previous.isRunning():
            previous.request_stop()
        ws_name = self.ws_combo.currentText().strip()
        if not ws_name or ws_name == "None":
            return
        seed = self._simulation_seed()
        thread = PlotThread(
            player, enemy, ws_name, self.tp_value.value(), self._ws_type(),
            samples=20000, seed=seed,
            reference_enemies=self._reference_enemy_cases(
                self.quick_reference_checkbox.isChecked()
            ), parent=self,
        )
        self.quick_distribution_thread = thread
        self._quick_distribution_threads.append(thread)
        thread.finished.connect(
            lambda worker=thread: self._discard_quick_distribution_thread(worker)
        )
        thread.completed.connect(
            lambda distribution, worker=thread: self._quick_ws_distribution_done(worker, distribution)
        )
        thread.failed.connect(
            lambda message, worker=thread: self._quick_ws_distribution_failed(worker, message)
        )
        thread.stopped.connect(
            lambda _message, worker=thread: self._quick_ws_distribution_stopped(worker)
        )
        self.result_label.setText(
            f"Sampling 20,000 {ws_name} results for the damage distribution..."
        )
        thread.start()

    def _discard_quick_distribution_thread(self, worker: PlotThread):
        try:
            self._quick_distribution_threads.remove(worker)
        except ValueError:
            pass

    def _quick_ws_distribution_done(self, worker: PlotThread, distribution: dict):
        if worker is not self.quick_distribution_thread:
            return
        if not self._render_ws_distribution_graph(
            self.quick_result_figure, self.quick_result_canvas, distribution,
            ws_name=worker.ws_name,
        ):
            self._quick_ws_distribution_failed(worker, "The sampled histogram was invalid.")
            return
        self.result_label.setText(
            f"20,000 samples - mean {float(distribution['mean']):,.0f} - "
            f"median {float(distribution['median']):,.0f} - "
            f"90% range {float(distribution['p05']):,.0f}-{float(distribution['p95']):,.0f}"
        )
        if self._last_quick_result is not None:
            self._last_quick_result["seed"] = worker.seed
            self._last_quick_result["distribution"] = _json_value(distribution)
        self.statusBar().showMessage("20,000-sample WS distribution completed", 5000)

    def _quick_ws_distribution_failed(self, worker: PlotThread, message: str):
        if worker is not self.quick_distribution_thread:
            return
        self.result_label.setText(f"WS distribution failed: {message}")
        self._render_quick_result_graph("", None)
        self.statusBar().showMessage("WS distribution failed", 5000)

    def _quick_ws_distribution_stopped(self, worker: PlotThread):
        if worker is self.quick_distribution_thread:
            self._render_quick_result_graph("", None)

    def save_quick_result(self):
        """Persist the last inline evaluation only when the user asks."""
        if not self._last_quick_result:
            QMessageBox.information(self, "Save Quick Look", "Evaluate a single set first.")
            return
        saved = self._last_quick_result
        action = str(saved.get("action") or "quick")
        dps_summary = saved.get("dps_summary") or {}
        self._add_history(
            "quick-look", f"Quick Look · {action}", {
                "seed": saved.get("seed"),
                "scenario": saved.get("scenario") or {},
                "metrics": {
                    "output": saved.get("output"), "text": saved.get("text", ""),
                    **{
                        key: dps_summary.get(key)
                        for key in ("total_dps", "tp_dps", "ws_dps")
                        if key in dps_summary
                    },
                },
                "dps_series": dps_summary.get("dps_series") or {},
                "reference_summaries": dps_summary.get("reference_summaries") or {},
                "distribution": saved.get("distribution") or {},
                "gearsets": {"single": saved.get("gearset") or {}},
            },
        )
        self.statusBar().showMessage("Quick Look result saved to Results", 5000)

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
            action_label = self.optimize_action.currentText()
            warm_cache_mode = action_label == WARM_CACHE_ACTION
            ranking_mode = action_label in {"Rank weapon-type WS", WARM_CACHE_ACTION}
            # Ranking deliberately fixes the currently equipped weapon layer;
            # it does not require that layer to appear in the accessible armor
            # picker (for example a profile-owned or Porter-resolved weapon).
            if ranking_mode:
                empty_weapon_slots = []
            if empty_weapon_slots:
                labels = ", ".join(slot.upper() for slot in empty_weapon_slots)
                raise ValueError(
                    f"No optimizer candidates are selected for {labels}. "
                    "Select a weapon or explicitly select Empty; the current Quick Look weapon will not be added automatically."
            )
            substat_mode = action_label in {"Tradeoff optimization", "Sub-stat optimization"}
            if warm_cache_mode and not self.cache_enabled:
                raise ValueError("Enable simulation caching from File before running Warm cache.")
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
            seed = int(seed_text) if seed_text else secrets.randbits(31)
            self.seed.setText(str(seed))
            cache_seed_note = ""
            action_type = {
                "Weapon skill": "weapon skill", "Attack round": "attack round",
                "Spell": "spell cast",
                "Combined TP + WS": "combined tp/ws",
                "Rank weapon-type WS": "rank weapon skills",
                WARM_CACHE_ACTION: "rank weapon skills",
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
                    "search_mode": (
                        "fast" if self.optimizer_quality.currentText() == "Fast" else "deep"
                    ),
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
            kwargs["n_iter"] = self.optimizer_passes.value()
            profile_builder_active = bool(getattr(self, "_profile_builder_optimizer_active", None))
            profile_builder_search_note = ""
            if profile_builder_active and not ranking_mode:
                depth = self.profile_builder_depth.currentText()
                passes, restarts, _shared = _search_quality_settings(depth)
                kwargs.update({"n_iter": passes, "restarts": restarts})
                profile_builder_search_note = (
                    f"Profile Builder {depth}: {restarts} search run(s) × {passes} passes."
                )
            cache_kind = "ws-ranking" if ranking_mode else "optimizer"
            cache_context = self._optimizer_cache_context(args)
            cache_request = self._optimizer_cache_request(
                cache_kind, args, kwargs,
            )
            cache_key, cached = (None, None)
            cache_key, cached = self._cache_lookup(cache_kind, cache_request)
            fresh_search = False
            if cached is not None and not profile_builder_active:
                saved_choice = self._saved_work_choice()
                if saved_choice == "cancel":
                    return
                if saved_choice == "fresh":
                    fresh_search = True
                    seed = secrets.randbits(31)
                    self.seed.setText(str(seed))
                    kwargs["seed"] = seed
                    cache_request = self._optimizer_cache_request(cache_kind, args, kwargs)
                    cache_key = self.simulation_cache.key_for(cache_kind, cache_request)
                    cached = None
            self._active_optimizer_cache = None
            if cached is not None:
                self._clear_optimizer_log()
                self._optimizer_run_state = {}
                self._optimizer_started_at = None
                self._initialize_optimizer_run_cards(1)
                self._ranking_skill_in_progress = ranking_skill if ranking_mode else None
                self._optimizer_action_in_progress = action_label
                self.show_optimizer_status()
                restored = self._restore_optimizer_payload(
                    cached["payload"], cache_context, ranking=ranking_mode
                )
                if ranking_mode:
                    self._ws_ranking_done(restored)
                else:
                    self._optimizer_done(restored)
                self._append_optimizer_log(
                    f"Restored from cache ({self._cache_age_text(cached['created_at'])}; "
                    f"saved runtime {self._format_duration(cached['runtime_seconds'])})."
                )
                self.optimizer_activity.setText("Restored from cache")
                self._set_optimizer_run_ui(
                    "restored", "Result restored from the simulation cache.", 100.0
                )
                self.statusBar().showMessage("Optimizer result restored from cache", 6000)
                return
            restart_cache_eligible = bool(
                self.cache_enabled and not ranking_mode and not substat_mode
                and action_type != "combined tp/ws" and parallel_mode == "search_runs"
            )
            if restart_cache_eligible:
                quality = (
                    self.profile_builder_depth.currentText()
                    if profile_builder_active else self.optimizer_quality.currentText()
                )
                if quality in SEARCH_QUALITY:
                    _passes, restart_count, allow_shared = _search_quality_settings(quality)
                else:
                    restart_count = int(kwargs["restarts"])
                    allow_shared = False
                warm_start = None
                if allow_shared and not fresh_search:
                    warm_start = self._validated_shared_optimizer_start(check_gear, args[11])
                restart_seeds = [
                    int(value) for value in np.random.SeedSequence(seed).generate_state(restart_count)
                ]
                cached_restarts = []
                restart_requests = {}
                for index, restart_seed in enumerate(restart_seeds, start=1):
                    is_warm = bool(warm_start is not None and index == restart_count)
                    request = self._restart_cache_request(
                        args, kwargs, index, restart_seed, warm_start if is_warm else None
                    )
                    restart_requests[index] = (request, is_warm)
                    restart_key = self.simulation_cache.key_for("optimizer-restart", request)
                    saved_restart = self.simulation_cache.get(restart_key, "optimizer-restart")
                    if saved_restart is not None:
                        cached_restarts.append(
                            self._restore_restart_result(saved_restart["payload"], cache_context)
                        )
                if cached_restarts and not profile_builder_active and not fresh_search:
                    saved_choice = self._saved_work_choice()
                    if saved_choice == "cancel":
                        return
                    if saved_choice == "fresh":
                        fresh_search = True
                        seed = secrets.randbits(31)
                        self.seed.setText(str(seed))
                        kwargs["seed"] = seed
                        cached_restarts = []
                        warm_start = None
                        restart_seeds = [
                            int(value) for value in np.random.SeedSequence(seed).generate_state(restart_count)
                        ]
                        restart_requests = {
                            index: (
                                self._restart_cache_request(args, kwargs, index, restart_seed), False
                            )
                            for index, restart_seed in enumerate(restart_seeds, start=1)
                        }
                        cache_request = self._optimizer_cache_request(cache_kind, args, kwargs)
                        cache_key = self.simulation_cache.key_for(cache_kind, cache_request)

                def store_restart(result, warm_used=False):
                    request, expected_warm = restart_requests[int(result["index"])]
                    if bool(warm_used) != bool(expected_warm):
                        return
                    restart_key = self.simulation_cache.key_for("optimizer-restart", request)
                    self.simulation_cache.put(
                        restart_key, "optimizer-restart",
                        self._serialize_restart_result(result, bool(warm_used)), 0.0,
                        request_summary={
                            "mode": "optimizer restart", "job": f"{args[0]}/{args[1]}",
                            "index": int(result["index"]), "quality": quality,
                        },
                    )

                kwargs["cached_restarts"] = cached_restarts
                kwargs["restart_callback"] = store_restart
                kwargs["warm_starting_gearset"] = warm_start
            if cache_key is not None:
                self._active_optimizer_cache = {
                    "kind": cache_kind, "key": cache_key, "context": cache_context,
                    "ranking": ranking_mode,
                    "request_summary": self._optimizer_cache_summary(cache_kind, args, kwargs),
                }
            checks = wsdist.estimate_candidate_checks(
                check_gear, JOBS[self.main_job.currentText()], optimizer_ws_type
            )
            self._clear_optimizer_log()
            self.optimizer_top_results = []
            self._optimizer_run_state = {}
            self._optimizer_started_at = time.monotonic()
            batch_started = getattr(self, "_profile_builder_optimizer_batch_started_at", None)
            if profile_builder_active and batch_started:
                self._optimizer_eta_started_at = float(batch_started)
            else:
                self._optimizer_eta_started_at = self._optimizer_started_at
                self._optimizer_progress_samples = []
            run_count = 1 if ranking_mode or kwargs["parallel_mode"] == "single_run" else kwargs["restarts"]
            self._initialize_optimizer_run_cards(run_count)
            self.show_optimizer_status()
            self._optimizer_status_timer.start()
            self._set_optimizer_header_eta(status="calculating...")
            self._set_optimizer_gear_results_enabled(False)
            self.optimizer_progress_value.setText("Overall progress: 0.0% · 100.0% remaining")
            self.optimizer_eta_value.setText("Estimated time remaining: calculating…")
            self.optimizer_best_value.setText("Best metric: —")
            self.optimizer_phase_value.setText("Current phase: starting")
            self._append_optimizer_log(
                f"Starting optimizer · ~{checks:,} candidates per pass"
            )
            self.optimizer_activity.setText("Starting…")
            self._set_optimizer_run_ui(
                "starting",
                f"{action_label} · preparing approximately {checks:,} candidates per pass.",
            )
            if cache_seed_note:
                self._append_optimizer_log(cache_seed_note)
            if profile_builder_search_note:
                self._append_optimizer_log(profile_builder_search_note)
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
                    else wsdist.optimize_tradeoffs if substat_mode
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
            if self._dashboard_ws_ranking_active:
                self._dashboard_ws_ranking_active = False
                self.dashboard_ws_status.setText(f"WS ranking could not start: {error}")
                self._refresh_build_dashboard()
            QMessageBox.critical(self, "Optimizer", str(error))

    def _build_overnight_tasks(self, scenario_names: list[str] | None = None) -> list[dict]:
        """Build cache tasks for selected enemies, sets, and one-item variants."""
        context = self._combat_context()
        task_context = {
            "main_job": context["main_job"], "sub_job": context["sub_job"],
            "master_level": context["master_level"],
            "buffs": copy.deepcopy(context["buffs"]),
            "abilities": copy.deepcopy(context["abilities"]),
        }
        current_enemy_name = self.enemy_combo.currentText().strip()
        scenario_names = list(scenario_names or [current_enemy_name])
        scenarios = []
        for scenario_name in scenario_names:
            if scenario_name == current_enemy_name:
                scenario_enemy = _report_enemy(context["enemy"], context["debuffs"])
            else:
                preset = enemies.preset_enemies.get(scenario_name)
                if not preset:
                    continue
                scenario_enemy = _report_enemy(dict(preset), context["debuffs"])
            scenarios.append((scenario_name, dict(scenario_enemy.stats)))
        if not scenarios:
            raise ValueError("Select at least one valid overnight enemy scenario.")
        tp_values = (1000, 2000, 3000)
        spell_name = self.spell_combo.currentText().strip()
        selected_ws = self.ws_combo.currentText().strip()
        ranged_ws = set(WS_BY_SKILL.get("Marksmanship", []) + WS_BY_SKILL.get("Archery", []))
        candidate_items = {
            slot: {
                item_name(item): item
                for item in self.optimizer_items_for_slot(slot)
                if item_name(item) in self.candidates.get(slot, set())
            }
            for slot in SLOTS
        }
        base_sets = (
            ("Quick Look", self.quick_set.items),
            ("TP set", self.tp_set.items),
            ("WS set", self.ws_set.items),
        )
        tasks = []
        seen = set()

        def add_task(role, gearset, action, tp, *, enemy_stats, ws_name="", ws_type="", spell_type=""):
            request = _quick_cache_request(
                action, gearset,
                main_job=task_context["main_job"],
                sub_job=task_context["sub_job"],
                master_level=task_context["master_level"],
                buffs=task_context["buffs"], abilities=task_context["abilities"],
                enemy=enemy_stats, tp=tp, ws_name=ws_name, ws_type=ws_type,
                spell_name=spell_name if action == "spell" else "",
                spell_type=spell_type,
            )
            key = self.simulation_cache.key_for("quick-look", request)
            if key in seen:
                return
            seen.add(key)
            tasks.append({
                "kind": "quick-look", "request": request, "context": task_context,
                "enemy": enemy_stats, "gearset": request["gearset"],
                "role": role,
                "action": action, "tp": tp, "ws_name": ws_name,
                "ws_type": ws_type, "spell_name": spell_name,
                "spell_type": spell_type,
            })

        for scenario_name, enemy_stats in scenarios:
            for role, base in base_sets:
                variants = [dict(base)]
                for slot in SLOTS:
                    base_name = item_name(base[slot])
                    for name, item in candidate_items[slot].items():
                        if name == base_name:
                            continue
                        variant = dict(base)
                        variant[slot] = item
                        variants.append(variant)
                for variant in variants:
                    main_skill = variant["main"].get("Skill Type", "None")
                    ws_names = [
                        name for name in WS_BY_SKILL.get(main_skill, ())
                        if name and name != "None"
                    ]
                    if selected_ws and selected_ws in ws_names:
                        ws_names = [selected_ws, *[name for name in ws_names if name != selected_ws]]
                    elif selected_ws and not ws_names:
                        ws_names = [selected_ws]
                    task_role = f"{scenario_name} / {role}"
                    for tp in tp_values:
                        add_task(task_role, variant, "attack", tp, enemy_stats=enemy_stats)
                    for ws_name in ws_names:
                        ws_type = "ranged" if ws_name in ranged_ws else "melee"
                        for tp in tp_values:
                            add_task(
                                task_role, variant, "ws", tp,
                                enemy_stats=enemy_stats, ws_name=ws_name, ws_type=ws_type,
                            )
                    if spell_name and spell_name != "None":
                        add_task(
                            task_role, variant, "spell", 0,
                            enemy_stats=enemy_stats,
                            spell_type=self._spell_type(spell_name),
                        )
        return tasks

    def run_overnight_simulations(self):
        if self.optimizer_thread and self.optimizer_thread.isRunning():
            QMessageBox.information(self, "Background work", "Stop the optimizer before starting overnight simulations.")
            return
        if self.overnight_thread and self.overnight_thread.isRunning():
            return
        if not self.cache_enabled:
            QMessageBox.information(
                self, "Simulation cache disabled",
                "Enable simulation caching from File before warming the cache.",
            )
            return
        enemy_names = list(enemies.preset_enemies)
        current_enemy = self.enemy_combo.currentText().strip()
        saved_scenarios = self.settings.value("overnight_simulations/enemies", [], list)
        if not isinstance(saved_scenarios, list):
            saved_scenarios = []
        saved_scenarios = [str(name) for name in saved_scenarios if str(name) in enemy_names]
        if current_enemy not in saved_scenarios:
            saved_scenarios.insert(0, current_enemy)
        scenario_dialog = OvernightScenarioDialog(
            enemy_names, current_enemy, saved_scenarios, self
        )
        if scenario_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        scenario_names = scenario_dialog.selected_names()
        if not scenario_names:
            QMessageBox.information(
                self, "Overnight simulations", "Select at least one enemy scenario."
            )
            return
        default_hours = self.settings.value("overnight_simulations/hours", 8.0, float)
        hours, accepted = QInputDialog.getDouble(
            self, "Run overnight simulations",
            "Time budget (hours):\nThe queue stops when this budget expires or all selected tasks finish.",
            max(0.1, float(default_hours)), 0.1, 72.0, 1,
        )
        if not accepted:
            return
        try:
            tasks = self._build_overnight_tasks(scenario_names)
        except Exception as error:
            QMessageBox.critical(self, "Overnight simulations", str(error))
            return
        if not tasks:
            QMessageBox.information(self, "Overnight simulations", "No simulation tasks were generated.")
            return
        self.settings.setValue("overnight_simulations/hours", hours)
        self.settings.setValue("overnight_simulations/enemies", scenario_names)
        self._clear_optimizer_log()
        self._optimizer_run_state = {}
        self._optimizer_started_at = time.monotonic()
        self._optimizer_eta_started_at = self._optimizer_started_at
        self._optimizer_progress_samples = []
        self._optimizer_action_in_progress = "Overnight simulations"
        self._initialize_optimizer_run_cards(1)
        self.show_optimizer_status()
        self._optimizer_status_timer.start()
        self._set_optimizer_header_eta(status="calculating...")
        self._append_optimizer_log(
            f"Starting overnight cache queue: {len(tasks):,} deterministic tasks across "
            f"{len(scenario_names)} enemy scenario(s); "
            f"time budget {hours:g} hour(s)."
        )
        self.optimizer_activity.setText("Warming cache")
        self._set_optimizer_run_ui(
            "warming", f"Cache simulation running · 0/{len(tasks):,} tasks processed.", 0.0
        )
        self.optimize_button.setEnabled(False)
        self.overnight_button.setEnabled(False)
        self.stop_overnight_button.setEnabled(True)
        self.overnight_thread = OvernightSimulationThread(
            tasks, self.simulation_cache, hours, self
        )
        self.overnight_thread.progress.connect(self._overnight_progress)
        self.overnight_thread.succeeded.connect(self._overnight_done)
        self.overnight_thread.failed.connect(self._overnight_failed)
        self.overnight_thread.stopped.connect(self._overnight_stopped)
        self.overnight_thread.start()

    def _overnight_progress(self, summary: dict):
        planned = max(1, int(summary.get("planned", 0)))
        processed = int(summary.get("processed", 0))
        fraction = min(0.98, processed / planned)
        state = self._optimizer_run_state.setdefault(1, {"index": 1, "total": 1})
        state.update({
            "phase": "warming cache", "fraction": fraction,
            "planned": planned, "tested": processed,
            "updated": time.monotonic(),
            "results": (
                f"Stored {summary.get('stored', 0):,}; already cached "
                f"{summary.get('cached', 0):,}; failed {summary.get('failed', 0):,}."
            ),
        })
        self.optimizer_activity.setText("Warming cache")
        self._set_optimizer_run_ui(
            "warming",
            f"Cache simulation running · {processed:,}/{planned:,} tasks processed.",
            fraction * 100,
        )
        self._refresh_optimizer_status()
        self._append_optimizer_log(
            f"Cache queue {processed:,}/{planned:,}: stored {summary.get('stored', 0):,}, "
            f"cached {summary.get('cached', 0):,}, failed {summary.get('failed', 0):,}."
        )

    def _finish_overnight_ui(self, label: str):
        self._optimizer_status_timer.stop()
        self.optimize_button.setEnabled(True)
        self.overnight_button.setEnabled(True)
        self.stop_overnight_button.setEnabled(False)
        self.optimizer_activity.setText(label)
        state = {"Completed": "completed", "Failed": "failed", "Stopped": "stopped"}.get(
            label, "idle"
        )
        self._set_optimizer_run_ui(
            state,
            f"Cache simulation {label.casefold()}.",
            100.0 if state == "completed" else None,
        )
        self._refresh_cache_status()

    def _overnight_done(self, summary: dict):
        for state in self._optimizer_run_state.values():
            state["phase"] = "completed"
            state["fraction"] = 1.0
        self._refresh_optimizer_status()
        self._append_optimizer_log(
            f"Overnight cache complete: stored {summary.get('stored', 0):,}; "
            f"already cached {summary.get('cached', 0):,}; failed {summary.get('failed', 0):,}; "
            f"elapsed {self._format_duration(summary.get('elapsed', 0))}."
        )
        self._finish_overnight_ui("Completed")
        self._set_optimizer_header_eta(status="complete")
        self.optimizer_progress_value.setText("Overall progress: 100.0% · 0.0% remaining")
        self.optimizer_eta_value.setText("Estimated time remaining: complete")
        self.optimizer_phase_value.setText("Current phase: finished")
        self.statusBar().showMessage("Overnight cache queue completed", 6000)
        self._schedule_optimizer_status_close()

    def _overnight_failed(self, message: str):
        for state in self._optimizer_run_state.values():
            state["phase"] = "failed"
        self._refresh_optimizer_status()
        self._append_optimizer_log(f"Overnight cache failed: {message}")
        self._finish_overnight_ui("Failed")
        QMessageBox.critical(self, "Overnight simulations", message)

    def _overnight_stopped(self, summary: dict):
        for state in self._optimizer_run_state.values():
            state["phase"] = "stopped"
        self._refresh_optimizer_status()
        self._append_optimizer_log(
            f"Overnight cache stopped: processed {summary.get('processed', 0):,}/"
            f"{summary.get('planned', 0):,}; stored {summary.get('stored', 0):,}."
        )
        self._finish_overnight_ui("Stopped")
        self.statusBar().showMessage("Overnight cache queue stopped", 5000)

    def stop_overnight_simulations(self):
        if self.overnight_thread and self.overnight_thread.isRunning():
            self.overnight_thread.request_stop()
            self.stop_overnight_button.setEnabled(False)
            self.optimizer_activity.setText("Stopping...")
            self._set_optimizer_run_ui(
                "stopping", "Stop requested · finishing the current cache task."
            )
            self._append_optimizer_log("Stop requested; finishing the current cache task...")

    def _append_optimizer_log(self, message: str):
        """Append a readable, color-coded optimizer status line."""
        self._optimizer_log_messages.append(str(message))
        # Keep the live window responsive during large profile-builder runs.
        if len(self._optimizer_log_messages) > 2000:
            del self._optimizer_log_messages[:-2000]
        self._render_optimizer_log()

    def _clear_optimizer_log(self):
        self._optimizer_log_messages.clear()
        if hasattr(self, "optimizer_log"):
            self.optimizer_log.clear()

    def _render_optimizer_log(self, *_args):
        """Render the searchable live log while preserving run colors."""
        if not hasattr(self, "optimizer_log"):
            return
        query = self.optimizer_log_filter.text().strip().casefold() if hasattr(self, "optimizer_log_filter") else ""
        lines = []
        for message in self._optimizer_log_messages:
            if query and query not in message.casefold():
                continue
            match = re.search(r"Search run (\d+)", message)
            lowered = message.casefold()
            if match:
                color = OPTIMIZER_RUN_COLORS[(int(match.group(1)) - 1) % len(OPTIMIZER_RUN_COLORS)]
                if "failed" in lowered or "error" in lowered:
                    color = "#c58d91"
                elif "stopped" in lowered or "stop requested" in lowered:
                    color = "#d6ad68"
            elif "failed" in lowered or "error" in lowered:
                color = "#c58d91"
            elif "stopped" in lowered or "stop requested" in lowered:
                color = "#d6ad68"
            elif "completed" in lowered or "selected search run" in lowered:
                color = "#b7c5ab"
            else:
                color = "#d0ccd6"
            lines.append(f"<span style='color:{color}'>{escape(message)}</span>")
        self.optimizer_log.setHtml("<br>".join(lines))
        self.optimizer_log.verticalScrollBar().setValue(self.optimizer_log.verticalScrollBar().maximum())

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
            card.setObjectName("optimizerRunCard")
            card.setFrameShape(QFrame.Shape.StyledPanel)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            card.setStyleSheet(
                "QFrame#optimizerRunCard { border: 1px solid #85818f; border-radius: 3px; "
                "background: #35353d; }"
            )
            grid = QGridLayout(card)
            grid.setContentsMargins(6, 5, 6, 5)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(4)
            title = QLabel(f"Search run {index}/{run_count}")
            title.setStyleSheet(
                f"font-size: 10pt; font-weight: 700; color: {OPTIMIZER_RUN_COLORS[(index - 1) % len(OPTIMIZER_RUN_COLORS)]};"
            )
            phase = QLabel("Queued")
            phase.setWordWrap(True)
            detail = QLabel("Waiting for a worker update.")
            detail.setWordWrap(True)
            detail.setMaximumHeight(32)
            result_panel = QFrame()
            result_panel.setObjectName("optimizerCurrentResult")
            result_layout = QVBoxLayout(result_panel)
            result_layout.setContentsMargins(6, 3, 6, 4)
            result_layout.setSpacing(1)
            results = QLabel(_optimizer_current_result_lines(None))
            results.setObjectName("optimizerCurrentResultValues")
            results.setWordWrap(True)
            result_layout.addWidget(results)
            detail.setStyleSheet("color: #dedbe2;")
            for label in (phase, detail, results):
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(title, 0, 0)
            grid.addWidget(phase, 0, 1)
            grid.addWidget(detail, 1, 0, 1, 2)
            grid.addWidget(result_panel, 2, 0, 1, 2)
            self._optimizer_run_cards[index] = {
                "card": card, "title": title, "phase": phase, "detail": detail,
                "results": results, "result_panel": result_panel,
            }
        self._reflow_optimizer_run_cards()

    def _reflow_optimizer_run_cards(self):
        """Lay worker cards out at the current status-window breakpoint."""
        if not hasattr(self, "optimizer_runs_layout"):
            return
        while self.optimizer_runs_layout.count():
            self.optimizer_runs_layout.takeAt(0)
        cards = [
            value["card"] for _index, value in sorted(self._optimizer_run_cards.items())
        ]
        count = len(cards)
        if not count:
            return
        width = self.optimizer_runs_box.contentsRect().width()
        # Three columns fit comfortably in the normal 960px status dialog and
        # reduce Deep's twelve cards to four rows.  Fall back to two/one on
        # narrow windows so the card text remains readable.
        columns = min(count, 3 if width >= 900 else 2 if width >= 620 else 1)
        for column in range(3):
            self.optimizer_runs_layout.setColumnStretch(column, 1 if column < columns else 0)
        for index, card in enumerate(cards):
            row, column = divmod(index, columns)
            final_lone_card = index == count - 1 and column == 0 and count % columns == 1
            span = columns if final_lone_card else 1
            self.optimizer_runs_layout.addWidget(card, row, column, 1, span)

    def _refresh_optimizer_run_cards(self):
        for index, card in self._optimizer_run_cards.items():
            state = self._optimizer_run_state.get(index)
            if not state:
                continue
            phase = str(state.get("phase", "queued")).replace("_", " ").title()
            card["phase"].setText(f"State: {phase}")
            details = []
            if state.get("iteration") and state.get("iterations"):
                details.append(f"Pass {state['iteration']}/{state['iterations']}")
            if state.get("tested") is not None and state.get("planned"):
                details.append(f"{state['tested']:,}/{state['planned']:,} combinations")
            if state.get("best") is not None:
                details.append(f"Best: {state['best']:,.4f}")
            card["detail"].setText("  ·  ".join(details) or "Waiting for a worker update.")
            card["results"].setText(_optimizer_current_result_lines(state.get("results")))
            if state.get("phase") == "completed":
                color, background = "#c5d0bd", "#414941"
            elif state.get("phase") in {"stopping", "stopped"}:
                color, background = "#e6c983", "#4a4435"
            elif state.get("phase") == "failed":
                color, background = "#e1b1b1", "#4b373a"
            else:
                color = OPTIMIZER_RUN_COLORS[(index - 1) % len(OPTIMIZER_RUN_COLORS)]
                background = "#35353d"
            card["card"].setStyleSheet(
                "QFrame#optimizerRunCard { border: 1px solid #85818f; border-radius: 3px; "
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
        search_progress = sum(fractions) / max(1, len(fractions))
        batch_total = int(getattr(self, "_profile_builder_optimizer_total", 0) or 0)
        batch_completed = int(
            getattr(self, "_profile_builder_optimizer_completed_count", 0) or 0
        )
        batch_running = (
            str(getattr(self, "_profile_builder_optimizer_batch_state", "")) == "running"
            and batch_total > 0
        )
        progress = (
            min(1.0, (batch_completed + search_progress) / batch_total)
            if batch_running else search_progress
        )
        self.optimizer_progress_value.setText(
            f"Overall progress: {progress * 100:.1f}% · {(1 - progress) * 100:.1f}% remaining"
        )
        eta_started = self._optimizer_eta_started_at or self._optimizer_started_at
        elapsed = time.monotonic() - (eta_started or time.monotonic())
        if (
            not self._optimizer_progress_samples
            or elapsed - self._optimizer_progress_samples[-1][0] >= 1.0
            or progress >= 1.0
        ):
            self._optimizer_progress_samples.append((elapsed, progress))
            self._optimizer_progress_samples = self._optimizer_progress_samples[-120:]
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
            self._set_optimizer_header_eta(remaining)
        else:
            remaining = _remaining_time_estimate(
                self._optimizer_progress_samples, elapsed=elapsed, progress=progress,
            )
        if not ranking_state and remaining is not None:
            self.optimizer_eta_value.setText(
                f"Estimated time remaining: {self._format_duration(remaining)}"
            )
            self._set_optimizer_header_eta(remaining)
        elif not ranking_state:
            self.optimizer_eta_value.setText("Estimated time remaining: calculating…")
            self._set_optimizer_header_eta(status="calculating…")
        elif ranking_state:
            self._set_optimizer_header_eta(status="calculating…")
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
        optimizer_running = bool(self.optimizer_thread and self.optimizer_thread.isRunning())
        overnight_running = bool(self.overnight_thread and self.overnight_thread.isRunning())
        if optimizer_running or overnight_running:
            stopping = any(
                state.get("phase") in {"stopping", "stopped"} for state in states
            )
            ui_state = "stopping" if stopping else "warming" if overnight_running else "running"
            self._set_optimizer_run_ui(
                ui_state,
                self.optimizer_phase_value.text().removeprefix("Current phase: "),
                progress * 100,
            )

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
        active_cache = self._active_optimizer_cache
        self._active_optimizer_cache = None
        self._cache_store(
            active_cache, self._serialize_optimizer_payload(result, ranking=True),
            time.monotonic() - (self._optimizer_started_at or time.monotonic()),
        )
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
        self._set_optimizer_gear_results_enabled(False)
        self.optimizer_activity.setText("Completed")
        self._set_optimizer_run_ui(
            "completed", f"Weapon-skill ranking complete · {success_count} results.", 100.0
        )
        self.optimizer_progress_value.setText("Overall progress: 100.0% · 0.0% remaining")
        self.optimizer_eta_value.setText("Estimated time remaining: complete")
        self.optimizer_best_value.setText("Best metric: see three-column ranking")
        self.optimizer_phase_value.setText("Current phase: finished")
        self.ws_ranking_dialog = WeaponSkillRankingDialog(result, self.icons, self)
        self.ws_ranking_dialog.show()
        if self._dashboard_ws_ranking_active:
            self._dashboard_ws_ranking_active = False
            self._apply_dashboard_ws_ranking(result)
        try:
            saved = self._serialize_optimizer_payload(result, ranking=True)
            first_entry = next(
                (row for rows in (result.get("rankings") or {}).values() for row in rows),
                None,
            )
            gearsets = {}
            if isinstance(first_entry, dict) and first_entry.get("player") is not None:
                player_data = first_entry["player"]
                if isinstance(player_data, dict) and player_data.get("__player__"):
                    serialized_player = player_data["__player__"]
                    gearsets = {"single": serialized_player.get("gearset") or {}}
            self._add_history(
                "ws-ranking", f"WS ranking · {result.get('skill_type', 'weapon type')}", {
                    "seed": _json_value(result.get("seed")),
                    "scenario": self._history_scenario(
                        action="weapon-skill ranking", ws_name="", tp=0,
                        seed=result.get("seed"),
                    ),
                    "metrics": {"ranking_rows": sum(len(rows) for rows in (result.get("rankings") or {}).values())},
                    "gearsets": gearsets,
                    "optimizer": _json_value(saved),
                },
            )
        except Exception as error:
            self._append_optimizer_log(f"Ranking history save skipped: {error}")
        self.statusBar().showMessage("Weapon-skill ranking completed", 5000)
        self._schedule_optimizer_status_close()

    def _optimizer_done(self, result):
        self._optimizer_status_timer.stop()
        for state in self._optimizer_run_state.values():
            state["phase"] = "completed"
            state["fraction"] = 1.0
        self._refresh_optimizer_status()
        active_cache = self._active_optimizer_cache
        self._active_optimizer_cache = None
        self._cache_store(
            active_cache, self._serialize_optimizer_payload(result),
            time.monotonic() - (self._optimizer_started_at or time.monotonic()),
        )
        substat_summary = result[5] if isinstance(result, (tuple, list)) and len(result) > 5 else []
        self.best_player, _output, metric, winning_seed, self.optimizer_top_results = result[:5]
        self._last_optimizer_output = _output
        self.best_tp_player = getattr(self.best_player, "tp_player", self.best_player)
        self.best_ws_player = getattr(self.best_player, "ws_player", self.best_player)
        self._last_completed_optimizer_action = self._optimizer_action_in_progress
        self._last_substat_summary = substat_summary or []
        self.optimizer_top_results = list(self.optimizer_top_results or [])
        self._append_optimizer_log("Completed · reproducibility data saved internally")
        self.optimize_button.setEnabled(True)
        self.stop_optimizer_button.setEnabled(False)
        self.equip_best_button.setEnabled(True)
        self._set_optimizer_gear_results_enabled(bool(self.optimizer_top_results))
        if self.optimizer_top_results:
            QTimer.singleShot(0, self.show_top_sets)
        self.optimizer_activity.setText("Completed")
        self._set_optimizer_run_ui(
            "completed", "Optimization complete · results are ready to review.", 100.0
        )
        self.optimizer_progress_value.setText("Overall progress: 100.0% · 0.0% remaining")
        self.optimizer_eta_value.setText("Estimated time remaining: complete")
        action_type = {
            "Weapon skill": "weapon skill", "Attack round": "attack round",
            "Spell": "spell cast", "Combined TP + WS": "combined tp/ws",
        }.get(self._optimizer_action_in_progress, "")
        result_summary = _optimizer_result_summary(
            action_type, _output, float(metric), self.metric_combo.currentText(),
            self.ws_combo.currentText(),
        )
        defense_summary = _optimizer_defense_summary(self.best_player)
        if defense_summary.get("fallback"):
            result_summary += " · ⚠ best available defense (requested floors not met)"
        self.optimizer_best_value.setText(result_summary)
        self._append_optimizer_log(f"Result · {result_summary}")
        if isinstance(self._last_substat_summary, dict) and self._last_substat_summary.get("mode") == "tradeoff":
            summary = self._last_substat_summary
            self._append_optimizer_log(
                f"Tradeoff frontier: {summary.get('frontier_count', 0)} non-dominated sets "
                f"across {', '.join(summary.get('targets') or [])}."
            )
            self.optimizer_best_value.setText(
                f"Balanced frontier result · {summary.get('search_mode', 'fast').title()} · {result_summary}"
            )
        elif self._last_substat_summary:
            for row in self._last_substat_summary:
                self._append_optimizer_log(
                    f"Priority {row['stat']}: {row['value']:,.1f} "
                    f"(damage {row['damage']:,.4f}; floor {row['damage_floor']:,.4f})"
                )
            self.optimizer_best_value.setText(
                f"Tradeoff result · {result_summary}"
            )
        self.optimizer_phase_value.setText("Current phase: finished")
        self.statusBar().showMessage("Optimizer completed", 5000)
        self._save_optimizer_history(result, float(metric), winning_seed, result_summary)
        self._profile_builder_optimizer_completed(metric)
        self._schedule_optimizer_status_close()

    def _save_optimizer_history(self, result, metric: float, seed, summary_text: str):
        """Store a compact optimizer result without retaining live player objects."""
        try:
            saved_player = _serialize_optimizer_player(self.best_player)
            gearsets = {}
            if saved_player.get("combined"):
                gearsets = {
                    "tp": saved_player.get("tp_gearset") or {},
                    "ws": saved_player.get("ws_gearset") or {},
                }
            else:
                gearsets = {"single": saved_player.get("gearset") or {}}
            action = self._optimizer_action_in_progress or "optimizer"
            payload = {
                "seed": _json_value(seed),
                "scenario": self._history_scenario(
                    action=f"optimizer: {action}", ws_name=self.ws_combo.currentText(),
                    tp=self.tp_value.value(), seed=seed,
                ),
                "metrics": {
                    "metric": _json_value(metric),
                    "metric_name": self.metric_combo.currentText(),
                    "summary": summary_text,
                    "output": _json_value(self._last_optimizer_output),
                },
                "gearsets": gearsets,
                "optimizer": _json_value(self._serialize_optimizer_payload(result)),
                "warnings": [
                    "Optimizer result is a saved winner; run Generate WS distribution or a cycle for plots."
                ],
            }
            self._add_history("optimizer", f"Optimizer · {action}", payload)
        except Exception as error:
            self._append_optimizer_log(f"History save skipped: {error}")

    def _optimizer_failed(self, message: str):
        self._optimizer_status_timer.stop()
        self._active_optimizer_cache = None
        self._ranking_skill_in_progress = None
        if self._dashboard_ws_ranking_active:
            self._dashboard_ws_ranking_active = False
            self.dashboard_ws_status.setText(f"WS ranking failed: {message}")
            QTimer.singleShot(0, self._refresh_build_dashboard)
        for state in self._optimizer_run_state.values():
            if state.get("phase") not in {"completed", "stopped"}:
                state["phase"] = "failed"
        self._refresh_optimizer_status()
        self._append_optimizer_log(f"Failed: {message}")
        self.optimize_button.setEnabled(True)
        self.stop_optimizer_button.setEnabled(False)
        self.optimizer_activity.setText("Failed")
        self._set_optimizer_run_ui("failed", f"Optimization failed · {message}")
        self.optimizer_eta_value.setText("Estimated time remaining: unavailable")
        self._cancel_profile_builder_optimizer_queue("optimizer failed")
        QMessageBox.critical(self, "Optimizer failed", message)

    def _optimizer_stopped(self, payload):
        self._optimizer_status_timer.stop()
        self._active_optimizer_cache = None
        self._ranking_skill_in_progress = None
        if self._dashboard_ws_ranking_active:
            self._dashboard_ws_ranking_active = False
            self.dashboard_ws_status.setText("WS ranking stopped before consolidation.")
            QTimer.singleShot(0, self._refresh_build_dashboard)
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
        self._set_optimizer_gear_results_enabled(bool(self.optimizer_top_results))
        self.optimizer_activity.setText("Stopped")
        self._set_optimizer_run_ui("stopped", "Optimization stopped by request.")
        self.optimizer_eta_value.setText("Estimated time remaining: stopped")
        self.optimizer_phase_value.setText("Current phase: stopped")
        self.statusBar().showMessage("Optimizer stopped", 5000)
        self._cancel_profile_builder_optimizer_queue("optimizer stopped")

    def stop_optimizer(self):
        if self.optimizer_thread and self.optimizer_thread.isRunning():
            self.optimizer_thread.request_stop()
            self.stop_optimizer_button.setEnabled(False)
            self.optimizer_activity.setText("Stopping...")
            self._set_optimizer_run_ui(
                "stopping", "Stop requested · finishing the current calculation."
            )
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
                f"Overall progress: {(current - 1) / max(1, total) * 100:.1f}%"
            )
            self.optimizer_phase_value.setText(
                f"Current phase: {ranking.group(3)}"
            )
        self._update_optimizer_run_state(message)
        if not re.search(r"Search run \d+", message):
            self._append_optimizer_log(message)
        if "started" in message.lower():
            self.optimizer_activity.setText("Running")
            self._set_optimizer_run_ui("running", message)

    def show_top_sets(self):
        if not self.optimizer_top_results:
            return
        self.top_sets_dialog = TopSetsDialog(self.optimizer_top_results, self.icons, self)
        self.top_sets_dialog.show()

    def _set_optimizer_gear_results_enabled(self, enabled: bool):
        """Keep both Show Gear entry points synchronized."""
        self.show_top_sets_button.setEnabled(bool(enabled))
        if hasattr(self, "show_results_button"):
            self.show_results_button.setEnabled(bool(enabled))

    def load_optimizer_result(self, index: int, destination: str):
        """Load a selected best result into Quick Look or TP/WS editors."""
        if not 0 <= index < len(self.optimizer_top_results):
            return
        result = self.optimizer_top_results[index]
        tp_player, ws_player = _optimizer_result_players(result)
        if tp_player is None or ws_player is None:
            return
        if destination == "tp":
            self.tp_set.set_gearset(tp_player.gearset)
            self.workspace_mode.setCurrentText("TP â†’ WS Cycle")
            self._select_tab("Gear Workspace")
            self.statusBar().showMessage("Loaded selected result into TP set", 5000)
        elif destination == "ws":
            self.ws_set.set_gearset(ws_player.gearset)
            self.workspace_mode.setCurrentText("TP â†’ WS Cycle")
            self._select_tab("Gear Workspace")
            self.statusBar().showMessage("Loaded selected result into WS set", 5000)
        elif destination == "tpws":
            self.tp_set.set_gearset(tp_player.gearset)
            self.ws_set.set_gearset(ws_player.gearset)
            self.workspace_mode.setCurrentText("TP → WS Cycle")
            self._select_tab("Gear Workspace")
            self.statusBar().showMessage("Loaded selected result into TP / WS sets", 5000)
        else:
            self.quick_set.set_gearset(tp_player.gearset)
            self.workspace_mode.setCurrentText("Single Set")
            self._select_tab("Gear Workspace")
            self.statusBar().showMessage("Loaded selected TP result into Quick Look", 5000)

    def load_ws_ranking_result(self, entry: dict):
        player = entry.get("player")
        if player is None:
            return
        self.ws_set.set_gearset(player.gearset)
        self.ws_combo.setCurrentText(str(entry.get("ws_name") or "None"))
        self.tp_value.setValue(int(entry.get("tp") or 1000))
        self.workspace_mode.setCurrentText("TP → WS Cycle")
        self._select_tab("Gear Workspace")
        self.statusBar().showMessage(
            f"Loaded {entry.get('ws_name')} optimized WS set", 5000
        )

    def equip_best(self):
        if self.best_player is not None:
            self.quick_set.set_gearset(self.best_player.gearset)
            self.workspace_mode.setCurrentText("Single Set")
            self._select_tab("Gear Workspace")

    def swap_tp_ws_sets(self):
        tp_items = dict(self.tp_set.items)
        self.tp_set.set_gearset(self.ws_set.items)
        self.ws_set.set_gearset(tp_items)
        self.statusBar().showMessage("TP and WS sets swapped", 3000)

    def _simulation_seed(self) -> int:
        value = self.workspace_seed.text().strip()
        if value:
            try:
                return int(value) & 0xFFFFFFFF
            except ValueError as error:
                raise ValueError("Run seed must be a whole number.") from error
        seed = secrets.randbits(31)
        self.workspace_seed.setText(str(seed))
        return seed

    def run_simulation(self):
        if self.simulation_thread and self.simulation_thread.isRunning():
            return
        try:
            tp_player, enemy, _buffs, _abilities = self._context(self.tp_set.items)
            ws_player, _enemy, _buffs, _abilities = self._context(self.ws_set.items)
            ws_name = self.ws_combo.currentText().strip()
            if not ws_name or ws_name == "None":
                raise ValueError("Select a weapon skill before running a two-hour cycle.")
            seed = self._simulation_seed()
            self._running_simulation_seed = seed
            reference_enemies = self._reference_enemy_cases(
                self.cycle_reference_checkbox.isChecked()
            )
            self.plot_status.setText(
                f"Running two-hour DPS simulation · {ws_name} at {self.tp_value.value():,} TP..."
            )
            self._render_cycle_graph_placeholder("Running the two-hour DPS simulation...")
            self.simulation_thread = SimulationThread(
                tp_player, ws_player, enemy, self.tp_value.value(), ws_name,
                self._ws_type(), seed, reference_enemies=reference_enemies, parent=self,
            )
            self.simulation_thread.completed.connect(self._simulation_done)
            self.simulation_thread.failed.connect(self._simulation_failed)
            self.simulation_thread.stopped.connect(self._simulation_stopped)
            self.simulate_button.setEnabled(False)
            self.cancel_simulation_button.setEnabled(True)
            self.simulation_thread.start()
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
            self._render_cycle_graph_placeholder("Sampling 20,000 weapon-skill results...")
            self.plot_thread = PlotThread(
                player, enemy, ws_name, self.tp_value.value(), self._ws_type(),
                seed=self._simulation_seed(),
                reference_enemies=self._reference_enemy_cases(
                    self.cycle_reference_checkbox.isChecked()
                ), parent=self,
            )
            self.plot_thread.completed.connect(self._plot_distribution_done)
            self.plot_thread.failed.connect(self._plot_distribution_failed)
            self.plot_thread.stopped.connect(self._plot_distribution_failed)
            self.plot_thread.start()
        except Exception as error:
            QMessageBox.critical(self, "Distribution plot", str(error))

    def _simulation_done(self, summary: dict):
        try:
            if not isinstance(summary, dict) or not all(
                np.isfinite(float(summary.get(key, 0))) for key in ("total_dps", "tp_dps", "ws_dps")
            ):
                raise ValueError("Simulation returned non-finite DPS values and was not saved.")
            seed = int(getattr(self, "_running_simulation_seed", 0) or 0)
            payload = {
                "seed": seed,
                "scenario": self._history_scenario(
                    action="two-hour cycle", ws_name=self.ws_combo.currentText(),
                    tp=self.tp_value.value(), seed=seed,
                ),
                "metrics": _json_value({
                    key: value for key, value in summary.items()
                    if key not in {"dps_series", "reference_summaries"}
                }),
                "dps_series": _json_value(summary.get("dps_series") or {}),
                "reference_summaries": _json_value(summary.get("reference_summaries") or {}),
                "gearsets": self._history_gearsets(tp=self.tp_set.items, ws=self.ws_set.items),
                "plot": {
                    "tp_player": self._history_player_snapshot(self.simulation_thread.player_tp),
                    "ws_player": self._history_player_snapshot(self.simulation_thread.player_ws),
                },
            }
            if _dps_series_chart_data(summary) is None:
                raise ValueError("Simulation returned no valid DPS convergence series.")
            self._render_cycle_dps_graph(summary)
            self._add_history(
                "cycle", f"{self.ws_combo.currentText()} cycle · {self.tp_value.value():,} TP", payload
            )
            self.plot_status.setText(
                f"Completed and saved · total {summary['total_dps']:,.1f} DPS."
            )
            self.simulate_button.setEnabled(True)
            self.cancel_simulation_button.setEnabled(False)
            self.statusBar().showMessage(
                "Two-hour DPS graph updated; result also saved to Results", 6000
            )
        except Exception as error:
            self._simulation_failed(str(error))

    def _simulation_failed(self, message: str):
        self.simulate_button.setEnabled(True)
        self.cancel_simulation_button.setEnabled(False)
        self.plot_status.setText(f"Simulation failed: {message}")
        QMessageBox.critical(self, "Simulation failed", message)

    def _simulation_stopped(self, message: str):
        self.simulate_button.setEnabled(True)
        self.cancel_simulation_button.setEnabled(False)
        self.plot_status.setText(message)
        self.statusBar().showMessage("Two-hour simulation stopped; no result was saved", 5000)

    def stop_simulation(self):
        if self.simulation_thread and self.simulation_thread.isRunning():
            self.simulation_thread.request_stop()
            self.cancel_simulation_button.setEnabled(False)
            self.plot_status.setText("Stopping simulation after the current calculation...")

    def _plot_distribution_done(self, distribution: dict):
        try:
            values = [distribution.get(key) for key in ("mean", "median", "p05", "p95")]
            if not all(value is not None and np.isfinite(float(value)) for value in values):
                raise ValueError("Weapon-skill distribution returned non-finite values and was not saved.")
            seed = int(self.plot_thread.seed or 0)
            payload = {
                "seed": seed,
                "scenario": self._history_scenario(
                    action="WS distribution", ws_name=self.plot_thread.ws_name,
                    tp=self.plot_thread.tp_value, seed=seed,
                ),
                "metrics": _json_value({
                    "average_ws_damage": distribution.get("mean"),
                    "ws_minimum": distribution.get("minimum"),
                    "ws_maximum": distribution.get("maximum"),
                    "samples": distribution.get("samples"),
                }),
                "distribution": _json_value(distribution),
                "gearsets": self._history_gearsets(ws=self.plot_thread.player.gearset),
                "plot": {"ws_player": self._history_player_snapshot(self.plot_thread.player)},
            }
            if _ws_distribution_chart_data(distribution) is None:
                raise ValueError("Weapon-skill distribution returned an invalid histogram.")
            self._render_ws_distribution_graph(
                self.cycle_result_figure, self.cycle_result_canvas, distribution,
                ws_name=self.plot_thread.ws_name,
            )
            self._add_history(
                "distribution", f"{self.plot_thread.ws_name} distribution", payload
            )
            self.plot_status.setText(
                "20,000-sample weapon-skill distribution complete and saved to Results."
            )
            self.statusBar().showMessage(
                "WS distribution graph updated; result also saved to Results", 6000
            )
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
