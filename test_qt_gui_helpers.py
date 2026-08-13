import unittest
import tempfile
from pathlib import Path

import gear
from create_player import create_enemy
from enemies import preset_enemies
from qt_gui_main import (
    AUTO_WEAPON_TYPE, REMA_WEAPON_NAMES, WS_BY_SKILL, _aspirational_catalog, _compose_profile_payloads,
    _is_r15_variant, _profile_category, _profile_set_descriptor, _profile_ws_name,
    _quick_cache_request, _run_overnight_cache_task, _with_weapon_overlays,
    magic_damage_spell_choices, weapon_skill_choices,
)
from simulation_cache import SimulationCache
from profile_builder import (
    ProfileRecipe, build_stat_set, child_seed, optimizer_scenario, weapon_category, weapon_overlays,
)
from wsdist import obvious_blacklist_suggestions, universal_blacklist_suggestions


class ProfileReportHelperTests(unittest.TestCase):
    def test_magic_damage_choices_are_scoped_to_formula(self):
        self.assertIn("Fire VI", magic_damage_spell_choices("Elemental Magic"))
        self.assertNotIn("Fire VI", magic_damage_spell_choices("Quick Draw"))
        self.assertIn("Earth Shot", magic_damage_spell_choices("Quick Draw"))
        self.assertIn("Katon: San", magic_damage_spell_choices("Ninjutsu"))
        self.assertEqual(magic_damage_spell_choices("Ranged Attack"), ["None", "Ranged Attack"])

    def test_weapon_skill_choices_follow_explicit_type(self):
        quick_set = {
            "main": {"Skill Type": "Sword"},
            "ranged": {"Skill Type": "None"},
        }
        self.assertEqual(weapon_skill_choices("Marksmanship", quick_set), [
            "None", *WS_BY_SKILL["Marksmanship"]
        ])
        self.assertIn("Last Stand", weapon_skill_choices("Marksmanship", quick_set))
        self.assertNotIn("Savage Blade", weapon_skill_choices("Marksmanship", quick_set))
        self.assertIn("Savage Blade", weapon_skill_choices(AUTO_WEAPON_TYPE, quick_set))

    def test_profile_builder_detects_weapon_overlays_and_categories(self):
        empty = {"Name": "Empty", "Type": "None", "Skill Type": "None"}
        overlays = [
            {"name": "Weapon_SwordShield", "descriptor": {"role": "weapon"},
             "specified_slots": {"main", "sub"}, "gearset": {"main": {"Name": "Sword", "Skill Type": "Sword"}, "sub": {"Name": "Shield", "Type": "Shield"}}},
            {"name": "Weapon_DualWield", "descriptor": {"role": "weapon"},
             "specified_slots": {"main", "sub"}, "gearset": {"main": {"Name": "Axe", "Skill Type": "Axe"}, "sub": {"Name": "Axe", "Type": "Weapon"}}},
            {"name": "Weapon_GreatAxe", "descriptor": {"role": "weapon"},
             "specified_slots": {"main", "sub"}, "gearset": {"main": {"Name": "Great Axe", "Skill Type": "Great Axe"}, "sub": empty}},
        ]
        detected = weapon_overlays(overlays)
        self.assertEqual([item["name"] for item in detected], ["Weapon_DualWield", "Weapon_GreatAxe", "Weapon_SwordShield"])
        self.assertEqual([weapon_category(item) for item in detected], ["DualWield", "TwoHanded", "SingleWield"])

    def test_profile_builder_stat_recipe_respects_sir_cap_and_duplicate_rings(self):
        empty = {"Name": "Empty", "Name2": "Empty", "Type": "None", "Skill Type": "None"}
        slots = ("head", "neck", "ear1", "ear2", "body", "hands", "ring1", "ring2", "back", "waist", "legs", "feet")
        candidates = {slot: [empty.copy()] for slot in slots}
        candidates["head"].append({"Name": "SIR Helm", "Name2": "SIR Helm", "Spell interruption rate down": -50})
        candidates["body"].append({"Name": "SIR Body", "Name2": "SIR Body", "Spell interruption rate down": -42})
        ring = {"Name": "Rare Ring", "Name2": "Rare Ring", "Spell interruption rate down": -20, "Accessible Count": 1}
        candidates["ring1"].append(ring)
        candidates["ring2"].append(ring)
        result = build_stat_set("SIR", candidates, ProfileRecipe("SIR", ("Spell interruption rate down",), (("Spell interruption rate down", 92),)))
        self.assertEqual(result.equipment["head"]["Name"], "SIR Helm")
        self.assertEqual(result.equipment["body"]["Name"], "SIR Body")
        self.assertNotEqual(result.equipment["ring1"].get("Name"), result.equipment["ring2"].get("Name"))
        self.assertEqual(child_seed(123, "WAR", "Tp_Default"), child_seed(123, "WAR", "Tp_Default"))

    def test_profile_builder_optimizer_scenarios_follow_set_variant(self):
        self.assertEqual(
            optimizer_scenario("Tp_Default", 1000),
            {"enemy": "Apex Toad", "pdt": 0, "mdt": 0, "dt": 0, "tp": 1000},
        )
        self.assertEqual(
            optimizer_scenario("Savage_Hybrid_DualWield", 2000),
            {"enemy": "Apex Knight Lugcrawler", "pdt": 50, "mdt": 25, "dt": 0, "tp": 2000},
        )
        self.assertEqual(
            optimizer_scenario("Ukko_HighAcc", 3000)["enemy"],
            "Apex Archaic Cogs",
        )

    def test_obvious_blacklist_requires_every_variant_to_be_dominated(self):
        weak = {"Name": "Weak Helm", "Name2": "Weak Helm", "Type": "Armor", "Skill Type": "None", "Attack": 5, "Model Complete": True}
        strong = {"Name": "Strong Helm", "Name2": "Strong Helm", "Type": "Armor", "Skill Type": "None", "Attack": 8, "Model Complete": True}
        suggestions = obvious_blacklist_suggestions({"head": [weak, strong]})
        self.assertEqual(suggestions, {"weak helm": {"strong helm"}})

        upgraded_variant = {"Name": "Weak Helm", "Name2": "Weak Helm [Attack+10]", "Type": "Armor", "Skill Type": "None", "Attack": 10, "Model Complete": True}
        suggestions = obvious_blacklist_suggestions({"head": [weak, upgraded_variant, strong]})
        self.assertNotIn("weak helm", suggestions)

    def test_obvious_blacklist_skips_special_and_incomplete_items(self):
        weak = {"Name": "Weak Helm", "Name2": "Weak Helm", "Type": "Armor", "Skill Type": "None", "Attack": 5, "Model Complete": True}
        strong = {"Name": "Strong Helm", "Name2": "Strong Helm", "Type": "Armor", "Skill Type": "None", "Attack": 8, "Model Complete": True}
        conditional = {**weak, "Name": "Conditional Helm", "Name2": "Conditional Helm", "Conditional Effects": ["Campaign: Attack +20"]}
        incomplete = {**weak, "Name": "Incomplete Helm", "Name2": "Incomplete Helm", "Model Complete": False}
        self.assertNotIn("conditional helm", obvious_blacklist_suggestions({"head": [conditional, strong]}))
        self.assertNotIn("incomplete helm", obvious_blacklist_suggestions({"head": [incomplete, strong]}))

    def test_blacklist_dominance_ignores_bridge_metadata(self):
        cosmetic = {
            "Name": "Cosmetic Weapon", "Name2": "Cosmetic Weapon", "Type": "Weapon",
            "Skill Type": "Scythe", "Model Complete": True, "Resource Flags": 1,
        }
        normal = {
            "Name": "Normal Weapon", "Name2": "Normal Weapon", "Type": "Weapon",
            "Skill Type": "Scythe", "Model Complete": True, "Resource Flags": 2,
        }
        self.assertEqual(obvious_blacklist_suggestions({"main": [cosmetic, normal]}), {})

    def test_blacklist_does_not_auto_hide_zero_stat_gear(self):
        cosmetic = {
            "Name": "Costume Scythe", "Name2": "Costume Scythe", "Type": "Weapon",
            "Skill Type": "Scythe", "Model Complete": True,
        }
        stronger = {
            "Name": "Modeled Scythe", "Name2": "Modeled Scythe", "Type": "Weapon",
            "Skill Type": "Scythe", "Model Complete": True, "DMG": 100, "Delay": 500,
        }
        self.assertNotIn("costume scythe", obvious_blacklist_suggestions({"main": [cosmetic, stronger]}))

    def test_universal_blacklist_does_not_borrow_another_characters_upgrade(self):
        weak = {"Name": "Shared Helm", "Name2": "Shared Helm", "Type": "Armor", "Skill Type": "None", "Attack": 5}
        strong = {"Name": "Superior Helm +2", "Name2": "Superior Helm +2", "Type": "Armor", "Skill Type": "None", "Attack": 8}
        suggestions = universal_blacklist_suggestions({
            "character with +2": {"head": [weak, strong]},
            "character without +2": {"head": [weak]},
        })
        self.assertNotIn("shared helm", suggestions)
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

    def test_quick_tp_action_alias_uses_the_attack_request(self):
        common = {
            "main_job": "mnk", "sub_job": "war", "master_level": 50,
            "buffs": {}, "abilities": {}, "enemy": {"Defense": 100},
        }
        alias = _quick_cache_request("tp", {"main": gear.Spharai}, **common, tp=1000)
        canonical = _quick_cache_request("attack", {"main": gear.Spharai}, **common, tp=1000)
        self.assertEqual(alias, canonical)

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
