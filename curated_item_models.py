"""Small, source-attributed corrections for gaps in imported item archives.

The generated FFXIAH archive is intentionally left untouched: it is a snapshot
and will be regenerated.  These entries contain only values verified against a
current item page and are applied before that archive.  Conditional effects are
preserved as text until the relevant simulation formula supports them.
"""

from __future__ import annotations


CURATED_ITEM_MODELS = {
    11037: {
        "stats": {"Earth Resistance": 10, "Stoneskin Bonus": 10},
        "source": "https://www.bg-wiki.com/ffxi/Earthcry_Earring",
        "effects": ("Enhances Stoneskin effect (+10 HP absorbed).",),
    },
    11590: {
        "stats": {"Healing Magic Skill": 7, "Enhancing Magic Skill": 7},
        "source": "https://www.bg-wiki.com/ffxi/Colossus%27s_Torque",
        "effects": (
            "On Lightsday: Healing Magic Skill +10 and Enhancing Magic Skill +10.",
        ),
    },
    18912: {
        "stats": {"DMG": 1, "Delay": 999},
        "source": "https://www.ffxiah.com/item/18912/ark-saber",
        "effects": ("Enchantment: Costume.",),
    },
    18913: {
        "stats": {"DMG": 1, "Delay": 999},
        "source": "https://www.ffxidb.com/items/18913",
        "effects": ("Enchantment: Costume.",),
    },
    19041: {
        "stats": {"Store TP": 4},
        "source": "https://www.bg-wiki.com/ffxi/Category%3AGrips",
        "effects": ("During Campaign: Store TP +20.",),
    },
    20542: {
        "stats": {
            "DMG": 50,
            "Delay": 54,
            "Hand-to-Hand Skill": 108,
            "Guarding Skill": 108,
            "Magic Accuracy Skill": 84,
        },
        "source": "https://www.bg-wiki.com/ffxi/Gnafron%27s_Adargas",
        "effects": (
            "Automaton maximum HP/MP depends on frame; increases automaton skill effects.",
        ),
    },
    21369: {
        "stats": {},
        "source": "https://www.bg-wiki.com/ffxi/Category%3AAmmo",
        "effects": ("Enchantment: Mog Garden Jetsam.",),
    },
    23917: {
        "stats": {
            "Defense": 130, "HP": 119, "MP": 99,
            "STR": 31, "DEX": 38, "VIT": 31, "AGI": 40, "INT": 31, "MND": 36, "CHR": 32,
            "Accuracy": 57, "Magic Accuracy": 57, "Evasion": 104, "Magic Evasion": 108,
            "Magic Defense": 4, "Gear Haste": 8, "Fast Cast": 14,
            "Regen Duration": 27,
        },
        "source": "https://www.bg-wiki.com/ffxi/Runeist_Armor_Set",
        "effects": ("Set: Accuracy, Ranged Accuracy, and Magic Accuracy +.",),
    },
    24121: {
        "stats": {
            "Defense": 154, "HP": 102,
            "STR": 40, "DEX": 37, "VIT": 28, "AGI": 32, "INT": 30, "MND": 19, "CHR": 28,
            "Accuracy": 41, "Attack": 41, "Ranged Accuracy": 41, "Ranged Attack": 41,
            "Magic Accuracy": 41, "Evasion": 108, "Magic Evasion": 88, "Magic Defense": 6,
            "Gear Haste": 6, "Store TP": 5, "Crit Rate": 5, "DT": -5,
        },
        "source": "https://www.bg-wiki.com/ffxi/Category%3ASuperior_Equipment",
        "effects": ("Set effect applies with other Perfection armor pieces.",),
    },
    26041: {
        "stats": {"Ailment Resistance Magic": 20, "Enhancing Magic Duration": -50},
        "source": "https://www.bg-wiki.com/ffxi/Category%3AA.M.A.N._Trove",
    },
    26215: {
        "stats": {"Healing Magic Skill": 15, "Fast Cast": -10, "Cure Potency": 5, "Cursna": 20},
        "source": "https://www.bg-wiki.com/ffxi/Menelaus%27s_Ring",
    },
}

# These records are intentionally not made eligible merely to silence a
# missing-data warning.  Add a model only after an authoritative item page
# publishes all displayed fields.
UNVERIFIED_ITEM_MODELS = {
    21543: "Ryofu Uchiwa is a July 2026 item whose public listing still has unknown stats.",
}
