"""Responsive PyQt6 interface for WSDist.

The permanent widget tree stays small. Large gear lists exist only while a
picker is open, avoiding the window-drag repaint cost of the legacy Tk UI.
The calculation and optimizer modules are reused without formula changes.
"""

from __future__ import annotations

import ast
import csv
import copy
import json
import multiprocessing
import os
import re
import sys
from pathlib import Path

from PyQt6.QtCore import QSettings, QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QSplitter,
    QStatusBar, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

import actions
import buffs as buff_data
import create_player
import enemies
import gear
import wsdist
from wsdist_bridge import BridgeStore


APP_DIR = Path(__file__).resolve().parent
SLOTS = (
    "main", "sub", "ranged", "ammo", "head", "neck", "ear1", "ear2",
    "body", "hands", "ring1", "ring2", "back", "waist", "legs", "feet",
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
    if "hybrid" in tokens or "hybrid" in set_name.casefold():
        return "Hybrid"
    if tokens & {"tp", "tpmelee", "tpranged"}:
        return "TP"
    if tokens & {"dt", "pdt", "mdt", "damage", "taken", "idle"}:
        return "DT"
    return None


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
    result["weapon_setup"] = " + ".join(labels) if labels else "As listed"
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


def item_tooltip(item: dict) -> str:
    ignored = {"Name", "Name2", "Jobs", "Slots", "Bridge Key", "Eligible"}
    lines = [str(item.get("Name") or item_name(item))]
    for key, value in item.items():
        if key not in ignored and value not in (None, "", 0, False, [], {}):
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


class GearIconProvider:
    """Resolve and cache item icons without adding permanent image widgets."""

    def __init__(self):
        self._item_ids: dict[str, int] = {}
        self._icons: dict[tuple[int, tuple[str, ...]], QIcon] = {}
        self._bridge_icon_dir: Path | None = None
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
                 icons: GearIconProvider, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Optimizer candidates: {slot.title()}")
        self.resize(600, 680)
        self._items = sorted(items, key=lambda value: item_name(value).lower())
        self.selected_names = set(selected)
        self.icons = icons
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter candidates...")
        self.list = QListWidget()
        self.list.setUniformItemSizes(True)
        self.list.setIconSize(QSize(32, 32))
        layout.addWidget(self.search)
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

    def set_item(self, slot: str, item: dict):
        self.items[slot] = item
        self.buttons[slot].setText(item_name(item))
        self.buttons[slot].setIcon(self.owner.icons.icon(item))
        self.buttons[slot].setToolTip(item_tooltip(item))
        self.changed.emit()

    def refresh_icons(self):
        for slot, item in self.items.items():
            self.buttons[slot].setIcon(self.owner.icons.icon(item))

    def set_gearset(self, gearset: dict):
        for slot in SLOTS:
            self.set_item(slot, gearset.get(slot, gear.Empty))


class OptimizeThread(QThread):
    progress = pyqtSignal(str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, args: tuple, kwargs: dict, parent=None):
        super().__init__(parent)
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            kwargs = dict(self.kwargs)
            kwargs["progress_callback"] = self.progress.emit
            self.succeeded.emit(wsdist.optimize_set(*self.args, **kwargs))
        except Exception as error:
            self.failed.emit(str(error))


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
        }
        if payload["category"] is not None:
            attack = actions.average_attack_round(
                player, enemy, 0, context["tp_value"], "Time to WS"
            )
            dps = actions.average_attack_round(
                player, enemy, 0, context["tp_value"], "DPS"
            )
            row["tp_dps"] = dps[0]
            row["time_to_ws"] = attack[0]
            row["_tp_damage"] = attack[1][0]
            row["_tp_return"] = attack[1][1]
            row["_attack_time"] = attack[1][2]
        if payload["ws_name"]:
            ws_type = "ranged" if payload["ws_name"] in (
                WS_BY_SKILL.get("Marksmanship", []) + WS_BY_SKILL.get("Archery", [])
            ) else "melee"
            ws = actions.average_ws(
                player, enemy, payload["ws_name"], context["tp_value"], ws_type, "Damage dealt"
            )
            row["ws_damage"] = ws[0]
        if row["tp_dps"] != "" and row["ws_damage"] != "":
            # Convert the average TP-round values into one TP-to-WS cycle,
            # including the game's forced two-second WS delay.
            tp_round = actions.average_attack_round(
                player, enemy, 0, context["tp_value"], "Damage dealt"
            )
            tp_time = attack[0]
            row["total_dps"] = (tp_round[0] * context["tp_value"] / attack[1][1] + row["ws_damage"]) / (tp_time + 2.0)
        return row
    except Exception as error:
        return {
            "name": payload["name"], "category": payload["category"] or "WS",
            "ws_name": payload["ws_name"] or "", "tp_dps": "", "time_to_ws": "",
            "ws_damage": "", "total_dps": "", "weapon_setup": payload.get("weapon_setup", "As listed"),
            "error": str(error),
            "_tp_damage": None, "_tp_return": None, "_attack_time": None,
        }


class ProfileReportThread(QThread):
    progress = pyqtSignal(str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, payloads: list[dict], context: dict, parent=None):
        super().__init__(parent)
        self.payloads = payloads
        self.context = context

    def run(self):
        try:
            rows = []
            for index, payload in enumerate(self.payloads, 1):
                self.progress.emit(f"Evaluating {index}/{len(self.payloads)}: {payload['name']}")
                rows.append(_evaluate_profile_set(payload, self.context))
            self.succeeded.emit(rows)
        except Exception as error:
            self.failed.emit(str(error))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WSDist — Qt")
        self.resize(1220, 820)
        self.setMinimumSize(QSize(900, 650))
        icon = APP_DIR / "icons32" / "23937.png"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.settings = QSettings("WSDist", "QtGui")
        self.bridge_store = BridgeStore()
        self.icons = GearIconProvider()
        self.character_paths: dict[str, Path] = {}
        self.equipment = _base_equipment()
        self.optimizer_thread: OptimizeThread | None = None
        self.report_thread: ProfileReportThread | None = None
        self.best_player = None
        self.candidates = {slot: {"Empty"} for slot in SLOTS}
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
        legacy = QAction("About legacy interface", self)
        legacy.triggered.connect(lambda: QMessageBox.information(
            self, "Legacy interface",
            "Run python gui_main.py to use the restored Tk interface."
        ))
        close = QAction("Exit", self)
        close.triggered.connect(self.close)
        file_menu.addActions([select_root, refresh, legacy])
        file_menu.addSeparator()
        file_menu.addAction(close)

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
        form.addRow("TP", self.tp_value)
        form.addRow("Aftermath", self.aftermath)
        form.addRow("Weapon skill", self.ws_combo)
        form.addRow("Spell", self.spell_combo)
        self.main_job.currentTextChanged.connect(self._refresh_job_data)
        self.sub_job.currentTextChanged.connect(self.refresh_quick_stats)
        self.master_level.valueChanged.connect(self.refresh_quick_stats)
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
        self.tp_set.changed.connect(self._gear_changed)
        self.ws_set.changed.connect(self._gear_changed)
        self.tabs.addTab(self._quick_tab(), "Quick Look")
        self.tabs.addTab(self._optimizer_tab(), "Optimizer")
        self.tabs.addTab(self._sets_tab(), "TP / WS Sets")
        self.tabs.addTab(self._buffs_tab(), "Buffs")
        self.tabs.addTab(self._advanced_tab(), "Advanced")
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
            "Uses the selected gear, Buffs tab, and Advanced switches. Haste values use the engine's source caps."
        ))
        totals_header.addStretch(1)
        refresh_totals = QPushButton("Refresh totals")
        refresh_totals.clicked.connect(self.refresh_quick_stats)
        totals_header.addWidget(refresh_totals)
        totals_layout.addLayout(totals_header)
        self.quick_stats = QPlainTextEdit()
        self.quick_stats.setReadOnly(True)
        self.quick_stats.setMinimumHeight(180)
        self.quick_stats.setMaximumHeight(220)
        totals_layout.addWidget(self.quick_stats)
        layout.addWidget(totals)
        return tab

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
        pdt_total = max(-50, stats.get("PDT", 0) + stats.get("DT", 0))
        mdt_total = max(-50, stats.get("MDT", 0) + stats.get("DT", 0))

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
            f"Magic Dmg {number('Magic Damage'):>7}    Store TP {number('Store TP'):>7}",
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

    def refresh_quick_stats(self, *_args):
        if not hasattr(self, "quick_stats"):
            return
        try:
            player, enemy, _buffs, _abilities = self._context()
            self.quick_stats.setPlainText(self._quick_stats_text(player, enemy))
        except Exception as error:
            self.quick_stats.setPlainText(f"Unable to calculate totals: {error}")

    def _optimizer_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        top = QHBoxLayout()
        candidates = QGroupBox("Candidates by slot")
        grid = QGridLayout(candidates)
        self.candidate_buttons = {}
        for index, slot in enumerate(SLOTS):
            button = QPushButton("1 selected")
            button.clicked.connect(lambda _checked=False, name=slot: self.choose_candidates(name))
            row, column = divmod(index, 4)
            cell = QVBoxLayout()
            label = QLabel(slot.upper())
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.addWidget(label)
            cell.addWidget(button)
            grid.addLayout(cell, row, column)
            self.candidate_buttons[slot] = button
        select_all = QPushButton("Select all gear in all slots")
        select_all.setToolTip("Include every available item for every optimizer slot.")
        select_all.clicked.connect(self.select_all_candidates)
        grid.addWidget(select_all, 4, 0, 1, 4)
        top.addWidget(candidates, 1)

        options = QGroupBox("Search")
        form = QFormLayout(options)
        self.optimize_action = QComboBox()
        self.optimize_action.addItems(["Weapon skill", "Attack round", "Spell"])
        self.metric_combo = QComboBox()
        self.optimize_action.currentTextChanged.connect(self._refresh_optimizer_metrics)
        self.pdt = QSpinBox()
        self.pdt.setRange(-50, 100)
        self.mdt = QSpinBox()
        self.mdt.setRange(-50, 100)
        self.restarts = QSpinBox()
        self.restarts.setRange(1, 64)
        self.restarts.setValue(3)
        self.restarts.setToolTip("Independent searches. More restarts improve coverage but add work.")
        self.workers = QSpinBox()
        self.workers.setRange(0, max(1, os.cpu_count() or 1))
        self.workers.setToolTip("0 uses available CPU cores while leaving one free.")
        self.seed = QLineEdit()
        self.seed.setPlaceholderText("random")
        self.seed.setToolTip("Optional repeatable seed. Blank creates a new search sequence.")
        form.addRow("Action", self.optimize_action)
        form.addRow("Metric", self.metric_combo)
        form.addRow("Required PDT %", self.pdt)
        form.addRow("Required MDT %", self.mdt)
        form.addRow("Restarts", self.restarts)
        form.addRow("Parallel workers", self.workers)
        form.addRow("Optimizer seed", self.seed)
        self.optimize_button = QPushButton("Run optimizer")
        self.optimize_button.clicked.connect(self.run_optimizer)
        self.equip_best_button = QPushButton("Equip best set")
        self.equip_best_button.setEnabled(False)
        self.equip_best_button.clicked.connect(self.equip_best)
        form.addRow(self.optimize_button)
        form.addRow(self.equip_best_button)
        top.addWidget(options)
        layout.addLayout(top)
        self.optimizer_log = QPlainTextEdit()
        self.optimizer_log.setReadOnly(True)
        self.optimizer_log.setPlaceholderText("Optimizer progress appears here.")
        layout.addWidget(self.optimizer_log, 1)
        self.optimizer_activity = QLabel("Idle")
        self.optimizer_activity.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.optimizer_activity)
        self._refresh_optimizer_metrics(self.optimize_action.currentText())
        return tab

    def _refresh_optimizer_metrics(self, action: str):
        metrics = {
            "Weapon skill": ["Damage dealt", "TP return", "Magic accuracy"],
            "Attack round": ["Time to WS", "Damage dealt", "TP return", "DPS"],
            "Spell": ["Damage dealt", "TP return"],
        }[action]
        current = self.metric_combo.currentText()
        self.metric_combo.clear()
        self.metric_combo.addItems(metrics)
        if current in metrics:
            self.metric_combo.setCurrentText(current)

    def _sets_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        sets = QTabWidget()
        sets.addTab(self.tp_set, "TP set")
        sets.addTab(self.ws_set, "WS set")
        layout.addWidget(sets, 1)
        simulate = QPushButton("Run DPS simulation")
        simulate.clicked.connect(self.run_simulation)
        layout.addWidget(simulate)
        return tab

    def _buffs_tab(self) -> QWidget:
        """Build the structured equivalent of the legacy Active Buffs pane."""
        tab = QWidget()
        outer = QVBoxLayout(tab)
        note = QLabel(
            "Enable only buffs currently active. These controls feed the existing "
            "calculation engine; Advanced JSON remains available for uncommon cases."
        )
        note.setWordWrap(True)
        outer.addWidget(note)
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
        return tab

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

    def _advanced_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.buffs_json = QPlainTextEdit("{}")
        self.abilities_json = QPlainTextEdit("{}")
        layout.addWidget(QLabel("Additional buff sources (JSON object)"))
        layout.addWidget(self.buffs_json, 1)
        layout.addWidget(QLabel("Abilities / special toggles (JSON object)"))
        layout.addWidget(self.abilities_json, 1)
        note = QLabel(
            "Additional sources are added to the structured Buffs tab. Use this "
            "for buffs and switches not yet promoted to dedicated controls."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        return tab

    def _profile_report_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        controls = QHBoxLayout()
        self.profile_job_combo = QComboBox()
        self.profile_job_combo.currentTextChanged.connect(self._populate_profile_report)
        refresh = QPushButton("Refresh profile sets")
        refresh.clicked.connect(self._populate_profile_report)
        run = QPushButton("Run profile report")
        run.clicked.connect(self.run_profile_report)
        controls.addWidget(QLabel("LuAshitacast job:"))
        controls.addWidget(self.profile_job_combo)
        controls.addWidget(refresh)
        controls.addWidget(run)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.profile_report_status = QLabel("Load a character bridge to inspect its LuAshitacast profiles.")
        self.profile_report_status.setWordWrap(True)
        layout.addWidget(self.profile_report_status)
        weapon_sets = QGroupBox("Profile weapon overlays")
        weapon_form = QFormLayout(weapon_sets)
        self.report_main_weapon_combo = QComboBox()
        self.report_ranged_weapon_combo = QComboBox()
        self.report_main_weapon_combo.currentIndexChanged.connect(self._profile_weapon_changed)
        self.report_ranged_weapon_combo.currentIndexChanged.connect(self._profile_weapon_changed)
        weapon_form.addRow("Main / sub weapon set", self.report_main_weapon_combo)
        weapon_form.addRow("Ranged / ammo set", self.report_ranged_weapon_combo)
        weapon_note = QLabel(
            "Use these when the profile equips armor and weapons in separate sets. "
            "Only explicitly listed weapon slots replace the armor set."
        )
        weapon_note.setWordWrap(True)
        weapon_form.addRow(weapon_note)
        layout.addWidget(weapon_sets)

        self.profile_report_table = QTableWidget(0, 8)
        self.profile_report_table.setHorizontalHeaderLabels(
            ["Set", "Type", "Matched WS", "Weapon setup", "TP DPS", "Time to WS", "WS Damage", "Total DPS"]
        )
        self.profile_report_table.setAlternatingRowColors(True)
        self.profile_report_table.setSortingEnabled(True)
        self.profile_report_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.profile_report_table, 1)

        selected = QGroupBox("Selected TP + WS total DPS")
        selected_form = QFormLayout(selected)
        self.report_tp_combo = QComboBox()
        self.report_ws_set_combo = QComboBox()
        self.report_ws_name_combo = QComboBox()
        self.report_ws_name_combo.setEditable(True)
        self.report_tp_combo.currentTextChanged.connect(self._refresh_selected_report_sets)
        self.report_ws_set_combo.currentTextChanged.connect(self._refresh_selected_report_sets)
        selected_form.addRow("TP / DT / hybrid set", self.report_tp_combo)
        selected_form.addRow("WS set", self.report_ws_set_combo)
        selected_form.addRow("Weapon skill", self.report_ws_name_combo)
        selected_button = QPushButton("Calculate selected total DPS")
        selected_button.clicked.connect(self.run_selected_profile_report)
        selected_form.addRow(selected_button)
        self.selected_report_result = QLabel("Choose a TP set and WS set, then calculate.")
        self.selected_report_result.setWordWrap(True)
        selected_form.addRow(self.selected_report_result)
        layout.addWidget(selected)
        return tab

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
        payloads = []
        for profile_set in profile.get("sets", []):
            name = str(profile_set.get("name") or "Unnamed")
            category = _profile_category(name)
            ws_name = _profile_ws_name(name)
            gearset = {slot: gear.Empty for slot in SLOTS}
            specified_slots = set()
            missing = []
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
                else:
                    gearset[slot] = item
            if category is None and ws_name is None and not (specified_slots & set(WEAPON_SLOTS)):
                continue
            payloads.append({
                "name": name, "category": category, "ws_name": ws_name,
                "gearset": gearset, "specified_slots": specified_slots, "missing": missing,
            })
        return payloads

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
        current_main_weapon = self.report_main_weapon_combo.currentText()
        current_ranged_weapon = self.report_ranged_weapon_combo.currentText()
        self.report_tp_combo.clear()
        self.report_ws_set_combo.clear()
        self.report_main_weapon_combo.clear()
        self.report_ranged_weapon_combo.clear()
        self.report_main_weapon_combo.addItem("None (use gear set as listed)", None)
        self.report_ranged_weapon_combo.addItem("None (use gear set as listed)", None)
        for payload in payloads:
            if payload["category"] is not None:
                self.report_tp_combo.addItem(payload["name"], payload)
            if payload["ws_name"]:
                self.report_ws_set_combo.addItem(payload["name"], payload)
            if set(payload["specified_slots"]) & set(MAIN_WEAPON_SLOTS):
                self.report_main_weapon_combo.addItem(payload["name"], payload)
            if set(payload["specified_slots"]) & set(RANGED_WEAPON_SLOTS):
                self.report_ranged_weapon_combo.addItem(payload["name"], payload)
        if current_main_weapon:
            self.report_main_weapon_combo.setCurrentText(current_main_weapon)
        if current_ranged_weapon:
            self.report_ranged_weapon_combo.setCurrentText(current_ranged_weapon)
        self.report_tp_combo.blockSignals(False)
        self.report_ws_set_combo.blockSignals(False)
        self.report_main_weapon_combo.blockSignals(False)
        self.report_ranged_weapon_combo.blockSignals(False)
        self._refresh_selected_report_sets()
        reportable_count = sum(1 for payload in payloads if payload["category"] is not None or payload["ws_name"])
        if reportable_count:
            self.profile_report_status.setText(
                f"Found {reportable_count} TP, DT, hybrid, or weapon-skill sets. Select weapon overlays before running."
            )
        else:
            self.profile_report_status.setText(
                "No readable TP, DT, hybrid, or named WS sets found for this profile."
            )

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
        self._refresh_selected_report_sets()
        if self.profile_job_combo.currentText():
            self.profile_report_status.setText(
                "Weapon overlay changed. Run the report again to evaluate the selected setup."
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
        buffs = self._merge_buff_sources(structured, self._json_object(self.buffs_json, "Additional buffs"))
        abilities = self._json_object(self.abilities_json, "Abilities")
        abilities.setdefault("Aftermath", self.aftermath.value())
        abilities.setdefault("Enhancing Skill", self.enhancing_skill.value())
        abilities.setdefault("Storm spell", self.storm_combo.currentText() if self.whm_enabled.isChecked() else "None")
        abilities.setdefault("Enemy Resist Rank", "100%")
        abilities.setdefault("99999", False)
        main_job_name = self.profile_job_combo.currentText() if use_profile_job else self.main_job.currentText()
        return {
            "main_job": JOBS[main_job_name],
            "sub_job": JOBS.get(self.sub_job.currentText(), "None"),
            "master_level": self.master_level.value(), "buffs": buffs,
            "abilities": abilities,
            "enemy": {name: spin.value() for name, spin in self.enemy_spins.items()},
            "debuffs": debuffs, "tp_value": self.tp_value.value(),
        }

    def run_profile_report(self):
        if self.report_thread and self.report_thread.isRunning():
            return
        payloads = [payload for payload in self._profile_payloads()
                    if payload["category"] is not None or payload["ws_name"]]
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
        payloads = [self._effective_profile_payload(payload) for payload in payloads]
        self.report_thread = ProfileReportThread(payloads, context, self)
        self.report_thread.progress.connect(self.profile_report_status.setText)
        self.report_thread.succeeded.connect(self._profile_report_done)
        self.report_thread.failed.connect(self._profile_report_failed)
        self.report_thread.start()

    @staticmethod
    def _report_value(value, decimals=1) -> str:
        if value in (None, ""):
            return "—"
        return f"{float(value):,.{decimals}f}"

    def _profile_report_done(self, rows: list[dict]):
        self.profile_report_table.setSortingEnabled(False)
        self.profile_report_table.setRowCount(0)
        for row in rows:
            index = self.profile_report_table.rowCount()
            self.profile_report_table.insertRow(index)
            values = [
                row["name"], row["category"], row["ws_name"] or "—",
                row["weapon_setup"], self._report_value(row["tp_dps"]),
                self._report_value(row["time_to_ws"]),
                self._report_value(row["ws_damage"], 0), self._report_value(row["total_dps"]),
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if row.get("error"):
                    cell.setToolTip(row["error"])
                self.profile_report_table.setItem(index, column, cell)
        self.profile_report_table.setSortingEnabled(True)
        errors = sum(1 for row in rows if row.get("error"))
        self.profile_report_status.setText(
            f"Report complete: {len(rows) - errors}/{len(rows)} sets evaluated" +
            (f"; {errors} errors shown as tooltips." if errors else ".")
        )

    def _profile_report_failed(self, message: str):
        self.profile_report_status.setText(f"Report failed: {message}")
        QMessageBox.critical(self, "LAC report", message)

    def run_selected_profile_report(self):
        tp_payload = self._effective_profile_payload(self.report_tp_combo.currentData())
        ws_payload = self._effective_profile_payload(self.report_ws_set_combo.currentData())
        ws_name = self.report_ws_name_combo.currentText().strip()
        if not tp_payload or not ws_payload or not ws_name:
            QMessageBox.information(self, "LAC report", "Select a TP/DT/hybrid set, WS set, and weapon skill.")
            return
        try:
            context = self._report_context(use_profile_job=True)
            tp_row = _evaluate_profile_set(
                {**tp_payload, "category": tp_payload["category"], "ws_name": None}, context
            )
            ws_row = _evaluate_profile_set(
                {**ws_payload, "category": None, "ws_name": ws_name}, context
            )
            if tp_row.get("error") or ws_row.get("error"):
                raise ValueError(tp_row.get("error") or ws_row.get("error"))
            if not tp_row["_tp_return"] or ws_row["ws_damage"] == "":
                raise ValueError("The selected TP set returned no usable TP or the WS set was invalid.")
            rounds = context["tp_value"] / tp_row["_tp_return"]
            total_time = tp_row["time_to_ws"] + 2.0
            total_damage = tp_row["_tp_damage"] * rounds + ws_row["ws_damage"]
            total_dps = total_damage / total_time if total_time > 0 else 0
            self.selected_report_result.setText(
                f"{tp_payload['name']} → {ws_payload['name']} using {ws_name}: "
                f"TP time {tp_row['time_to_ws']:,.2f}s, WS damage {ws_row['ws_damage']:,.0f}, "
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
            if job not in jobs or name in seen:
                continue
            seen.add(name)
            result.append(item)
        return result

    def choose_candidates(self, slot: str):
        dialog = CandidatePicker(
            slot, self.items_for_slot(slot), self.candidates[slot], self.icons, self
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.candidates[slot] = dialog.selected_names
            self._update_candidate_button(slot)

    def select_all_candidates(self):
        for slot in SLOTS:
            self.candidates[slot] = {item_name(item) for item in self.items_for_slot(slot)}
            self._update_candidate_button(slot)
        self.statusBar().showMessage("All available gear selected for every optimizer slot.", 5000)

    def _update_candidate_button(self, slot: str):
        self.candidate_buttons[slot].setText(f"{len(self.candidates[slot])} selected")

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
            data = self.bridge_store.load(path)
            self.icons.set_bridge_icon_dir(path.parent / "icons32")
            gear.all_gear.update(self.bridge_store.catalog)
            self.equipment = self.bridge_store.equipment_dict()
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
        self.refresh_quick_stats()

    def _gear_changed(self):
        for slot in SLOTS:
            name = item_name(self.quick_set.items[slot])
            if name != "Empty":
                self.candidates[slot].add(name)
                self._update_candidate_button(slot)
        self._refresh_ws_choices()
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
            for slot in SLOTS:
                if item_name(editor.items[slot]) not in valid[slot]:
                    editor.set_item(slot, gear.Empty)
        for slot in SLOTS:
            self.candidates[slot].intersection_update(valid[slot])
            if not self.candidates[slot]:
                self.candidates[slot].add(item_name(self.quick_set.items[slot]))
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
            self.quick_stats.setPlainText(self._quick_stats_text(player, enemy))
        except Exception as error:
            QMessageBox.critical(self, "Evaluation failed", str(error))

    def run_optimizer(self):
        if self.optimizer_thread and self.optimizer_thread.isRunning():
            return
        try:
            _player, enemy, buffs, abilities = self._context()
            check_gear = {}
            for slot in SLOTS:
                lookup = {item_name(item): item for item in self.items_for_slot(slot)}
                check_gear[slot] = [lookup[name] for name in self.candidates[slot] if name in lookup]
                if not check_gear[slot]:
                    check_gear[slot] = [self.quick_set.items[slot]]
            if not any(len(values) > 1 for values in check_gear.values()):
                raise ValueError("Select at least two candidates in one slot.")
            seed_text = self.seed.text().strip()
            seed = int(seed_text) if seed_text else None
            action_type = {
                "Weapon skill": "weapon skill", "Attack round": "attack round",
                "Spell": "spell cast",
            }[self.optimize_action.currentText()]
            args = (
                JOBS[self.main_job.currentText()], JOBS.get(self.sub_job.currentText(), "None"),
                self.master_level.value(), buffs, abilities, enemy,
                self.ws_combo.currentText(), self.spell_combo.currentText(), action_type,
                self.tp_value.value(), check_gear, dict(self.quick_set.items),
                self.pdt.value(), self.mdt.value(), self.metric_combo.currentText(), False, 2,
            )
            kwargs = {
                "restarts": self.restarts.value(), "workers": self.workers.value(),
                "seed": seed, "return_details": True,
            }
            checks = wsdist.estimate_candidate_checks(
                check_gear, JOBS[self.main_job.currentText()], self._ws_type()
            )
            self.optimizer_log.clear()
            self.optimizer_log.appendPlainText(
                f"Starting optimizer · ~{checks:,} candidates per pass"
            )
            self.optimizer_activity.setText("Starting…")
            self.optimize_button.setEnabled(False)
            self.equip_best_button.setEnabled(False)
            self.optimizer_thread = OptimizeThread(args, kwargs, self)
            self.optimizer_thread.progress.connect(self._optimizer_progress)
            self.optimizer_thread.succeeded.connect(self._optimizer_done)
            self.optimizer_thread.failed.connect(self._optimizer_failed)
            self.optimizer_thread.start()
        except Exception as error:
            QMessageBox.critical(self, "Optimizer", str(error))

    def _optimizer_done(self, result):
        self.best_player, _output, metric, winning_seed = result
        self.optimizer_log.appendPlainText(
            f"Completed · metric {metric:.6f} · seed {winning_seed}"
        )
        self.optimize_button.setEnabled(True)
        self.equip_best_button.setEnabled(True)
        self.optimizer_activity.setText("Completed")
        self.statusBar().showMessage("Optimizer completed", 5000)

    def _optimizer_failed(self, message: str):
        self.optimizer_log.appendPlainText(f"Failed: {message}")
        self.optimize_button.setEnabled(True)
        self.optimizer_activity.setText("Failed")
        QMessageBox.critical(self, "Optimizer failed", message)

    def _optimizer_progress(self, message: str):
        self.optimizer_log.appendPlainText(message)
        if " active (" in message:
            self.optimizer_activity.setText(message)
        elif "started" in message.lower():
            self.optimizer_activity.setText("Running")

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
                self.ws_combo.currentText(), self._ws_type(), False,
            )
        except Exception as error:
            QMessageBox.critical(self, "Simulation failed", str(error))

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
