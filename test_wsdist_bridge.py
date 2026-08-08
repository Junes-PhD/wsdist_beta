import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lac_profile import bridge_hash, parse_set_entries, serialize_set, write_set
from wsdist_bridge import BridgeStore


class BridgeTests(unittest.TestCase):
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
