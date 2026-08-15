import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lac_profile import (
    bridge_hash, parse_set_entries, prepare_managed_update, prepare_profile_builder_update, prepare_set_renames,
    serialize_set, write_profile_source, write_set,
)
from wsdist_bridge import (
    BridgeStore, _gear_record, _with_builtin_model, _with_curated_model,
    _with_unverified_warning, _exact_rank_augment, _builtin_augment_path,
    hoxne_stat_bonus,
)
from gear import Bifrost_Ring
from weapon_bonus import get_weapon_bonus


class BridgeTests(unittest.TestCase):
    def test_bifrost_ring_converts_hp_to_mp(self):
        self.assertEqual(Bifrost_Ring["HP"], -70)
        self.assertEqual(Bifrost_Ring["MP"], 70)

        model = _with_curated_model({"item_id": 11640, "stats": [], "model_complete": False})
        self.assertEqual(model["stats"], {"HP": -70, "MP": 70})

    def test_krooti_beck_earring_augment_is_loaded_from_curated_model(self):
        model = _with_curated_model({
            "item_id": 25506,
            "name": "Beck. Earring +2",
            "stats": [],
            "model_complete": False,
        })
        self.assertEqual(model["stats"], {
            "Pet: Accuracy": 16,
            "Pet: Ranged Accuracy": 16,
            "Pet: Magic Accuracy": 16,
            "DT": -6,
            "Pet: Store TP": 6,
        })
        self.assertEqual(model["stat_source"], "curated")

    def test_eminent_bullet_model_has_ranged_damage_stats(self):
        model = _with_curated_model({
            "item_id": 21331,
            "name": "Eminent Bullet",
            "stats": [],
            "model_complete": False,
        })
        self.assertEqual(model["stats"], {"DMG": 238, "Delay": 240})

    def test_rostam_uses_explicit_divergence_path_identity(self):
        expected = {
            "A": {"Double Damage": 50, "Store TP": 25},
            "B": {"FUA": 50, "Subtle Blow II": 25},
            "C": {"Phantom Roll": 8, "Roll Duration": 60},
        }
        for path, bonuses in expected.items():
            with self.subTest(path=path):
                record = {
                    "key": f"21581|path={path}", "item_id": 21581,
                    "name": "Rostam", "slots_mask": 3, "jobs_mask": 1 << 17,
                    "accessible_count": 1, "model_complete": True,
                    "augment_path": path.lower(), "augment_rank": 25,
                    "stats": {"DMG": 132, "Accuracy": 50},
                    "lac": {"Name": "Rostam", "AugPath": path, "AugRank": 25},
                }
                item = _with_builtin_model(record, _gear_record(record))
                self.assertEqual(item["Augment Path"], path)
                self.assertEqual(item["DMG"], 132 if path == "C" else 137)
                for stat, value in bonuses.items():
                    self.assertEqual(item[stat], value)
                if path != "A":
                    self.assertNotIn("Double Damage", item)
                if path != "B":
                    self.assertNotIn("FUA", item)

    def test_manual_lac_editor_write_is_atomic_backed_up_and_conflict_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "SAM.lua"
            original = "local sets = {}\nreturn sets\n"
            updated = "local sets = { Tp = {} }\nreturn sets\n"
            profile.write_text(original, encoding="utf-8")
            backup, saved_hash = write_profile_source(
                profile, updated, expected_hash=bridge_hash(original),
            )
            self.assertEqual(profile.read_text(encoding="utf-8"), updated)
            self.assertEqual(backup.read_text(encoding="utf-8"), original)
            self.assertEqual(saved_hash, bridge_hash(updated))
            profile.write_text("-- external edit\n" + updated, encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "changed on disk"):
                write_profile_source(profile, updated, expected_hash=saved_hash)

    def test_curated_models_fill_every_verified_live_gap(self):
        expected = {
            11037: {"Stoneskin Bonus": 10},
            11590: {"Healing Magic Skill": 7},
            18912: {"DMG": 1, "Delay": 999},
            18913: {"DMG": 1, "Delay": 999},
            19041: {"Store TP": 4},
            20542: {"DMG": 50, "Hand-to-Hand Skill": 108},
            23917: {"Fast Cast": 14, "Gear Haste": 8},
            24121: {"DT": -5, "Crit Rate": 5},
            26041: {"Enhancing Magic Duration": -50},
            26215: {"Fast Cast": -10, "Cure Potency": 5},
        }
        for item_id, stats in expected.items():
            with self.subTest(item_id=item_id):
                model = _with_curated_model({"item_id": item_id, "stats": [], "model_complete": False})
                self.assertTrue(model["model_complete"])
                self.assertTrue(model["data_source"].startswith("https://"))
                for name, value in stats.items():
                    self.assertEqual(model["stats"][name], value)

    def test_unverified_new_item_remains_ineligible_with_clear_warning(self):
        record = _with_unverified_warning({"item_id": 21543, "stats": [], "model_complete": False})
        item = _gear_record({
            **record, "name": "Ryofu Uchiwa", "slots_mask": 3,
            "jobs_mask": 8388606, "accessible_count": 1,
        })
        self.assertFalse(item["Eligible"])
        self.assertIn("July 2026", item["Model Warning"])

    def test_transferability_preserves_exclusive_flags(self):
        record = {
            "item_id": 99, "name": "Transfer Test", "slots_mask": 1 << 4,
            "jobs_mask": 1 << 1, "accessible_count": 1, "model_complete": True,
            "resource_flags": 0x2000,
        }
        item = _gear_record(record)
        self.assertTrue(item["Exclusive"])
        self.assertFalse(item["Transferable"])

    def test_augmented_items_are_never_marked_transferable(self):
        common = {
            "item_id": 21581, "name": "Rostam", "slots_mask": 3,
            "jobs_mask": 1 << 17, "accessible_count": 1,
            "model_complete": True, "transferable": True,
            "stats": {"DMG": 132}, "lac": {"Name": "Rostam"},
        }
        for field, value in (
            ("augment_path", "A"), ("augment_rank", 25),
            ("augment_trial", 1), ("augments", ["DMG +5"]),
        ):
            with self.subTest(field=field):
                item = _gear_record({**common, field: value})
                self.assertTrue(item["Augmented"])
                self.assertFalse(item["Transferable"])

        base = _gear_record(common)
        self.assertFalse(base["Augmented"])
        self.assertTrue(base["Transferable"])

    def test_bridge_preserves_separate_rank_stats_for_hover(self):
        item = _gear_record({
            "item_id": 21685, "name": "Epeolatry", "slots_mask": 1,
            "jobs_mask": 1 << 20, "accessible_count": 1,
            "model_complete": True, "augment_rank": 10,
            "augment_rank_stats": {"DMG": 26, "Accuracy": 20},
            "augment_rank_stats_in_total": True,
            "stats": {"DMG": 331, "Accuracy": 20},
        })
        self.assertEqual(item["Rank"], 10)
        self.assertEqual(item["Rank Stats"], {"DMG": 26, "Accuracy": 20})
        self.assertTrue(item["Rank Stats In Total"])

        separate = _gear_record({
            "item_id": 21685, "name": "Epeolatry", "slots_mask": 1,
            "jobs_mask": 1 << 20, "accessible_count": 1,
            "model_complete": True, "augment_rank": 10,
            "augment_rank_stats": {"DMG": 26},
            "stats": {"DMG": 305},
        })
        self.assertEqual(separate["DMG"], 331)
        self.assertFalse(separate["Rank Stats In Total"])

    def test_single_path_rank_imports_default_to_path_a(self):
        epeolatry = _gear_record({
            "item_id": 21685, "name": "Epeolatry", "slots_mask": 1,
            "jobs_mask": 1 << 20, "accessible_count": 1,
            "model_complete": True, "augment_type": "Dynamis",
            "augment_rank": 10, "stats": {"DMG": 305},
        })
        self.assertEqual(epeolatry["Augment Path"], "A")

        crocea = _gear_record({
            "item_id": 21686, "name": "Crocea Mors", "slots_mask": 1,
            "jobs_mask": 1 << 11, "accessible_count": 1,
            "model_complete": True, "augment_type": "Dynamis",
            "augment_rank": 17, "stats": {"DMG": 180},
        })
        self.assertNotIn("Augment Path", crocea)

        oboro = _gear_record({
            "item_id": 30001, "name": "Minos", "slots_mask": 1,
            "jobs_mask": 1 << 1, "accessible_count": 1,
            "model_complete": True, "augment_type": "Oboro",
            "augment_rank": 15, "stats": {"DMG": 274},
        })
        self.assertEqual(oboro["Augment Path"], "A")

        unity = _gear_record({
            "item_id": 30002, "name": "Acuity Belt +1", "slots_mask": 1 << 10,
            "jobs_mask": 1 << 5, "accessible_count": 1,
            "model_complete": True, "augment_type": "Unity accessory",
            "augment_rank": 15, "stats": {"Magic Accuracy": 15},
        })
        self.assertEqual(unity["Augment Path"], "A")

    def test_epeolatry_r10_uses_supplied_rank_package(self):
        record = {
            "item_id": 21685, "name": "Epeolatry", "slots_mask": 1,
            "jobs_mask": 1 << 20, "accessible_count": 1,
            "model_complete": True, "augment_type": "Dynamis",
            "augment_rank": 10,
            "stats": {"DMG": 305, "Delay": 489, "Magic Damage": 186,
                      "Great Sword Skill": 269, "Magic Accuracy Skill": 242,
                      "PDT": -25},
            "lac": {"Name": "Epeolatry", "AugRank": 10},
        }
        item = _with_builtin_model(record, _gear_record(record))
        self.assertEqual(item["DMG"], 329)
        self.assertEqual(item["Accuracy"], 19)
        self.assertEqual(item["Magic Accuracy"], 19)
        self.assertAlmostEqual(
            get_weapon_bonus(item["Name2"], "Empty", "Dimidiation"), 0.10,
        )

    def test_rank_path_d_resolves_the_correct_fixed_path_model(self):
        record = {
            "item_id": 999, "name": "Ryuo Tekko +1", "slots_mask": 1 << 6,
            "jobs_mask": 1 << 12, "accessible_count": 1, "model_complete": True,
            "augment_path": "D", "augment_rank": 15, "stats": {},
        }
        item = _with_builtin_model(record, _gear_record(record))
        self.assertEqual(item["Augment Path"], "D")
        self.assertEqual(item["DA"], 4)
        self.assertEqual(item["Accuracy"], 58)

        # Some older catalog rows keep the path glued to an upgraded name.
        compact = {
            "Name": "Carmine Cuisses +1", "Name2": "Carmine Cuisses +1D",
            "Accuracy": 55, "Attack": 47, "Dual Wield": 6,
        }
        self.assertEqual(_builtin_augment_path(compact), "D")

    def test_malware_and_kroot_exact_rank_observations_apply_once(self):
        coiste_record = _exact_rank_augment({
            "item_id": 0, "name": "Coiste Bodhar", "slots_mask": 1 << 3,
            "jobs_mask": 1 << 12, "accessible_count": 1, "model_complete": True,
            "augment_path": "A", "augment_rank": 3,
            "stats": {"DA": 3, "Store TP": 3},
        })
        coiste = _gear_record(coiste_record)
        self.assertEqual(coiste["Attack"], 3)
        self.assertEqual(coiste["Rank Stats"], {"Attack": 3})

        crocea_record = _exact_rank_augment({
            "item_id": 21686, "name": "Crocea Mors", "slots_mask": 1,
            "jobs_mask": 1 << 5, "accessible_count": 1, "model_complete": True,
            "augment_path": "C", "augment_rank": 17,
            "stats": {"DMG": 180},
        })
        crocea = _gear_record(crocea_record)
        self.assertEqual(crocea["DMG"], 185)
        self.assertEqual(crocea["Elemental WS Damage%"], 60)
        self.assertEqual(crocea["EnSpell Damage%"], 340)
        self.assertAlmostEqual(get_weapon_bonus("Kikoku [path=A; rank=2]", "", "Blade: Metsu"), 0.02)

        # The bridge uses abbreviated client labels for some JSE equipment.
        nodowa = _gear_record(_exact_rank_augment({
            "item_id": 0, "name": "Sam. Nodowa +2", "slots_mask": 1 << 9,
            "jobs_mask": 1 << 12, "accessible_count": 1, "model_complete": True,
            "augment_path": "A", "augment_rank": 6, "stats": {},
        }))
        self.assertEqual(nodowa["Rank Stats"], {"STR": 6, "Store TP": 2, "PDL": 2})

        ryuo = _gear_record(_exact_rank_augment({
            "item_id": 0, "name": "Ryuo Sune-Ate +1", "slots_mask": 1 << 8,
            "jobs_mask": 1 << 12, "accessible_count": 1, "model_complete": True,
            "augment_path": "C", "augment_rank": 15, "stats": {},
        }))
        self.assertEqual(ryuo["Rank Stats"], {"HP": 65, "Store TP": 5, "Subtle Blow": 8})

    def test_bridge_preserves_resource_item_level_for_candidate_filters(self):
        item = _gear_record({
            "item_id": 100, "name": "Item Level Helm", "slots_mask": 1 << 4,
            "jobs_mask": 1 << 1, "accessible_count": 1, "model_complete": True,
            "item_level": 118, "stats": {"Defense": 100},
        })
        self.assertEqual(item["Item Level"], 118)

    def test_halasz_magic_crit_rate_is_not_physical_crit_rate(self):
        item = _gear_record({
            "item_id": 27535, "name": "Halasz Earring", "slots_mask": 1 << 11,
            "jobs_mask": 1 << 1, "accessible_count": 1, "model_complete": True,
            "stats": {"MP": 45, "Crit Rate": 14},
        })
        self.assertEqual(item["Magic Crit Rate II"], 14)
        self.assertNotIn("Crit Rate", item)

    def test_steelflash_bladeborn_stats_keep_set_da_conditional(self):
        common = {
            "slots_mask": (1 << 11) | (1 << 12),
            "jobs_mask": (1 << 23) - 2,
            "accessible_count": 1,
            "model_complete": True,
        }
        steelflash = _gear_record({
            **common, "item_id": 28520, "name": "Steelflash Earring",
            "stats": {"Accuracy": 8, "Store TP": 1, "DA": 7},
        })
        bladeborn = _gear_record({
            **common, "item_id": 28521, "name": "Bladeborn Earring",
            "stats": {"Attack": 8},
        })

        self.assertNotIn("DA", steelflash)
        self.assertNotIn("DA", bladeborn)
        self.assertEqual(steelflash["Store TP"], 1)
        self.assertEqual(bladeborn["Store TP"], 1)
        self.assertIn("Double Attack +7%", steelflash["Conditional Effects"][0])

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
        self.assertIn("CreateCycle('HybridAccuracy'", first)
        self.assertIn("[2] = 'Acc', [3] = 'HighAcc'", first)
        self.assertIn("applyWSDistHybridTpSet();", first)
        self.assertIn("setName = setName .. '_' .. accuracy", first)
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
