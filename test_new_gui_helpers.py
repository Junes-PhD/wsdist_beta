import unittest
import tempfile
from pathlib import Path

import gear
from create_player import create_enemy
from enemies import preset_enemies
from new_gui_main import (
    REMA_WEAPON_NAMES, _aspirational_catalog, _compose_profile_payloads,
    _is_r15_variant, _profile_category, _profile_set_descriptor, _profile_ws_name,
    _quick_cache_request, _run_overnight_cache_task, _with_weapon_overlays,
)
from simulation_cache import SimulationCache


class ProfileReportHelperTests(unittest.TestCase):
    def test_quick_cache_request_ignores_inputs_unused_by_action(self):
        common = {
            "main_job": "mnk", "sub_job": "war", "master_level": 50,
            "buffs": {}, "abilities": {}, "enemy": {"Defense": 100},
        }
        first = _quick_cache_request(
            "attack", {"main": gear.Spharai}, **common, tp=1000,
            ws_name="Victory Smite", spell_name="Fire VI",
        )
        second = _quick_cache_request(
            "attack", {"main": gear.Spharai}, **common, tp=1000,
            ws_name="Shijin Spiral", spell_name="Blizzard VI",
        )
        self.assertEqual(first, second)

    def test_profile_ws_name_matches_compact_and_short_names(self):
        self.assertEqual(_profile_ws_name("Laststand_Default"), "Last Stand")
        self.assertEqual(_profile_ws_name("Savage_Acc"), "Savage Blade")
        self.assertEqual(_profile_ws_name("Aedge_Hybrid"), "Aeolian Edge")
        self.assertEqual(_profile_ws_name("Hi_Default"), "Blade: Hi")
        self.assertEqual(_profile_ws_name("Dimi_Acc"), "Dimidiation")

    def test_hybrid_is_variant_not_combat_category(self):
        self.assertEqual(_profile_category("Tp_Hybrid"), "TP")
        self.assertIsNone(_profile_category("Ws_Hybrid"))
        self.assertIsNone(_profile_category("Idle"))
        self.assertEqual(_profile_set_descriptor("Ws_Hybrid")["role"], "ws_base")

    def test_composes_partial_tp_and_ws_layers_in_lac_order(self):
        def payload(name, slots):
            descriptor = _profile_set_descriptor(name)
            gearset = {slot: gear.Empty for slot in (
                "main", "sub", "ranged", "ammo", "head", "neck", "ear1", "ear2",
                "body", "hands", "ring1", "ring2", "back", "waist", "legs", "feet",
            )}
            gearset.update(slots)
            return {
                "name": name, "descriptor": descriptor,
                "gearset": gearset, "specified_slots": set(slots),
                "missing": [], "incomplete": [],
            }

        raw = [
            payload("Tp_Default", {"head": {"Name": "TP head"}, "body": {"Name": "TP body"}}),
            payload("Tp_Acc", {"head": {"Name": "Acc head"}}),
            payload("Ws_Default", {"neck": {"Name": "WS neck"}}),
            payload("Savage_Default", {"body": {"Name": "Savage body"}}),
            payload("Savage_Acc", {"head": {"Name": "Savage acc head"}}),
        ]
        effective = {entry["name"]: entry for entry in _compose_profile_payloads(raw)}
        self.assertEqual(effective["Tp_Acc"]["gearset"]["head"]["Name"], "Acc head")
        self.assertEqual(effective["Tp_Acc"]["gearset"]["body"]["Name"], "TP body")
        savage = effective["Savage_Acc"]
        self.assertEqual(savage["layers"], ["Ws_Default", "Savage_Default", "Savage_Acc"])
        self.assertEqual(savage["gearset"]["neck"]["Name"], "WS neck")
        self.assertEqual(savage["gearset"]["body"]["Name"], "Savage body")
        self.assertEqual(savage["gearset"]["head"]["Name"], "Savage acc head")

    def test_weapon_overlays_only_replace_explicit_weapon_slots(self):
        payload = {
            "name": "Tp_Default",
            "gearset": {
                "main": {"Name": "Armor main"}, "sub": {"Name": "Armor sub"},
                "ranged": {"Name": "Armor range"}, "ammo": {"Name": "Armor ammo"},
                "head": {"Name": "Armor head"},
            },
        }
        main_weapon = {
            "name": "Weapon_DW",
            "specified_slots": {"main", "sub"},
            "gearset": {"main": {"Name": "Sword"}, "sub": {"Name": "Dagger"}},
        }
        ranged_weapon = {
            "name": "Gun_TP",
            "specified_slots": {"ranged"},
            "gearset": {"ranged": {"Name": "Gun"}, "ammo": {"Name": "Ignored ammo"}},
        }
        combined = _with_weapon_overlays(payload, main_weapon, ranged_weapon)
        self.assertEqual(combined["gearset"]["main"]["Name"], "Sword")
        self.assertEqual(combined["gearset"]["sub"]["Name"], "Dagger")
        self.assertEqual(combined["gearset"]["ranged"]["Name"], "Gun")
        self.assertEqual(combined["gearset"]["ammo"]["Name"], "Armor ammo")
        self.assertEqual(combined["gearset"]["head"]["Name"], "Armor head")
        self.assertEqual(combined["weapon_setup"], "Tp_Default -> Weapon_DW -> Gun_TP")

    def test_aspirational_catalog_includes_all_legacy_rema_base_and_r15_models(self):
        catalog = _aspirational_catalog()
        for weapon_name in REMA_WEAPON_NAMES:
            variants = [
                record["item"] for record in catalog.values()
                if record["item"].get("Name") == weapon_name
            ]
            self.assertTrue(variants, weapon_name)
            self.assertTrue(any(not _is_r15_variant(item) for item in variants), weapon_name)
            self.assertTrue(any(_is_r15_variant(item) for item in variants), weapon_name)

    def test_overnight_task_stores_then_reuses_deterministic_result(self):
        empty = {
            "Name": "Empty", "Name2": "Empty", "Type": "None",
            "Skill Type": "None", "Jobs": gear.all_jobs,
        }
        gearset = {
            slot: empty.copy() for slot in (
                "main", "sub", "ranged", "ammo", "head", "body", "hands", "legs",
                "feet", "neck", "waist", "ear1", "ear2", "ring1", "ring2", "back",
            )
        }
        gearset["main"] = gear.Spharai
        enemy = create_enemy(preset_enemies["Apex Toad"])
        context = {
            "main_job": "mnk", "sub_job": "war", "master_level": 50,
            "buffs": {}, "abilities": {},
        }
        request = _quick_cache_request(
            "attack", gearset, **context, enemy=dict(enemy.stats), tp=1000
        )
        task = {
            "kind": "quick-look", "request": request, "context": context,
            "enemy": dict(enemy.stats), "gearset": gearset,
            "action": "attack", "tp": 1000, "ws_name": "", "ws_type": "",
            "spell_name": "", "spell_type": "",
        }
        with tempfile.TemporaryDirectory() as directory:
            cache = SimulationCache(Path(directory), source_hash="test")
            self.assertEqual(_run_overnight_cache_task(task, cache), "stored")
            key = cache.key_for("quick-look", request)
            self.assertIsNotNone(cache.get(key, "quick-look"))
            self.assertEqual(_run_overnight_cache_task(task, cache), "cached")
            summary = cache.summary()
            self.assertEqual(summary["entries"], 1)
            self.assertEqual(summary["kinds"], {"quick-look": 1})


if __name__ == "__main__":
    unittest.main()
