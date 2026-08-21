"""Convert the compact POLUtils DAT export into simulator item models."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


DESCRIPTION_STATS = {
    "STR", "DEX", "VIT", "AGI", "INT", "MND", "CHR", "HP", "MP",
    "Defense", "Evasion", "Magic Evasion", "Magic Defense", "Accuracy",
    "Ranged Accuracy", "Attack", "Ranged Attack", "Magic Accuracy",
    "Magic Attack", "Magic Damage", "Gear Haste", "Magic Crit Rate II",
    "Fast Cast", "DT", "PDT", "MDT", "Dual Wield", "Store TP",
    "Weapon Skill Damage", "Magic Burst Damage", "Magic Burst Damage II",
    "Skillchain Bonus", "DA", "TA", "QA", "Crit Rate", "Crit Damage",
    "PDL", "Cure Potency", "Refresh", "Regen", "Enmity", "Snapshot",
    "Rapid Shot", "Recycle", "Conserve MP", "Conserve TP", "Daken",
    "Zanshin", "Kick Attacks", "Subtle Blow", "Subtle Blow II",
    "Shield Skill", "Parrying Skill", "Magic Accuracy Skill",
}

ALIASES = {
    "def": "Defense", "defense": "Defense", "dmg": "DMG", "damage": "DMG",
    "m acc": "Magic Accuracy", "macc": "Magic Accuracy",
    "m atk": "Magic Attack", "mab": "Magic Attack",
    "magic atk bonus": "Magic Attack", "magic atk. bonus": "Magic Attack",
    "magic attack bonus": "Magic Attack", "magic def bonus": "Magic Defense",
    "magic def. bonus": "Magic Defense", "mag def bns": "Magic Defense",
    "mag eva": "Magic Evasion", "mag eva.": "Magic Evasion", "meva": "Magic Evasion",
    "r acc": "Ranged Accuracy", "racc": "Ranged Accuracy",
    "r atk": "Ranged Attack", "ratk": "Ranged Attack",
    "haste": "Gear Haste", "fastcast": "Fast Cast", "fast cast": "Fast Cast",
    "damage taken": "DT", "physical damage taken": "PDT", "magic damage taken": "MDT",
    "phys dmg taken": "PDT", "mag dmg taken": "MDT", "pdt2": "PDT", "mdt2": "MDT",
    "stp": "Store TP", "wsd": "Weapon Skill Damage", "tp bonus": "TP Bonus",
    "weapon skill damage": "Weapon Skill Damage", "skillchain bonus": "Skillchain Bonus",
    "cure potency": "Cure Potency", "cure potency received": "Cure Potency Received",
    "magic burst damage": "Magic Burst Damage", "magic burst damage ii": "Magic Burst Damage II",
    "weapon skill": "Weapon Skill", "magic accuracy skill": "Magic Accuracy Skill",
    "magic damage": "Magic Damage", "magic evasion": "Magic Evasion",
    "magic defense": "Magic Defense", "magic accuracy": "Magic Accuracy",
    "magic attack": "Magic Attack", "ranged accuracy": "Ranged Accuracy",
    "ranged attack": "Ranged Attack", "dual wield": "Dual Wield", "store tp": "Store TP",
    "subtle blow": "Subtle Blow", "subtle blow ii": "Subtle Blow II",
    "shield skill": "Shield Skill", "parrying skill": "Parrying Skill",
    "enfeebling magic skill": "Enfeebling Magic Skill",
    "elemental magic skill": "Elemental Magic Skill",
}


def normalize_key(raw: str) -> str:
    cleaned = re.sub(r"\s+", " ", raw.strip().strip('"')).replace(".", "")
    lowered = cleaned.casefold()
    return ALIASES.get(lowered, cleaned)


def parse_number(value: str, *, hexadecimal: bool = False) -> int:
    value = str(value or "0").strip()
    try:
        return int(value, 16 if hexadecimal else 10)
    except ValueError:
        return 0


def parse_description(description: str) -> dict[str, int]:
    result: dict[str, int] = {}
    text = str(description or "").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", " ", text)
    for line in text.splitlines():
        for match in re.finditer(
            r'([A-Za-z][A-Za-z\s.\-/"]*?)\s*:\s*([+-]?)\s*(\d+(?:\.\d+)?)\s*%?',
            line,
        ):
            add_stat(result, match.group(1), match.group(2), match.group(3))
        for match in re.finditer(
            r'([A-Za-z][A-Za-z\s.\-/"]*?)\s*([+-])\s*(\d+(?:\.\d+)?)\s*%?',
            line,
        ):
            add_stat(result, match.group(1), match.group(2), match.group(3))
    return result


def add_stat(result: dict[str, int], raw_key: str, sign: str, number: str) -> None:
    key = normalize_key(raw_key)
    if key not in DESCRIPTION_STATS:
        return
    value = float((sign or "") + number)
    result[key] = int(value) if value.is_integer() else value


def model(item: dict) -> dict:
    item_id = parse_number(item.get("item_id"))
    stats = parse_description(item.get("description", ""))
    damage = parse_number(item.get("damage"))
    delay = parse_number(item.get("delay"))
    skill = parse_number(item.get("skill"), hexadecimal=True)
    if damage:
        stats["DMG"] = damage
    if delay:
        stats["Delay"] = delay
    return {
        "Name": item.get("name", ""),
        "Name2": item.get("name", ""),
        "Item ID": item_id,
        "stats": stats,
        "slots_mask": parse_number(item.get("slots_mask"), hexadecimal=True),
        "jobs_mask": parse_number(item.get("jobs_mask"), hexadecimal=True),
        "skill": skill,
        "type": parse_number(item.get("type"), hexadecimal=True),
        "level": parse_number(item.get("level")),
        "item_level": parse_number(item.get("item_level")),
        "description": item.get("description", ""),
        "complete": bool(stats),
        "data_source": "POLUtils native FFXI DAT resources",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = json.loads(args.input.read_text(encoding="utf-8"))
    models = {}
    for item in raw.get("items", []):
        converted = model(item)
        if converted["Item ID"] and converted["Name"]:
            models[str(converted["Item ID"])] = converted
    document = {
        "source": raw.get("source", "POLUtils native FFXI DAT resources"),
        "generated_utc": raw.get("generated_utc"),
        "models": models,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(models)} POLUtils models to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
