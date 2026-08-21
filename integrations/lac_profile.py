"""Small source-preserving LuAshitacast top-level set reader/writer."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from integrations.wsdist_bridge import bridge_hash


@dataclass(frozen=True)
class SetEntry:
    name: str
    start: int
    end: int
    value_start: int
    value_end: int


def _skip_string(text: str, index: int) -> int:
    quote = text[index]
    index += 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
        elif text[index] == quote:
            return index + 1
        else:
            index += 1
    raise ValueError("Unterminated string in profile.")


def _skip_comment(text: str, index: int) -> int:
    if text.startswith("--[[", index):
        end = text.find("]]", index + 4)
        if end < 0:
            raise ValueError("Unterminated block comment in profile.")
        return end + 2
    end = text.find("\n", index + 2)
    return len(text) if end < 0 else end + 1


def _skip_space(text: str, index: int) -> int:
    while index < len(text):
        if text[index].isspace():
            index += 1
        elif text.startswith("--", index):
            index = _skip_comment(text, index)
        else:
            break
    return index


def _find_sets_table(text: str) -> int:
    pattern = re.compile(r"(?:local\s+sets|local\s+Sets|profile\.Sets)\s*=\s*(?:T\s*)?\{")
    index = 0
    while index < len(text):
        if text.startswith("--", index):
            index = _skip_comment(text, index)
            continue
        if text[index] in "'\"":
            index = _skip_string(text, index)
            continue
        match = pattern.match(text, index)
        if match:
            return text.find("{", match.start(), match.end())
        index += 1
    raise ValueError("Could not locate a supported LuAshitacast sets table.")


def _key_at(text: str, index: int) -> tuple[str, int] | None:
    if text[index] == "[":
        close = index + 1
        quote = None
        while close < len(text):
            if quote:
                if text[close] == "\\":
                    close += 2
                    continue
                if text[close] == quote:
                    quote = None
            elif text[close] in "'\"":
                quote = text[close]
            elif text[close] == "]":
                raw = text[index + 1:close].strip()
                if len(raw) >= 2 and raw[0] in "'\"" and raw[-1] == raw[0]:
                    raw = raw[1:-1]
                return raw, close + 1
            close += 1
        raise ValueError("Unterminated bracketed set key.")
    match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[index:])
    if match:
        return match.group(0), index + len(match.group(0))
    return None


def parse_set_entries(text: str) -> tuple[int, list[SetEntry], int]:
    open_index = _find_sets_table(text)
    depth = 1
    index = open_index + 1
    entries: list[SetEntry] = []
    while index < len(text):
        if text.startswith("--", index):
            index = _skip_comment(text, index)
            continue
        if text[index] in "'\"":
            index = _skip_string(text, index)
            continue
        char = text[index]
        if char == "{":
            depth += 1
            index += 1
            continue
        if char == "}":
            depth -= 1
            if depth == 0:
                return open_index, entries, index
            index += 1
            continue
        if depth == 1 and (char.isalpha() or char == "_" or char == "["):
            key = _key_at(text, index)
            if key is None:
                index += 1
                continue
            name, after_key = key
            cursor = _skip_space(text, after_key)
            if cursor >= len(text) or text[cursor] != "=":
                index = after_key
                continue
            value_start = _skip_space(text, cursor + 1)
            if value_start >= len(text):
                raise ValueError(f"Set {name!r} has no value.")
            value_depth = 0
            scan = value_start
            while scan < len(text):
                if text.startswith("--", scan):
                    scan = _skip_comment(text, scan)
                    continue
                if text[scan] in "'\"":
                    scan = _skip_string(text, scan)
                    continue
                if text[scan] == "{":
                    value_depth += 1
                elif text[scan] == "}":
                    if value_depth == 0:
                        break
                    value_depth -= 1
                elif text[scan] == "," and value_depth == 0:
                    break
                scan += 1
            value_end = scan
            static_table = text[value_start] == "{" or (
                text[value_start] == "T" and _skip_space(text, value_start + 1) < len(text)
                and text[_skip_space(text, value_start + 1)] == "{"
            )
            if value_start >= value_end or not static_table:
                raise ValueError(f"Set {name!r} is computed or not a static table.")
            entries.append(SetEntry(name, index, value_end, value_start, value_end))
            index = scan + (1 if scan < len(text) and text[scan] == "," else 0)
            continue
        index += 1
    raise ValueError("Unterminated LuAshitacast sets table.")


def _lua_quote(value: object) -> str:
    text = str(value or "")
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'").replace("\r", "\\r").replace("\n", "\\n") + "'"


def serialize_item(item: dict | None) -> str | None:
    if not item:
        return None
    lac = item.get("LAC") or {}
    name = lac.get("Name") or item.get("Name")
    if not name or name == "Empty":
        return None
    fields = ["Name = " + _lua_quote(name)]
    for key in ("AugPath", "AugRank", "AugTrial", "Bag"):
        value = lac.get(key)
        if value not in (None, ""):
            fields.append(f"{key} = " + (str(int(value)) if key in ("AugRank", "AugTrial") else _lua_quote(value)))
    augments = lac.get("Augment") or item.get("Augments") or []
    if augments:
        fields.append("Augment = { " + ", ".join(f"[{i}] = {_lua_quote(value)}" for i, value in enumerate(augments, 1)) + " }")
    return "{ " + ", ".join(fields) + " }"


SLOT_MAP = {"main": "Main", "sub": "Sub", "ranged": "Range", "ammo": "Ammo", "head": "Head",
            "body": "Body", "hands": "Hands", "legs": "Legs", "feet": "Feet", "neck": "Neck",
            "waist": "Waist", "ear1": "Ear1", "ear2": "Ear2", "ring1": "Ring1", "ring2": "Ring2", "back": "Back"}


def serialize_set(name: str, equipment: dict[str, dict]) -> str:
    if not name or any(char in name for char in "\r\n\0"):
        raise ValueError("Set name is empty or contains a control character.")
    lines = ["[" + _lua_quote(name) + "] = {"]
    for slot in SLOT_MAP:
        literal = serialize_item(equipment.get(slot))
        if literal:
            lines.append(f"    {SLOT_MAP[slot]} = {literal},")
    # Keep the closing table brace aligned with the fields.  This makes
    # generated sets match the surrounding LAC profile indentation and keeps
    # the writer's trailing comma visually attached to the set.
    lines.append("    }")
    return "\n".join(lines)


def write_set(profile_path: Path, set_name: str, equipment: dict[str, dict], *, expected_hash: str,
              overwrite: bool = False, backup_dir: Path | None = None) -> tuple[Path, str]:
    profile_path = profile_path.resolve()
    if not profile_path.exists():
        raise FileNotFoundError(profile_path)
    source = profile_path.read_text(encoding="utf-8")
    current_hash = bridge_hash(source)
    if expected_hash and current_hash != expected_hash:
        raise RuntimeError("LuAshitacast profile changed since WSDist imported it; refresh before saving.")
    _, entries, close_index = parse_set_entries(source)
    matches = [entry for entry in entries if entry.name == set_name]
    if len(matches) > 1:
        raise RuntimeError(f"Profile contains duplicate top-level set name: {set_name}")
    if matches and not overwrite:
        raise FileExistsError(f"Set already exists: {set_name}")
    generated = serialize_set(set_name, equipment)
    replacement = generated + ","
    if matches:
        entry = matches[0]
        updated = source[:entry.start] + replacement + source[entry.end + (1 if entry.end < len(source) and source[entry.end] == ',' else 0):]
    else:
        insertion = "\n    " + replacement + "\n"
        updated = source[:close_index] + insertion + source[close_index:]
    # Parse before committing; this catches malformed generated table syntax and
    # guarantees the source-preserving writer still recognizes its own output.
    parse_set_entries(updated)
    backup_dir = backup_dir or profile_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{profile_path.stem}_{__import__('datetime').datetime.now():%Y.%m.%d_%H.%M.%S_%f}.lua"
    backup.write_text(source, encoding="utf-8", newline="")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=profile_path.parent,
                                     prefix=profile_path.name + ".", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(updated)
    os.replace(temporary, profile_path)
    return backup, bridge_hash(updated)


def write_profile_source(profile_path: Path, source: str, *, expected_hash: str,
                         backup_dir: Path | None = None) -> tuple[Path, str]:
    """Atomically save a manually edited LAC file with backup/conflict protection."""
    profile_path = profile_path.resolve()
    if not profile_path.exists():
        raise FileNotFoundError(profile_path)
    if not isinstance(source, str) or not source.strip():
        raise ValueError("LuAshitacast profile cannot be empty.")
    if "\x00" in source:
        raise ValueError("LuAshitacast profile contains a NUL character.")
    current = profile_path.read_text(encoding="utf-8")
    if expected_hash and bridge_hash(current) != expected_hash:
        raise RuntimeError(
            "LuAshitacast profile changed on disk after it was opened; reload before saving."
        )
    backup_dir = backup_dir or profile_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / (
        f"{profile_path.stem}_{__import__('datetime').datetime.now():%Y.%m.%d_%H.%M.%S_%f}.lua"
    )
    backup.write_text(current, encoding="utf-8", newline="")
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=profile_path.parent,
        prefix=profile_path.name + ".", suffix=".tmp", delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(source)
    os.replace(temporary, profile_path)
    return backup, bridge_hash(source)


def prepare_managed_update(source: str, sets: dict[str, dict[str, dict]], *,
                           add_wsdist_cycle: bool = True) -> str:
    """Return a validated profile update for an atomic managed-set write."""
    updated = source
    for set_name, equipment in sets.items():
        _, entries, close_index = parse_set_entries(updated)
        matches = [entry for entry in entries if entry.name == set_name]
        if len(matches) > 1:
            raise RuntimeError(f"Profile contains duplicate top-level set name: {set_name}")
        replacement = serialize_set(set_name, equipment) + ","
        if matches:
            entry = matches[0]
            trailing = 1 if entry.end < len(updated) and updated[entry.end] == "," else 0
            updated = updated[:entry.start] + replacement + updated[entry.end + trailing:]
        else:
            updated = updated[:close_index] + "\n    " + replacement + "\n" + updated[close_index:]

    if add_wsdist_cycle and "'WSDist'" not in updated and '"WSDist"' not in updated:
        cycle_pattern = re.compile(
            r"(gcdisplay\.CreateCycle\(\s*['\"]MeleeSet['\"]\s*,\s*\{)(.*?)(\}\s*\)\s*;?)",
            re.DOTALL,
        )
        match = cycle_pattern.search(updated)
        if match:
            body = match.group(2).rstrip()
            separator = " " if body.endswith(",") else ", "
            explicit = [int(value) for value in re.findall(r"\[(\d+)\]\s*=", body)]
            field = f"[{max(explicit) + 1}] = 'WSDist'" if explicit else "'WSDist'"
            replacement = match.group(1) + body + separator + field + match.group(3)
            updated = updated[:match.start()] + replacement + updated[match.end():]
        else:
            initialize = re.search(r"^(?P<indent>\s*)gcinclude\.Initialize\(\)\s*;?", updated, re.MULTILINE)
            if initialize is None:
                raise RuntimeError(
                    "Could not find a safe MeleeSet cycle or gcinclude.Initialize() insertion point."
                )
            indent = initialize.group("indent")
            insertion = (
                "\n" + indent + "gcdisplay.CreateCycle('MeleeSet', "
                "{ 'Default', 'Hybrid', 'Acc', 'WSDist' });"
            )
            updated = updated[:initialize.end()] + insertion + updated[initialize.end():]
    parse_set_entries(updated)
    return updated


def prepare_profile_builder_update(source: str, sets: dict[str, dict[str, dict]], *,
                                   defense_modes: tuple[str, ...] = ("None", "Dt", "Evasion", "MEVA")) -> str:
    """Apply a complete Profile Builder catalog and its small LAC adapter.

    Set tables remain source-preserving top-level entries so old profiles and
    GearSetBuilder can continue reading them.  A durable comment marker makes
    the managed ownership visible without touching unrelated Lua handlers.
    """
    updated = prepare_managed_update(source, sets, add_wsdist_cycle=False)
    marker = "-- WSDIST-PROFILE-BUILDER v1"
    if marker not in updated:
        table_start, _entries, _table_end = parse_set_entries(updated)
        updated = updated[:table_start + 1] + "\n    " + marker + "\n" + updated[table_start + 1:]
    cycle_pattern = re.compile(
        r"gcdisplay\.CreateCycle\(\s*['\"]MeleeSet['\"]\s*,\s*\{.*?\}\s*\)\s*;?",
        re.DOTALL,
    )
    melee_cycle = "gcdisplay.CreateCycle('MeleeSet', { [1] = 'Default', [2] = 'Acc', [3] = 'HighAcc', [4] = 'Hybrid' });"
    if cycle_pattern.search(updated):
        updated = cycle_pattern.sub(melee_cycle, updated, count=1)
    else:
        initialize = re.search(r"^(?P<indent>\s*)gcinclude\.Initialize\([^\n]+\)\s*;?", updated, re.MULTILINE)
        if initialize is None:
            raise RuntimeError("Could not find gcinclude.Initialize() for Profile Builder cycles.")
        updated = updated[:initialize.end()] + "\n" + initialize.group("indent") + melee_cycle + updated[initialize.end():]
    hybrid_accuracy_pattern = re.compile(
        r"gcdisplay\.CreateCycle\(\s*['\"]HybridAccuracy['\"]\s*,\s*\{.*?\}\s*\)\s*;?",
        re.DOTALL,
    )
    hybrid_accuracy_cycle = (
        "gcdisplay.CreateCycle('HybridAccuracy', { [1] = 'Default', "
        "[2] = 'Acc', [3] = 'HighAcc' });"
    )
    if hybrid_accuracy_pattern.search(updated):
        updated = hybrid_accuracy_pattern.sub(hybrid_accuracy_cycle, updated, count=1)
    else:
        position = updated.find(melee_cycle)
        updated = (
            updated[:position + len(melee_cycle)] + "\n    " + hybrid_accuracy_cycle
            + updated[position + len(melee_cycle):]
        )
    defense_cycle = "gcdisplay.CreateCycle('DefenseSet', { " + ", ".join(
        f"[{index}] = {_lua_quote(mode)}" for index, mode in enumerate(defense_modes, 1)
    ) + " });"
    if "CreateCycle('DefenseSet'" not in updated and 'CreateCycle("DefenseSet"' not in updated:
        position = updated.find(melee_cycle)
        updated = updated[:position + len(melee_cycle)] + "\n    " + defense_cycle + updated[position + len(melee_cycle):]
    # Adapter applies the selected defense overlay after TP/idle handling.
    adapter = (
        "\n-- WSDIST-PROFILE-BUILDER defense adapter\n"
        "local function applyWSDistDefenseSet()\n"
        "    local defenseSet = gcdisplay.GetCycle('DefenseSet') or 'None';\n"
        "    if (gcdisplay.GetToggle('DTset') == true) then defenseSet = 'Dt'; end\n"
        "    if (defenseSet ~= 'None' and sets[defenseSet] ~= nil) then gFunc.EquipSet(sets[defenseSet]); end\n"
        "end\n"
    )
    if "WSDIST-PROFILE-BUILDER defense adapter" not in updated:
        anchor = re.search(r"profile\.HandleDefault\s*=\s*function\(\)", updated)
        if anchor is None:
            raise RuntimeError("Could not locate profile.HandleDefault for Profile Builder adapter.")
        updated = updated[:anchor.start()] + adapter + "\n" + updated[anchor.start():]
        # Apply just before HandleDefault closes: profiles conventionally call
        # gcinclude.CheckDefault() near the end, so attach immediately after it.
        start = updated.find("profile.HandleDefault", anchor.start() + len(adapter))
        finish = updated.find("end", start)
        check = updated.find("gcinclude.CheckDefault", start, finish + 2000)
        if check >= 0:
            line_end = updated.find("\n", check)
            updated = updated[:line_end + 1] + "    applyWSDistDefenseSet();\n" + updated[line_end + 1:]
    hybrid_adapter = (
        "\n-- WSDIST-PROFILE-BUILDER hybrid TP adapter\n"
        "local function applyWSDistHybridTpSet()\n"
        "    if ((gcdisplay.GetCycle('MeleeSet') or 'Default') ~= 'Hybrid') then return; end\n"
        "    local accuracy = gcdisplay.GetCycle('HybridAccuracy') or 'Default';\n"
        "    local setName = 'Tp_Hybrid';\n"
        "    if (accuracy ~= 'Default') then setName = setName .. '_' .. accuracy; end\n"
        "    if (sets[setName] ~= nil) then gFunc.EquipSet(sets[setName]); end\n"
        "end\n"
    )
    if "WSDIST-PROFILE-BUILDER hybrid TP adapter" not in updated:
        anchor = re.search(r"profile\.HandleDefault\s*=\s*function\(\)", updated)
        if anchor is None:
            raise RuntimeError("Could not locate profile.HandleDefault for Hybrid TP adapter.")
        updated = updated[:anchor.start()] + hybrid_adapter + "\n" + updated[anchor.start():]
        start = updated.find("profile.HandleDefault", anchor.start() + len(hybrid_adapter))
        finish = updated.find("end", start)
        check = updated.find("gcinclude.CheckDefault", start, finish + 2000)
        if check >= 0:
            line_end = updated.find("\n", check)
            updated = updated[:line_end + 1] + "    applyWSDistHybridTpSet();\n" + updated[line_end + 1:]
    parse_set_entries(updated)
    return updated


def write_managed_sets(profile_path: Path, sets: dict[str, dict[str, dict]], *,
                       expected_hash: str, backup_dir: Path | None = None,
                       add_wsdist_cycle: bool = True) -> tuple[Path, str]:
    """Write a TP/WS managed pair as one backed-up atomic transaction."""
    profile_path = profile_path.resolve()
    source = profile_path.read_text(encoding="utf-8")
    if expected_hash and bridge_hash(source) != expected_hash:
        raise RuntimeError("LuAshitacast profile changed since WSDist imported it; refresh before saving.")
    updated = prepare_managed_update(source, sets, add_wsdist_cycle=add_wsdist_cycle)
    backup_dir = backup_dir or profile_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{profile_path.stem}_{__import__('datetime').datetime.now():%Y.%m.%d_%H.%M.%S_%f}.lua"
    backup.write_text(source, encoding="utf-8", newline="")
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=profile_path.parent,
        prefix=profile_path.name + ".", suffix=".tmp", delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(updated)
    os.replace(temporary, profile_path)
    return backup, bridge_hash(updated)


def write_profile_builder_sets(profile_path: Path, sets: dict[str, dict[str, dict]], *,
                               expected_hash: str, defense_modes: tuple[str, ...] = ("None", "Dt", "Evasion", "MEVA"),
                               backup_dir: Path | None = None) -> tuple[Path, str]:
    """Atomically publish a Profile Builder catalog with stale-file protection."""
    profile_path = profile_path.resolve()
    source = profile_path.read_text(encoding="utf-8")
    if expected_hash and bridge_hash(source) != expected_hash:
        raise RuntimeError("LuAshitacast profile changed since WSDist imported it; refresh before saving.")
    updated = prepare_profile_builder_update(source, sets, defense_modes=defense_modes)
    backup_dir = backup_dir or profile_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{profile_path.stem}_{__import__('datetime').datetime.now():%Y.%m.%d_%H.%M.%S_%f}.lua"
    backup.write_text(source, encoding="utf-8", newline="")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=profile_path.parent,
                                     prefix=profile_path.name + ".", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(updated)
    os.replace(temporary, profile_path)
    return backup, bridge_hash(updated)


def prepare_set_renames(source: str, renames: dict[str, str]) -> str:
    """Rename supported top-level sets and their common literal references."""
    renames = {old: new for old, new in renames.items() if old and new and old != new}
    if not renames:
        return source
    _, entries, _ = parse_set_entries(source)
    existing = {entry.name for entry in entries}
    for old, new in renames.items():
        if old not in existing:
            raise KeyError(f"Set does not exist: {old}")
        if new in existing and new not in renames:
            raise FileExistsError(f"Canonical set name already exists: {new}")
    if len(set(renames.values())) != len(renames):
        raise ValueError("Two source sets map to the same canonical name.")

    updated = source
    for entry in sorted((entry for entry in entries if entry.name in renames),
                        key=lambda value: value.start, reverse=True):
        prefix = updated[entry.start:entry.value_start]
        equals = prefix.rfind("=")
        if equals < 0:
            raise ValueError(f"Could not rewrite set key: {entry.name}")
        updated = (
            updated[:entry.start] + "[" + _lua_quote(renames[entry.name]) + "] "
            + prefix[equals:] + updated[entry.value_start:]
        )

    for old, new in sorted(renames.items(), key=lambda item: len(item[0]), reverse=True):
        updated = re.sub(rf"\bsets\.{re.escape(old)}\b", f"sets[{_lua_quote(new)}]", updated)
        for quote in ("'", '"'):
            updated = updated.replace(f"sets[{quote}{old}{quote}]", f"sets[{_lua_quote(new)}]")
            updated = updated.replace(f"{quote}{old}{quote}", _lua_quote(new))

    # Dynamic handlers commonly concatenate a family prefix with MeleeSet.
    family_changes = {}
    for old, new in renames.items():
        old_family = old.split("_", 1)[0]
        new_family = new.split("_", 1)[0]
        if old_family != new_family:
            family_changes[old_family + "_"] = new_family + "_"
    for old_prefix, new_prefix in family_changes.items():
        updated = updated.replace(_lua_quote(old_prefix), _lua_quote(new_prefix))

    parse_set_entries(updated)
    for old in renames:
        if re.search(rf"\bsets\.{re.escape(old)}\b|sets\[['\"]{re.escape(old)}['\"]\]", updated):
            raise RuntimeError(f"Unresolved reference remains for renamed set: {old}")
    return updated


def write_renamed_profile(profile_path: Path, renames: dict[str, str], *,
                          expected_hash: str, backup_dir: Path | None = None) -> tuple[Path, str]:
    profile_path = profile_path.resolve()
    source = profile_path.read_text(encoding="utf-8")
    if expected_hash and bridge_hash(source) != expected_hash:
        raise RuntimeError("LuAshitacast profile changed since WSDist imported it; refresh before saving.")
    updated = prepare_set_renames(source, renames)
    backup_dir = backup_dir or profile_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{profile_path.stem}_{__import__('datetime').datetime.now():%Y.%m.%d_%H.%M.%S_%f}.lua"
    backup.write_text(source, encoding="utf-8", newline="")
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=profile_path.parent,
        prefix=profile_path.name + ".", suffix=".tmp", delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(updated)
    os.replace(temporary, profile_path)
    return backup, bridge_hash(updated)


def write_reload_request(bridge_dir: Path, request: dict) -> Path:
    bridge_dir.mkdir(parents=True, exist_ok=True)
    target = bridge_dir / "wsdist_reload_request.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(request, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return target
