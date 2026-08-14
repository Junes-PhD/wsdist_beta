import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

import gear
from create_player import create_enemy
from enemies import preset_enemies
from qt_gui_main import (
    AUTO_WEAPON_TYPE, ITEM_LEVEL_FILTER_SLOTS, LuaSyntaxHighlighter, MainWindow, OPTIMIZER_STATE_LABELS,
    SLOTS,
    SEARCH_QUALITY,
    ResponsiveBuffGrid, ResponsiveStatSection,
    REMA_WEAPON_NAMES, WS_BY_SKILL, _aspirational_catalog,
    _is_r15_variant, _item_level_candidate_allowed, _profile_category,
    _profile_set_descriptor, _profile_ws_name,
    _dps_series_chart_data, _quick_cache_request, _quick_result_chart_data,
    _optimizer_current_result_lines, _optimizer_result_players, _remaining_time_estimate,
    _normalized_search_quality, _reference_enemy_names, _search_quality_settings,
    _run_overnight_cache_task, _ws_distribution_chart_data,
    magic_damage_spell_choices, weapon_skill_choices,
)
from simulation_cache import SimulationCache
from profile_builder import (
    GearSources, ProfileRecipe, bridge_candidates, build_profile_catalog,
    build_stat_set, child_seed, group_similar_ws_sets, optimizer_scenario,
    profile_catalog_plan, weapon_category, weapon_overlays,
)
from wsdist import obvious_blacklist_suggestions, universal_blacklist_suggestions


class ProfileReportHelperTests(unittest.TestCase):
    def test_reference_enemy_graph_labels_keep_active_enemy_first(self):
        self.assertEqual(
            _reference_enemy_names("Custom target"),
            ("Custom target", "Apex Toad", "Apex Knight Lugcrawler", "Apex Archaic Cogs"),
        )
        self.assertEqual(
            _reference_enemy_names("Apex Toad"),
            ("Apex Toad", "Apex Knight Lugcrawler", "Apex Archaic Cogs"),
        )
        self.assertEqual(_reference_enemy_names("Custom target", False), ("Custom target",))

    def test_search_quality_policy_and_legacy_deep_migration(self):
        self.assertEqual(_search_quality_settings("Fast"), (4, 6, True))
        self.assertEqual(_search_quality_settings("Standard"), (10, 10, True))
        self.assertEqual(_search_quality_settings("Deep"), (10, 12, False))
        self.assertEqual(tuple(SEARCH_QUALITY), ("Fast", "Standard", "Deep"))
        self.assertEqual(_normalized_search_quality("Deep", legacy_deep=True), "Standard")
        self.assertEqual(_normalized_search_quality("Deep", legacy_deep=False), "Deep")

    def test_shared_optimizer_start_requires_exact_current_candidates_and_augments(self):
        starting = {slot: dict(gear.Empty) for slot in SLOTS}
        starting["head"] = {
            **dict(gear.Empty), "Name": "Test Head", "Name2": "Test Head [Path A]",
            "Type": "Armor", "Path": "A", "Jobs": ["sam"],
        }
        check_gear = {slot: [item] for slot, item in starting.items()}
        record = {
            "kind": "optimizer", "stale": False, "corrupt": False,
            "payload": {"gearsets": {"single": {slot: dict(item) for slot, item in starting.items()}}},
        }

        class History:
            def list(self, *_args, **_kwargs):
                return [record]

        class Host:
            result_history = History()
            _item_cache_identity = staticmethod(MainWindow._item_cache_identity)

        host = Host()
        valid = MainWindow._validated_shared_optimizer_start(host, check_gear, starting)
        self.assertEqual(valid["head"]["Path"], "A")

        record["payload"]["gearsets"]["single"]["head"]["Path"] = "B"
        self.assertIsNone(
            MainWindow._validated_shared_optimizer_start(host, check_gear, starting)
        )

    def test_independent_cache_identity_ignores_worker_count_but_split_does_not(self):
        left = MainWindow._optimizer_cache_request(
            None, "optimizer", ("sam",), {"workers": 2, "parallel_mode": "search_runs"}
        )
        right = MainWindow._optimizer_cache_request(
            None, "optimizer", ("sam",), {"workers": 12, "parallel_mode": "search_runs"}
        )
        self.assertEqual(left, right)
        split_left = MainWindow._optimizer_cache_request(
            None, "optimizer", ("sam",), {"workers": 2, "parallel_mode": "single_run"}
        )
        split_right = MainWindow._optimizer_cache_request(
            None, "optimizer", ("sam",), {"workers": 12, "parallel_mode": "single_run"}
        )
        self.assertNotEqual(split_left, split_right)

    def test_combined_result_pair_is_read_from_wrapper_or_explicit_fields(self):
        tp_player = object()
        ws_player = object()
        wrapper = SimpleNamespace(tp_player=tp_player, ws_player=ws_player)
        self.assertEqual(
            _optimizer_result_players({"player": wrapper}),
            (tp_player, ws_player),
        )
        explicit_tp = object()
        self.assertEqual(
            _optimizer_result_players({"player": wrapper, "tp_player": explicit_tp}),
            (explicit_tp, ws_player),
        )

    def test_optimizer_eta_blends_recent_progress_and_handles_startup(self):
        self.assertIsNone(_remaining_time_estimate([], elapsed=2, progress=0))
        estimate = _remaining_time_estimate(
            [(0, 0.0), (30, 0.20), (60, 0.50)], elapsed=60, progress=0.50,
        )
        self.assertGreater(estimate, 45)
        self.assertLess(estimate, 90)
        self.assertEqual(
            _remaining_time_estimate([(0, 0), (60, 1)], elapsed=60, progress=1),
            0.0,
        )

    def test_optimizer_current_result_metrics_are_visually_separated(self):
        formatted = _optimizer_current_result_lines(
            "Current results: DPS 500.0; WS damage 18,000; TP time 6.2s"
        )
        self.assertEqual(formatted.splitlines(), [
            "• DPS 500.0", "• WS damage 18,000", "• TP time 6.2s",
        ])

    def test_lua_highlighter_does_not_treat_dashes_inside_strings_as_comments(self):
        self.assertEqual(LuaSyntaxHighlighter._comment_start("value = '--not comment'"), ("", -1))
        self.assertEqual(LuaSyntaxHighlighter._comment_start("value = 1 -- comment"), ("line", 10))
        self.assertEqual(LuaSyntaxHighlighter._comment_start("--[[ comment"), ("long", 0))

    def test_live_totals_uses_stable_resize_breakpoints(self):
        self.assertEqual(ResponsiveStatSection.columns_for_width(700, 12), 1)
        self.assertEqual(ResponsiveStatSection.columns_for_width(1000, 12), 2)
        self.assertEqual(ResponsiveStatSection.columns_for_width(1600, 12), 4)
        self.assertEqual(ResponsiveStatSection.columns_for_width(1600, 3), 3)

    def test_active_buff_cards_stack_before_fields_are_squeezed(self):
        self.assertEqual(ResponsiveBuffGrid.columns_for_width(700), 1)
        self.assertEqual(ResponsiveBuffGrid.columns_for_width(840), 2)
        self.assertEqual(ResponsiveBuffGrid.columns_for_width(1400), 2)

    def test_quick_attack_chart_uses_tp_pace_without_inventing_distribution(self):
        chart = _quick_result_chart_data(
            "attack", (6.494, [500.8, 207.5, 2.28, -1], 40.0), tp_target=1000,
        )
        self.assertEqual(chart["kind"], "tp_pace")
        self.assertEqual(chart["target_tp"], 1000.0)
        self.assertEqual(chart["time_to_ws"], 6.494)
        self.assertEqual(chart["physical_damage"], 460.8)
        self.assertEqual(chart["magical_damage"], 40.0)

    def test_quick_ws_and_spell_chart_keep_unlike_units_separate(self):
        ws_chart = _quick_result_chart_data("ws", (12345, [12345, 135.5, 1]))
        spell_chart = _quick_result_chart_data("spell", (4321, [4321, 0, 1]))
        self.assertEqual(ws_chart, {
            "kind": "action_result", "action": "ws",
            "damage": 12345.0, "tp_return": 135.5,
        })
        self.assertEqual(spell_chart["action"], "spell")
        self.assertEqual(spell_chart["damage"], 4321.0)
        self.assertIsNone(_quick_result_chart_data("ws", None))

    def test_ws_distribution_chart_requires_a_real_histogram(self):
        chart = _ws_distribution_chart_data({
            "samples": 20000, "mean": 15000, "median": 14900,
            "p05": 11000, "p95": 19000,
            "histogram": {"edges": [10000, 15000, 20000], "counts": [9000, 11000]},
        })
        self.assertEqual(chart["samples"], 20000)
        self.assertEqual(chart["counts"].tolist(), [9000.0, 11000.0])
        self.assertIsNone(_ws_distribution_chart_data({
            "histogram": {"edges": [0, 1], "counts": [1, 2]},
        }))

    def test_two_hour_dps_chart_keeps_total_tp_and_ws_series(self):
        chart = _dps_series_chart_data({"dps_series": {
            "time": [1, 7200], "total": [500, 700],
            "tp_time": [1, 7200], "tp": [200, 250],
            "ws_time": [2, 7200], "ws": [300, 450],
        }})
        self.assertEqual(set(chart), {"total", "tp", "ws"})
        self.assertEqual(chart["total"][0].tolist(), [1.0, 7200.0])
        self.assertIsNone(_dps_series_chart_data({"dps_series": {}}))

    def test_optimizer_running_state_is_explicit(self):
        self.assertEqual(OPTIMIZER_STATE_LABELS["running"], "SIMULATION RUNNING")
        self.assertEqual(OPTIMIZER_STATE_LABELS["stopping"], "STOPPING")

    def test_item_level_filter_targets_only_five_armor_slots(self):
        self.assertEqual(
            ITEM_LEVEL_FILTER_SLOTS,
            ("head", "body", "hands", "legs", "feet"),
        )
        self.assertEqual(MainWindow._item_level({"Item Level": 118}), 118)
        self.assertEqual(MainWindow._item_level({"item_level": "119"}), 119)
        self.assertFalse(_item_level_candidate_allowed("head", 118, True))
        self.assertTrue(_item_level_candidate_allowed("head", 119, True))
        self.assertTrue(_item_level_candidate_allowed("main", 1, True))
        self.assertTrue(_item_level_candidate_allowed("head", 1, False))

    def test_similar_ws_sets_group_around_largest_representative(self):
        empty = {"Name": "Empty", "Name2": "Empty"}
        shared = {slot: empty.copy() for slot in (
            "ammo", "head", "neck", "ear1", "ear2", "body", "hands",
            "ring1", "ring2", "back", "waist", "legs", "feet",
        )}
        fudo = dict(shared)
        fudo["head"] = {"Name": "Nyame Helm", "Name2": "Nyame Helm"}
        kasha = dict(fudo)
        kasha["hands"] = {"Name": "Nyame Gauntlets", "Name2": "Nyame Gauntlets"}
        jinpu = dict(shared)
        jinpu.update({
            "head": {"Name": "Magic Helm", "Name2": "Magic Helm"},
            "body": {"Name": "Magic Body", "Name2": "Magic Body"},
            "hands": {"Name": "Magic Hands", "Name2": "Magic Hands"},
        })
        groups = group_similar_ws_sets({
            "Tachi: Fudo": fudo, "Tachi: Kasha": kasha, "Tachi: Jinpu": jinpu,
        }, max_slot_differences=1)
        self.assertEqual(groups[0]["members"], ["Tachi: Fudo", "Tachi: Kasha"])
        self.assertEqual(groups[1]["members"], ["Tachi: Jinpu"])

    def test_bridge_candidates_accepts_porter_slip_schema_source(self):
        class Store:
            hoxne_mastery_rank = 5
            data = {"items": [{
                "key": "porter|helm", "item_id": 1, "name": "Porter Helm",
                "slots_mask": 1 << 4, "jobs_mask": 1 << 1,
                "accessible_count": 0, "total_count": 1,
                "stats": {"STR": 10}, "model_complete": True,
                "locations": [{"source": "porter_slip", "container": "Porter Slip"}],
            }]}
        candidates = bridge_candidates(
            Store(), "war", GearSources(accessible=False, porter=True, transferable=False),
        )
        self.assertIn("Porter Helm", [item.get("Name") for item in candidates["head"]])

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
        empty_set = {
            "main": {"Skill Type": "None"},
            "ranged": {"Skill Type": "None"},
        }
        fallback = weapon_skill_choices(AUTO_WEAPON_TYPE, empty_set)
        self.assertIn("Savage Blade", fallback)
        self.assertIn("Stardiver", fallback)

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
        self.assertEqual([item["name"] for item in detected], ["Weapon_SwordShield", "Weapon_DualWield", "Weapon_GreatAxe"])
        self.assertEqual([weapon_category(item) for item in detected], ["SingleWield", "DualWield", "TwoHanded"])

    def test_profile_builder_matches_weapon_skill_to_weapon_overlay(self):
        empty = {"Name": "Empty", "Type": "None", "Skill Type": "None"}
        overlays = [
            {"name": "Weapon_Masamune", "gearset": {
                "main": {"Name": "Masamune", "Skill Type": "Great Katana"},
                "ranged": empty,
            }},
            {"name": "Weapon_Polearm", "gearset": {
                "main": {"Name": "Kaja Lance", "Skill Type": "Polearm"},
                "ranged": empty,
            }},
        ]
        selected = MainWindow._profile_builder_overlay_for_set(
            "Stardiver_Default", overlays, "Stardiver"
        )
        self.assertEqual(selected["name"], "Weapon_Polearm")

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

    def test_profile_builder_never_duplicates_rare_paired_items(self):
        empty = {"Name": "Empty", "Name2": "Empty", "Type": "None", "Skill Type": "None"}
        slots = ("head", "neck", "ear1", "ear2", "body", "hands", "ring1", "ring2", "back", "waist", "legs", "feet", "ammo")
        candidates = {slot: [empty.copy()] for slot in slots}
        earring = {
            "Name": "Rare Earring", "Name2": "Rare Earring", "Fast Cast": 10,
            "Rare": True, "Accessible Count": 2,
        }
        candidates["ear1"].append(earring)
        candidates["ear2"].append(earring)
        result = build_stat_set(
            "Precast", candidates,
            ProfileRecipe("Precast", ("Fast Cast",)),
        )
        equipped = [result.equipment[slot].get("Name") for slot in ("ear1", "ear2")]
        self.assertEqual(equipped.count("Rare Earring"), 1)

    def test_profile_builder_discovers_steelflash_bladeborn_pair_bonus(self):
        empty = {"Name": "Empty", "Name2": "Empty", "Type": "None", "Skill Type": "None"}
        slots = ("head", "neck", "ear1", "ear2", "body", "hands", "ring1", "ring2", "back", "waist", "legs", "feet", "ammo")
        candidates = {slot: [empty.copy()] for slot in slots}
        candidates["ear1"].extend([
            {"Name": "Solo DA Ear 1", "Name2": "Solo DA Ear 1", "DA": 3},
            {"Name": "Steelflash Earring", "Name2": "Steelflash Earring", "Accuracy": 8, "Store TP": 1},
        ])
        candidates["ear2"].extend([
            {"Name": "Solo DA Ear 2", "Name2": "Solo DA Ear 2", "DA": 3},
            {"Name": "Bladeborn Earring", "Name2": "Bladeborn Earring", "Attack": 8, "Store TP": 1},
        ])

        result = build_stat_set(
            "Tp_Default", candidates,
            ProfileRecipe("Tp_Default", ("DA",)),
        )

        self.assertEqual(
            {result.equipment[slot]["Name"] for slot in ("ear1", "ear2")},
            {"Steelflash Earring", "Bladeborn Earring"},
        )

    def test_profile_builder_keeps_stat_ammo_in_generated_sets(self):
        empty = {"Name": "Empty", "Name2": "Empty", "Type": "None", "Skill Type": "None"}
        slots = ("head", "neck", "ear1", "ear2", "body", "hands", "ring1", "ring2", "back", "waist", "legs", "feet")
        candidates = {slot: [empty.copy()] for slot in slots}
        candidates["ammo"] = [empty.copy(), {
            "Name": "Coiste Bodhar", "Name2": "Coiste Bodhar", "Store TP": 3,
        }]
        result = build_stat_set(
            "Tp_Default", candidates,
            ProfileRecipe("Tp_Default", ("Store TP",)),
        )
        self.assertEqual(result.equipment["ammo"]["Name"], "Coiste Bodhar")

    def test_profile_builder_blends_priorities_instead_of_first_stat_wins_all(self):
        empty = {"Name": "Empty", "Name2": "Empty", "Type": "None", "Skill Type": "None"}
        slots = ("head", "neck", "ear1", "ear2", "body", "hands", "ring1", "ring2", "back", "waist", "legs", "feet", "ammo")
        candidates = {slot: [empty.copy()] for slot in slots}
        candidates["head"].extend([
            {"Name": "Tiny DA", "Name2": "Tiny DA", "DA": 1},
            {"Name": "Balanced TP", "Name2": "Balanced TP", "Store TP": 10, "Attack": 40},
        ])
        result = build_stat_set(
            "Tp_Default", candidates,
            ProfileRecipe("Tp_Default", ("DA", "Store TP", "Attack")),
        )
        self.assertEqual(result.equipment["head"]["Name"], "Balanced TP")

    def test_profile_builder_preserves_starting_items_and_only_warns_for_required_rules(self):
        empty = {"Name": "Empty", "Name2": "Empty", "Type": "None", "Skill Type": "None"}
        slots = ("head", "neck", "ear1", "ear2", "body", "hands", "ring1", "ring2", "back", "waist", "legs", "feet", "ammo")
        candidates = {slot: [empty.copy()] for slot in slots}
        candidates["hands"].append({"Name": "Fast Hands", "Name2": "Fast Hands", "Fast Cast": 8})
        starting = {"body": {"Name": "Existing Body", "Name2": "Existing Body", "HP": 10}}
        result = build_stat_set(
            "Precast", candidates,
            ProfileRecipe("Precast", ("Fast Cast", "DT", "HP"), (("Fast Cast", 80),)),
            starting=starting,
        )
        self.assertEqual(result.equipment["body"]["Name"], "Existing Body")
        self.assertEqual(result.equipment["hands"]["Name"], "Fast Hands")
        self.assertTrue(any("Fast Cast" in warning for warning in result.warnings))
        self.assertFalse(any("PDT" in warning or "MDT" in warning for warning in result.warnings))

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
        self.assertEqual(
            optimizer_scenario("Tp_Hybrid", 1000),
            {"enemy": "Apex Toad", "pdt": 50, "mdt": 25, "dt": 0, "tp": 1000},
        )
        self.assertEqual(
            optimizer_scenario("Tp_Hybrid_Acc_TwoHanded", 1000)["enemy"],
            "Apex Knight Lugcrawler",
        )
        self.assertEqual(
            optimizer_scenario("Tp_Hybrid_HighAcc", 1000)["enemy"],
            "Apex Archaic Cogs",
        )

    def test_profile_builder_defensive_shortlist_keeps_dt_specialists(self):
        empty = {"Name": "Empty", "Name2": "Empty", "Type": "None", "Skill Type": "None"}
        slots = ("head", "neck", "ear1", "ear2", "body", "hands", "ring1", "ring2", "back", "waist", "legs", "feet")
        candidates = {slot: [empty.copy()] for slot in slots}
        candidates["head"].extend(
            {"Name": f"HP Helm {index}", "Name2": f"HP Helm {index}", "HP": 1000 - index}
            for index in range(10)
        )
        candidates["head"].append({"Name": "DT Helm", "Name2": "DT Helm", "DT": -50})
        result = build_stat_set(
            "Dt",
            candidates,
            ProfileRecipe("Dt", ("HP", "Magic Evasion"), require_damage_cap=True),
        )
        self.assertEqual(result.equipment["head"]["Name"], "DT Helm")
        self.assertEqual(result.warnings, [])

    def test_profile_catalog_plan_uses_only_mapped_ws_families(self):
        payloads = [
            {"name": "Precast", "descriptor": {"role": "other"}},
            {"name": "Savage_Default", "descriptor": {
                "role": "ws", "family": "Savage", "ws_name": "Savage Blade",
            }},
            {"name": "UnknownWS_Default", "descriptor": {
                "role": "ws", "family": "UnknownWS", "ws_name": "",
            }},
        ]
        plan = profile_catalog_plan("war", payloads, 2000)
        names = [recipe.name for recipe, _metadata in plan]
        self.assertIn("Precast", names)
        self.assertIn("Tp_Hybrid", names)
        self.assertIn("Tp_Hybrid_Acc", names)
        self.assertIn("Tp_Hybrid_HighAcc", names)
        self.assertIn("Savage_HighAcc", names)
        self.assertNotIn("UnknownWS_Default", names)
        savage = next(metadata for recipe, metadata in plan if recipe.name == "Savage_Hybrid")
        self.assertEqual(savage["section_type"], "Weapon skill")
        self.assertEqual(savage["optimizer"]["tp"], 2000)

    def test_profile_catalog_returns_review_metadata(self):
        empty = {"Name": "Empty", "Name2": "Empty", "Type": "None", "Skill Type": "None"}
        slots = ("head", "neck", "ear1", "ear2", "body", "hands", "ring1", "ring2", "back", "waist", "legs", "feet")
        candidates = {slot: [empty.copy()] for slot in slots}
        candidates["head"].append({
            "Name": "Test Helm", "Name2": "Test Helm", "Accuracy": 30,
            "Attack": 20, "PDT": -50, "MDT": -50,
        })
        payloads = [{"name": "Savage_Default", "descriptor": {
            "role": "ws", "family": "Savage", "ws_name": "Savage Blade",
        }}]
        catalog = build_profile_catalog("war", payloads, candidates, 1000)
        self.assertIn("Tp_Default", catalog["sets"])
        self.assertIn("Savage_Default", catalog["sets"])
        self.assertEqual(catalog["recipe_details"]["Tp_Default"]["optimization_state"], "base")
        self.assertEqual(catalog["recipe_details"]["Dt"]["optimization_state"], "ready")
        self.assertEqual(catalog["recipe_details"]["Savage_Default"]["section_type"], "Weapon skill")
        self.assertEqual(catalog["recipe_details"]["Tp_Hybrid"]["mdt_target"], 25)
        self.assertEqual(
            catalog["recipe_details"]["Tp_Hybrid_Acc"]["variant"],
            "Hybrid · Accuracy",
        )
        self.assertEqual(
            catalog["recipe_details"]["Tp_Hybrid_HighAcc"]["optimizer"]["enemy"],
            "Apex Archaic Cogs",
        )

    def test_profile_catalog_keeps_distinct_weapon_layers_even_with_same_armor(self):
        empty = {"Name": "Empty", "Name2": "Empty", "Type": "None", "Skill Type": "None"}
        slots = ("head", "neck", "ear1", "ear2", "body", "hands", "ring1", "ring2", "back", "waist", "legs", "feet")
        candidates = {slot: [empty.copy()] for slot in slots}
        candidates["ammo"] = [empty.copy()]
        payloads = [
            {"name": "Weapon_Masamune", "descriptor": {"role": "weapon"},
             "specified_slots": {"main", "sub"}, "gearset": {
                 "main": {"Name": "Masamune", "Name2": "Masamune", "Type": "Weapon", "Skill Type": "Great Katana"},
                 "sub": empty.copy(),
             }},
            {"name": "Weapon_Bow", "descriptor": {"role": "weapon"},
             "specified_slots": {"main", "ranged", "ammo"}, "gearset": {
                 "main": {"Name": "Bow", "Name2": "Bow", "Type": "Weapon", "Skill Type": "Bow"},
                 "ranged": {"Name": "Bow", "Name2": "Bow", "Type": "Bow", "Skill Type": "Archery"},
                 "ammo": {"Name": "Arrow", "Name2": "Arrow", "Type": "Arrow", "Skill Type": "None"},
             }},
        ]
        catalog = build_profile_catalog("sam", payloads, candidates, 1000)
        self.assertIn("Tp_Default_Masamune", catalog["sets"])
        self.assertIn("Tp_Default_Bow", catalog["sets"])
        self.assertEqual(catalog["recipe_details"]["Tp_Default_Masamune"]["weapon_overlay"], "Weapon_Masamune")
        self.assertEqual(catalog["recipe_details"]["Tp_Default_Bow"]["weapon_overlay"], "Weapon_Bow")

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
        self.assertEqual(_profile_set_descriptor("Tp_HighAcc")["variant"], "HighAcc")
        self.assertEqual(_profile_set_descriptor("Tp_Hybrid_Acc")["variant"], "HybridAcc")
        self.assertEqual(
            _profile_set_descriptor("Tp_Hybrid_HighAcc")["variant"],
            "HybridHighAcc",
        )
        self.assertIsNone(_profile_category("Ws_Hybrid"))
        self.assertIsNone(_profile_category("Idle"))
        self.assertEqual(_profile_set_descriptor("Ws_Hybrid")["role"], "ws_base")

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
