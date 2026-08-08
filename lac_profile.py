"""Small source-preserving LuAshitacast top-level set reader/writer."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from wsdist_bridge import bridge_hash


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
    lines.append("}")
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


def write_reload_request(bridge_dir: Path, request: dict) -> Path:
    bridge_dir.mkdir(parents=True, exist_ok=True)
    target = bridge_dir / "wsdist_reload_request.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(request, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return target
