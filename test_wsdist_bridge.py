import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lac_profile import (
    bridge_hash, parse_set_entries, prepare_managed_update, prepare_profile_builder_update, prepare_set_renames,
    serialize_set, write_set,
)
from wsdist_bridge import BridgeStore, _gear_record, hoxne_stat_bonus


class BridgeTests(unittest.TestCase):
    def test_transferability_preserves_exclusive_flags(self):
        record = {
            "item_id": 99, "name": "Transfer Test", "slots_mask": 1 << 4,
            "jobs_mask": 1 << 1, "accessible_count": 1, "model_complete": True,
            "resource_flags": 0x2000,
        }
        item = _gear_record(record)
        self.assertTrue(item["Exclusive"])
        self.assertFalse(item["Transferable"])

    def test_halasz_magic_crit_rate_is_not_physical_crit_rate(self):
        item = _gear_record({
            "item_id": 27535, "name": "Halasz Earring", "slots_mask": 1 << 11,
            "jobs_mask": 1 << 1, "accessible_count": 1, "model_complete": True,
            "stats": {"MP": 45, "Crit Rate": 14},
        })
        self.assertEqual(item["Magic Crit Rate II"], 14)
        self.assertNotIn("Crit Rate", item)

    def bridge(self):
        return {
            "schema_version": 1,
            "character": {"key": "tester_123", "name": "Tester", "server_id": 123},
            "inventory": {"unique_items": 2, "accessible_items": 2},
            "items": [
                {"key": "1|base", "item_id": 1, "name": "Owned Helm", "slots_mask": 1 << 4,
                 "jobs_mask": 1 << 1, "accessible_count": 1, "total_count": 1, "stats": {"STR": 10},
                 "model_complete": True, "lac": {"Name": "Owned Helm"}},
                {"key": "2|unknown", "item_id": 2, "name": "Unknown Helm", "slots_mask": 1 << 4,
                 "jobs_mask": 1 << 1, "accessible_count": 1, "total_count": 1, "stats": {},
                 "model_complete": False, "lac": {"Name": "Unknown Helm"}},
                {"key": "3|shield", "item_id": 3, "name": "Aegis", "slots_mask": 1 << 1,
                 "jobs_mask": 1 << 7, "weapon_type": "5", "skill": 0,
                 "accessible_count": 1, "total_count": 1, "stats": {},
                 "model_complete": True, "lac": {"Name": "Aegis"}},
            ],
            "profiles": [{"job": "WAR", "source_hash": "old", "sets": [{
                "name": "TP", "slots": {"Head": {"item_id": 1, "name": "Owned Helm", "augments": []}},
            }]}],
        }

    def test_catalog_excludes_incomplete_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config" / "addons" / "gearsetbuilder" / "tester_123" / "wsdist_bridge.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(self.bridge()), encoding="utf-8")
            store = BridgeStore(root)
            store.load(path)
            self.assertIn("1|base", store.by_key)
            self.assertNotIn("2|unknown", [item["Bridge Key"] for item in store.by_slot["head"]])
            self.assertEqual(store.by_slot["head"][0]["Accessible Count"], 1)

    def test_bridge_classifies_shields_for_sub_slot_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config" / "addons" / "gearsetbuilder" / "tester_123" / "wsdist_bridge.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(self.bridge()), encoding="utf-8")
            store = BridgeStore(root)
            store.load(path)
            self.assertEqual(store.by_slot["sub"][0]["Type"], "Shield")
            self.assertEqual(store.by_slot["sub"][0]["Name"], "Aegis")

    def test_hoxne_rank_replaces_static_bridge_stats(self):
        bridge = self.bridge()
        bridge["items"].append({
            "key": "26120|hoxne", "item_id": 26120, "name": "Hoxne Earring",
            "slots_mask": 1 << 11, "jobs_mask": (1 << 23) - 2,
            "accessible_count": 1, "total_count": 1,
            "stats": {stat: 5 for stat in ("STR", "DEX", "VIT", "AGI", "INT", "MND", "CHR")},
            "model_complete": True, "lac": {"Name": "Hoxne Earring"},
        })
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config" / "addons" / "gearsetbuilder" / "tester_123" / "wsdist_bridge.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(bridge), encoding="utf-8")
            store = BridgeStore(root)
            store.load(path)
            store.set_hoxne_mastery_rank(9)
            hoxne = store.by_key["26120|hoxne"]
            self.assertEqual(hoxne["Name2"], "Hoxne Earring MR09")
            self.assertTrue(all(hoxne[stat] == 25 for stat in ("STR", "DEX", "VIT", "AGI", "INT", "MND", "CHR")))
        self.assertEqual(hoxne_stat_bonus(1), -30)
        self.assertEqual(hoxne_stat_bonus(10), 30)

    def test_discover_characters_returns_readable_selector_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config" / "addons" / "gearsetbuilder" / "tester_123" / "wsdist_bridge.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(self.bridge()), encoding="utf-8")
            store = BridgeStore(root)
            self.assertEqual(store.discover_characters(), [("Tester (tester_123)", path)])

    def test_profile_parser_handles_comments_and_t_tables(self):
        source = """local sets = {\n  ['TP'] = T{ Head = { Name = 'Hat' }, }, -- } in comment\n  WS = { Body = { Name = 'Body {x}' }, },\n};\nreturn profile;\n"""
        self.assertEqual([entry.name for entry in parse_set_entries(source)[1]], ["TP", "WS"])

    def test_profile_writer_backups_and_stale_hash(self):
        source = "local sets = { ['TP'] = { Main = { Name = 'Old' }, }, };\nreturn profile;\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "WAR.lua"
            path.write_text(source, encoding="utf-8")
            backup, new_hash = write_set(path, "TP", {"main": {"Name": "New"}},
                                         expected_hash=bridge_hash(source), overwrite=True)
            self.assertTrue(backup.exists())
            self.assertEqual(new_hash, bridge_hash(path.read_text(encoding="utf-8")))
            self.assertIn("Name = 'New'", path.read_text(encoding="utf-8"))
            with self.assertRaises(RuntimeError):
                write_set(path, "TP", {}, expected_hash="stale", overwrite=True)

    def test_serializer_preserves_lac_augments(self):
        text = serialize_set("WS", {"main": {"Name": "Sword", "LAC": {
            "Name": "Sword", "AugPath": "A", "AugRank": 15, "Augment": ["STR +10"]
        }}})
        self.assertIn("AugPath = 'A'", text)
        self.assertIn("AugRank = 15", text)
        self.assertIn("STR +10", text)

    def test_schema_two_uses_exact_embedded_profile_stats(self):
        bridge = self.bridge()
        bridge["schema_version"] = 2
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config" / "addons" / "gearsetbuilder" / "tester_123" / "wsdist_bridge.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(bridge), encoding="utf-8")
            store = BridgeStore(root)
            store.load(path)
            item = store.resolve_profile_item({
                "item_id": 99999, "name": "Profile-only Hat",
                "key": "99999|profile|hat", "slots_mask": 1 << 4,
                "jobs_mask": (1 << 23) - 2, "stats": {"STR": 42},
                "model_complete": True, "lac": {"Name": "Profile-only Hat"},
            })
            self.assertEqual(item["STR"], 42)
            self.assertEqual(item["Name"], "Profile-only Hat")

    def test_managed_pair_adds_safe_indexed_wsdist_cycle(self):
        source = (
            "local sets = { Tp_Default = { Head = 'Old' }, Savage_Default = {}, };\n"
            "profile.OnLoad = function()\n"
            "  gcdisplay.CreateCycle('MeleeSet', {[1] = 'Default', [2] = 'Hybrid', [3] = 'Acc'});\n"
            "end\nreturn profile;\n"
        )
        updated = prepare_managed_update(source, {
            "Tp_WSDist": {"head": {"Name": "New TP"}},
            "Savage_WSDist": {"body": {"Name": "New WS"}},
        })
        self.assertIn("[4] = 'WSDist'", updated)
        self.assertIn("['Tp_WSDist']", updated)
        self.assertIn("['Savage_WSDist']", updated)
        parse_set_entries(updated)

    def test_profile_builder_update_is_idempotent_and_adds_defense_adapter(self):
        source = (
            "local sets = { Tp_Default = { Head = 'Old' }, Dt = {}, };\n"
            "profile.OnLoad = function()\n"
            "  gcinclude.Initialize(T{'weapon'});\n"
            "  gcdisplay.CreateCycle('MeleeSet', {[1] = 'Default', [2] = 'Hybrid'});\n"
            "end\n"
            "profile.HandleDefault = function()\n"
            "  gcinclude.CheckDefault();\n"
            "end\nreturn profile;\n"
        )
        first = prepare_profile_builder_update(source, {
            "Tp_Default": {"head": {"Name": "New TP"}},
            "Tp_HighAcc": {"body": {"Name": "New Acc"}},
            "Evasion": {"feet": {"Name": "Evasion Boots"}},
        })
        second = prepare_profile_builder_update(first, {
            "Tp_Default": {"head": {"Name": "New TP"}},
            "Tp_HighAcc": {"body": {"Name": "New Acc"}},
            "Evasion": {"feet": {"Name": "Evasion Boots"}},
        })
        self.assertEqual(first, second)
        self.assertIn("WSDIST-PROFILE-BUILDER v1", first)
        self.assertIn("DefenseSet", first)
        self.assertIn("HighAcc", first)
        parse_set_entries(first)

    def test_guided_rename_updates_static_and_dynamic_references(self):
        source = (
            "local sets = { Savage_Default = {}, Savage_Acc = {}, };\n"
            "gFunc.EquipSet(sets.Savage_Default);\n"
            "gFunc.EquipSet('Savage_' .. gcdisplay.GetCycle('MeleeSet'));\n"
            "return profile;\n"
        )
        updated = prepare_set_renames(source, {
            "Savage_Default": "SavageBlade_Default",
            "Savage_Acc": "SavageBlade_Acc",
        })
        self.assertIn("sets['SavageBlade_Default']", updated)
        self.assertIn("'SavageBlade_' ..", updated)
        self.assertNotIn("Savage_Default", updated)

    def test_limbus_base_stats_do_not_inherit_rank_30_models(self):
        records = []
        for item_id, name, slots, stats in [
            (26119, "Alabaster Earring", 1 << 11, {
                "Defense": 10, "HP": 100, "Gear Haste": 5, "DT": -5,
                "Pet:Accuracy": 15, "Ranged Accuracy": 15, "Magic Accuracy": 15,
                "STR": 10, "DEX": 10, "VIT": 10, "AGI": 10, "INT": 10,
                "MND": 10, "CHR": 10, "Store TP": 5}),
            (26234, "Murky Ring", (1 << 13) | (1 << 14), {
                "Defense": 10, "MP": 30, "Spell interruption rate down": -3,
                "DT": -10, "Pet:Accuracy": 15, "Pet:Ranged Accuracy": 15,
                "Pet:Magic Accuracy": 15, "Accuracy": 15, "Ranged Accuracy": 15,
                "Magic Accuracy": 15, "Evasion": 10, "Magic Evasion": 10,
                "Crit Rate": 5}),
            (26275, "Alabaster Mantle", 1 << 15, {
                "Defense": 20, "Weapon Skill Damage": 11, "Accuracy": 25,
                "Attack": 25, "Ranged Accuracy": 25, "Ranged Attack": 25,
                "STR": 15, "DEX": 15}),
            (26276, "Murky Mantle", 1 << 15, {
                "Defense": 18, "Magic Damage": 58, "Magic Accuracy": 25,
                "INT": 15}),
        ]:
            records.append({"key": f"{item_id}|base", "item_id": item_id,
                            "name": name, "slots_mask": slots, "jobs_mask": 1 << 1,
                            "accessible_count": 1, "total_count": 1, "stats": stats,
                            "model_complete": True, "augment_type": "Unaugmented",
                            "lac": {"Name": name}})
        bridge = self.bridge()
        bridge["items"] = records
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config" / "addons" / "gearsetbuilder" / "tester_123" / "wsdist_bridge.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(bridge), encoding="utf-8")
            store = BridgeStore(root)
            store.load(path)
            earring = store.by_key["26119|base"]
            ring = store.by_key["26234|base"]
            alabaster_mantle = store.by_key["26275|base"]
            murky_mantle = store.by_key["26276|base"]
            self.assertEqual(earring["HP"], 100)
            self.assertNotIn("STR", earring)
            self.assertEqual(ring["MP"], 30)
            self.assertEqual(ring["Spell interruption rate down"], -3)
            self.assertNotIn("Accuracy", ring)
            self.assertNotIn("Evasion", ring)
            self.assertEqual(alabaster_mantle["Defense"], 20)
            self.assertNotIn("Accuracy", alabaster_mantle)
            self.assertEqual(murky_mantle["Magic Damage"], 33)
            self.assertNotIn("Magic Accuracy", murky_mantle)


if __name__ == "__main__":
    unittest.main()
