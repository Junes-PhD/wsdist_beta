"""Compact FFXI-inspired equipment menu prototype.

The layout and palette are based on ``_references/equipment menu.png``.  It
is deliberately independent of the simulator's gear model so it can be used
as a safe visual prototype before being connected to real equipment data.
"""

from __future__ import annotations

import sys
import zipfile
from html import escape
from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QProgressBar, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)


APP_DIR = Path(__file__).resolve().parent
ICON_ARCHIVE = APP_DIR / "icons32.zip"

# The IDs are from item_list.csv, so these entries resolve to the bundled
# icons32 archive without needing a separate asset directory.
ITEMS = (
    {"id": 21585, "name": "Crepuscular Knife", "slot": "Main",
     "description": "(Dagger) All Races\nDMG:133  Delay:190  DEX+15\nAGI+15  CHR+15  Accuracy+40"},
    {"id": 17006, "name": "Drill Calamary", "slot": "Ammo",
     "description": "A curious piece of ammunition."},
    {"id": 22255, "name": "Potestas Bomblet", "slot": "Ammo",
     "description": "STR+2  Attack+7"},
    {"id": 21347, "name": "Charitoni Sling", "slot": "Range",
     "description": "A sling favored by seasoned adventurers."},
    {"id": 22087, "name": "Loughnashade", "slot": "Range",
     "description": "(Wind Instr.) All Races\nCHR+3\nSlowly devours your soul\nLv.99 BRD"},
    {"id": 23732, "name": "Perfection Masque", "slot": "Head",
     "description": "DEF:123  HP+80  Accuracy+40"},
    {"id": 25574, "name": "Abyssal Mask", "slot": "Head",
     "description": "A dark mask suffused with abyssal power."},
    {"id": 23733, "name": "Perfection Plate.", "slot": "Body",
     "description": "DEF:167  HP+100  Accuracy+50"},
    {"id": 25842, "name": "Jumalik Mail", "slot": "Body",
     "description": "Heavy armor marked by ancient craftsmanship."},
)

EQUIPPED = {
    "Main": {"id": 20688, "name": "Tizona", "description":
             "(Sword) All Races Su5\nDMG:180  Delay:236  HP+130\nMP+70  Accuracy+50\nMagic Accuracy+50\nSword skill +269"},
    "Sub": {"id": 21621, "name": "Naegling", "description":
            "(Sword) All Races\nDMG:240  Delay:240\nSTR+15  Accuracy+50  Attack+50"},
    "Range": {"id": 22087, "name": "Malignance Pole", "description":
              "(Polearm) All Races\nDMG:320  Delay:492\nAccuracy+40  Attack+40"},
    "Head": {"id": 23732, "name": "Malignance Chapeau", "description":
             "DEF:123  HP+80\nAccuracy+40  Magic Accuracy+40\nHaste+6%"},
    "Neck": {"id": 26026, "name": "Shulmanu Collar", "description":
             "DEF:3  STR+3  DEX+3\nAccuracy+20  Attack+20"},
    "Ear 1": {"id": 14739, "name": "Suppanomimi", "description":
              "Accuracy+5  Attack+5\nDual Wield+5"},
    "Body": {"id": 23733, "name": "Malignance Tabard", "description":
             "DEF:167  HP+100\nAccuracy+50  Magic Accuracy+50\nHaste+3%"},
    "Hands": {"id": 23734, "name": "Malignance Gloves", "description":
              "DEF:113  HP+80\nAccuracy+40  Magic Accuracy+40\nHaste+3%"},
    "Ring 1": {"id": 10769, "name": "Gelatinous Ring +1", "description":
               "HP+200  VIT+15\nPhysical damage taken -7%"},
    "Ring 2": {"id": 10771, "name": "Cacoethic Ring +1", "description":
               "DEX+10  Accuracy+15\nAttack+15"},
    "Waist": {"id": 26321, "name": "Reiki Yotai", "description":
              "DEF:5  STR+5  DEX+5\nHaste+6%  Dual Wield+7"},
    "Legs": {"id": 25842, "name": "Herculean Trousers", "description":
             "DEF:129  STR+10\nAccuracy+35  Attack+35\nHaste+4%"},
    "Feet": {"id": 25946, "name": "Sulevia's Leggings +2", "description":
             "DEF:91  STR+15\nAccuracy+40  Attack+40\nHaste+3%"},
}

SLOT_GRID = (
    ("Main", "Sub", "Range", "Ammo"),
    ("Head", "Neck", "Ear 1", "Ear 2"),
    ("Body", "Hands", "Ring 1", "Ring 2"),
    ("Back", "Waist", "Legs", "Feet"),
)
SLOTS = tuple(slot for row in SLOT_GRID for slot in row)


STYLE = """
QMainWindow, QWidget {
    background: #171624; color: #f5f1ff;
    font-family: 'Segoe UI', Arial; font-size: 15px;
}
QWidget#root {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #5d5d62, stop:.48 #34343a, stop:1 #5b5b60);
}
QGroupBox {
    background: qlineargradient(y1:0, y2:1, stop:0 #242344, stop:.5 #13132b, stop:1 #211e40);
    border: 2px solid #57536f; margin-top: 10px; padding: 4px;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 7px; padding: 0 5px;
    color: #eeeaf2; background: #242044; font-weight: bold;
}
QLabel#title {
    background: #19172e; border: 2px solid #5a566f; border-left: 4px solid #d6ad68;
    color: #fffaff; font-size: 22px; font-weight: bold; padding: 4px 8px;
}
QLabel#instruction {
    background: #19172e; border: 2px solid #5a566f; color: #fffaff;
    font-size: 21px; font-weight: bold; padding: 4px 10px;
}
QLabel#heading { color: #f4f0f6; font-weight: bold; }
QLabel#job { color: #35aee9; font-weight: bold; }
QLabel#detail { color: #fffaff; background: transparent; border: 0; padding: 1px; font-size: 19px; }
QLabel#muted { color: #d0ccd6; }
QPushButton {
    background: #282348; color: #f8f2ff; border: 1px solid #716893; padding: 3px;
}
QPushButton:hover { background: #493a68; border-color: #d9b36e; }
QPushButton[selected="true"] { background: #77517c; border: 1px solid #edc5e7; }
QListWidget {
    background: #17142e; border: 1px solid #5d5878; outline: 0; font-size: 20px;
}
QListWidget::item { padding: 1px 3px; border-bottom: 1px solid #2c2848; }
QListWidget::item:selected { background: #654266; color: #fff; border: 1px solid #ce8ca8; }
QPushButton#equip_slot {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #70747e, stop:.48 #5d616b, stop:1 #4f535e
    );
    border: 1px solid #8f929c; color: #f7f4ff; padding: 0px;
    font-size: 11px;
}
QPushButton#equip_slot:hover { background: #777b86; border-color: #d9bd7a; }
QPushButton#equip_slot[selected="true"] {
    background: #767984; border: 3px solid #dfa064;
}
QProgressBar { background: #2a2944; border: 1px solid #77718e; }
QProgressBar::chunk { background: #d996ad; }
QStatusBar { background: #151329; color: #c7c2d2; font-size: 11px; }
QToolTip {
    background: #17142e; color: #fffaff;
    border: 2px solid #a39ab9; padding: 7px;
    font-size: 15px;
}
"""


class IconStore:
    """Read and cache 32px item icons from the repository ZIP."""

    def __init__(self) -> None:
        self._archive: zipfile.ZipFile | None = None
        self._cache: dict[tuple[int, int], QIcon] = {}

    def icon(self, item_id: int, size: int = 32) -> QIcon:
        cache_key = (item_id, size)
        if cache_key in self._cache:
            return self._cache[cache_key]
        icon = QIcon()
        try:
            if self._archive is None:
                self._archive = zipfile.ZipFile(ICON_ARCHIVE)
            data = self._archive.read(f"{item_id}.png")
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                if size != pixmap.width():
                    pixmap = pixmap.scaled(
                        size, size,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.FastTransformation,
                    )
                icon = QIcon(pixmap)
        except (OSError, KeyError, RuntimeError, zipfile.BadZipFile):
            pass
        self._cache[cache_key] = icon
        return icon


class EquipmentMenu(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Equipment")
        self.setFixedSize(984, 640)
        self.setStyleSheet(STYLE)
        self.icons = IconStore()
        self.selected_item = ITEMS[0]
        self.selected_slot = "Main"
        self.slot_buttons: dict[str, QPushButton] = {}
        self.slot_items = {
            slot: {**item, "slot": slot}
            for slot, item in EQUIPPED.items()
        }
        self._build_ui()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        root_layout = QGridLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setHorizontalSpacing(3)
        root_layout.setVerticalSpacing(3)

        title = QLabel("Equipment")
        title.setObjectName("title")
        instruction = QLabel("Select an item from the inventory and then use")
        instruction.setObjectName("instruction")
        root_layout.addWidget(title, 0, 0)
        root_layout.addWidget(instruction, 0, 1, 1, 2)
        root_layout.addWidget(self._status_panel(), 1, 0)
        root_layout.addWidget(self._mastery_panel(), 2, 0)
        root_layout.addWidget(self._equipment_panel(), 1, 1)
        root_layout.addWidget(self._inventory_panel(), 1, 2)
        root_layout.addWidget(self._detail_panel(), 2, 1, 1, 2)
        root_layout.setColumnMinimumWidth(0, 225)
        root_layout.setColumnMinimumWidth(1, 365)
        root_layout.setColumnMinimumWidth(2, 365)
        root_layout.setRowMinimumHeight(0, 52)
        root_layout.setRowMinimumHeight(2, 174)
        root_layout.setRowStretch(1, 1)
        self.setCentralWidget(root)
        self.inventory.setCurrentRow(4)

    def _status_panel(self) -> QWidget:
        box = QGroupBox("Status")
        box.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(9, 3, 7, 7)
        layout.setSpacing(0)
        name = QLabel("Kroot", objectName="heading")
        name.setStyleSheet("font-size: 18px; font-style: italic;")
        layout.addWidget(name)
        layout.addWidget(QLabel("Lv99 Bard", objectName="job"))
        layout.addWidget(QLabel("Lv55 Ninja", objectName="muted"))
        layout.addWidget(QLabel("ILv  115 / Su5", objectName="muted"))
        for label, base, bonus in (("HP", "2297/2297", ""), ("MP", "283/283", ""),
                                   ("TP", "0", ""), ("STR", "127", "+137"),
                                   ("DEX", "135", "+83"), ("VIT", "130", "+95"),
                                   ("AGI", "132", "+117"), ("INT", "139", "+90"),
                                   ("MND", "124", "+91"), ("CHR", "133", "+106")):
            value = f"{label:<3} {base:>8}"
            if bonus:
                value += f"  <font color='#44db80'>{bonus}</font>"
            row = QLabel(value)
            row.setTextFormat(Qt.TextFormat.RichText)
            row.setStyleSheet("font-size: 17px; font-weight: bold;")
            layout.addWidget(row)
        layout.addStretch()
        return box

    def _mastery_panel(self) -> QWidget:
        box = QGroupBox("")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(0)
        for text in ("M.Level 31", "Experience Points", "37,387", "Next M.Level", "307,400"):
            label = QLabel(text)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setObjectName("heading" if text.startswith("M.") else "muted")
            label.setStyleSheet("font-size: 18px; font-style: italic;")
            layout.addWidget(label)
        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(18)
        progress.setTextVisible(False)
        progress.setFixedHeight(12)
        layout.addWidget(progress)
        layout.addStretch(1)
        return box

    def _equipment_panel(self) -> QWidget:
        box = QGroupBox("")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 6, 8, 5)
        layout.setSpacing(2)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        for row_index, row_slots in enumerate(SLOT_GRID):
            for column_index, slot in enumerate(row_slots):
                button = QPushButton()
                button.setObjectName("equip_slot")
                button.setIconSize(QSize(48, 48))
                button.setFixedSize(72, 68)
                button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                item = self.slot_items.get(slot)
                if item is not None:
                    button.setIcon(self.icons.icon(item["id"], 48))
                    button.setText("")
                    button.setToolTip(self._equipment_tooltip(item))
                else:
                    button.setText(slot)
                    button.setToolTip(f"<b>{escape(slot)}</b><br><i>Empty equipment slot</i>")
                button.setToolTipDuration(20000)
                button.setProperty("selected", slot == self.selected_slot)
                button.clicked.connect(lambda _checked=False, current=slot: self.select_slot(current))
                self.slot_buttons[slot] = button
                grid.addWidget(button, row_index, column_index)
        layout.addLayout(grid)

        totals = QLabel(
            "Attack 1004     Defense 986\n"
            "<font color='#e34f38'>●</font> 10    "
            "<font color='#e9c76e'>●</font> 20    "
            "<font color='#cd77b1'>●</font> 20    "
            "<font color='#b79f86'>●</font> 0\n"
            "<font color='#6eaac8'>●</font> 20    "
            "<font color='#d7c324'>●</font> 30    "
            "<font color='#735ca0'>●</font> 20    "
            "<font color='#6c5148'>●</font> 0"
        )
        totals.setTextFormat(Qt.TextFormat.RichText)
        totals.setObjectName("heading")
        totals.setMinimumHeight(76)
        layout.addWidget(totals)
        layout.addStretch()
        return box

    @staticmethod
    def _equipment_tooltip(item: dict) -> str:
        """Build the FFXI-style description shown over an equipment tile."""
        description = escape(str(item.get("description") or "")).replace("\n", "<br>")
        return (
            "<div style='min-width:240px'>"
            f"<span style='color:#e6c983'><b>{escape(str(item['name']))}</b></span><br>"
            f"<span style='color:#aaa5b6'>{escape(str(item['slot']))}</span>"
            f"<hr>{description}<br>"
            "<span style='color:#aaa5b6'>Item Level: 119</span>"
            "</div>"
        )

    def _inventory_panel(self) -> QWidget:
        box = QGroupBox("Storage Options >")
        box.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(5, 4, 5, 5)

        self.inventory = QListWidget()
        self.inventory.setIconSize(QSize(32, 32))
        for index, item in enumerate(ITEMS):
            row = QListWidgetItem(self.icons.icon(item["id"], 32), item["name"])
            row.setSizeHint(QSize(0, 38))
            if index in (2, 8):
                row.setForeground(QColor("#aaa5b6"))
            elif index in (6, 7):
                row.setForeground(QColor("#8cf3b2"))
            row.setData(Qt.ItemDataRole.UserRole, item)
            self.inventory.addItem(row)
        self.inventory.currentItemChanged.connect(self.show_item)
        layout.addWidget(self.inventory, 1)
        return box

    def _detail_panel(self) -> QWidget:
        box = QGroupBox("")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(12, 7, 10, 7)
        layout.setSpacing(12)
        self.detail_icon = QLabel()
        self.detail_icon.setFixedSize(76, 76)
        self.detail_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.detail_icon)
        self.details = QLabel()
        self.details.setObjectName("detail")
        self.details.setWordWrap(True)
        layout.addWidget(self.details, 1)
        return box

    def filter_inventory(self, text: str) -> None:
        needle = text.casefold()
        for index in range(self.inventory.count()):
            row = self.inventory.item(index)
            row.setHidden(needle not in row.text().casefold())

    def select_slot(self, slot: str) -> None:
        self.selected_slot = slot
        for name, button in self.slot_buttons.items():
            button.setProperty("selected", name == slot)
            button.style().unpolish(button)
            button.style().polish(button)
        self.setWindowTitle(f"Equipment - {slot}")

    def show_item(self, row: QListWidgetItem | None, _old: QListWidgetItem | None = None) -> None:
        if row is None:
            return
        self.selected_item = row.data(Qt.ItemDataRole.UserRole)
        item = self.selected_item
        self.detail_icon.setPixmap(self.icons.icon(item["id"], 64).pixmap(QSize(64, 64)))
        self.details.setText(
            f'{item["name"]}\n{item["description"]}'
        )

    def equip_selected(self) -> None:
        self.statusBar().showMessage(
            f'Would equip {self.selected_item["name"]} in {self.selected_slot} (sample)'
        )


def main() -> int:
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 15))
    window = EquipmentMenu()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
