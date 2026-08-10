import contextlib
import hashlib
import io
import json
import os
import unittest
from unittest.mock import patch

# Formula-parity tests do not need machine-code compilation.
os.environ.setdefault("NUMBA_DISABLE_JIT", "1")

import numpy as np

import actions
import create_player as player_module
import wsdist as wsdist_module
from new_gui_main import SLOTS as GUI_SLOTS, _optimizer_check_gear
from create_player import (
    calculate_damage_taken,
    create_enemy,
    create_player,
    damage_taken_from_totals,
    damage_taken_item_values,
    damage_taken_totals,
)
from enemies import preset_enemies
from gear import *
from wsdist import (
    CombinedSetResult, apply_forced_empty_slots, build_set, optimize_set,
    prune_dominated_candidates,
    starting_item_candidates,
)


def empty_gearset():
    empty = {"Name": "Empty", "Name2": "Empty", "Type": "None", "Skill Type": "None", "Jobs": all_jobs}
    return {slot: empty.copy() for slot in (
        "main", "sub", "ranged", "ammo", "head", "body", "hands", "legs", "feet",
        "neck", "waist", "ear1", "ear2", "ring1", "ring2", "back",
    )}


class PerformanceParityTests(unittest.TestCase):
    def test_ws_tp_bonus_includes_weapon_and_moonshade_before_cap(self):
        gearset = empty_gearset()
        gearset["main"] = Hitaki
        gearset["ear1"] = Moonshade_Earring
        player = create_player("nin", "war", 50, gearset, {}, {})

        # Hitaki +1000, Moonshade +250, and the existing Fencer bonus all
        # contribute before the effective WS TP cap is applied.
        self.assertEqual(player.stats.get("TP Bonus"), 1550)
        self.assertEqual(actions.effective_ws_tp(1000, player), 2550)
        self.assertEqual(actions.effective_ws_tp(2000, player), 3000)

    def test_centovente_stacks_with_moonshade_but_not_itself(self):
        gearset = empty_gearset()
        gearset["main"] = Centovente
        gearset["ear1"] = Moonshade_Earring
        player = create_player("thf", "war", 50, gearset, {}, {})

        # One Centovente contributes +1000, Moonshade contributes +250, and
        # the existing Fencer bonus contributes +300.
        self.assertEqual(player.stats.get("TP Bonus"), 1550)

    def test_sagitta_path_a_store_tp_and_double_damage(self):
        gearset = empty_gearset()
        gearset["main"] = Sagitta
        player = create_player("mnk", "war", 50, gearset, {}, {})

        self.assertEqual(Sagitta["Store TP"], 25)
        self.assertEqual(Sagitta["Double Damage"], 50)
        self.assertEqual(player.stats.get("Store TP"), 25)
        self.assertEqual(player.stats.get("Double Damage"), 50)

        # The augment affects the first main-hand auto-attack hit. It must not
        # be treated as Double Attack or leak into weapon-skill damage.
        enemy = create_enemy(preset_enemies["Apex Toad"])
        enemy.stats["Base Defense"] = preset_enemies["Apex Toad"]["Defense"]
        enemy.stats["Magic Defense"] = max(-50, enemy.stats.get("Magic Defense", 0))
        without_augment = dict(Sagitta)
        without_augment.pop("Store TP")
        without_augment.pop("Double Damage")
        baseline = gearset.copy()
        baseline["main"] = without_augment
        baseline_player = create_player("mnk", "war", 50, baseline, {}, {})
        enhanced = actions.average_attack_round(player, enemy, 0, 1000, "Damage dealt")
        unenhanced = actions.average_attack_round(baseline_player, enemy, 0, 1000, "Damage dealt")
        self.assertGreater(enhanced[0], unenhanced[0])

    def test_optimizer_rejects_duplicate_centovente(self):
        """Centovente/Centovente2 must not be treated as two copies."""
        gearset = empty_gearset()
        gearset["main"] = Centovente
        gearset["sub"] = Centovente2
        check_gear = {slot: [item] for slot, item in gearset.items()}
        enemy = create_enemy(preset_enemies["Apex Toad"])
        enemy.stats["Base Defense"] = preset_enemies["Apex Toad"]["Defense"]
        enemy.stats["Magic Defense"] = max(-50, enemy.stats.get("Magic Defense", 0))

        # Spell evaluation keeps this regression test independent of melee
        # weapon-skill caps while still exercising build_set's starting-set
        # normalization.
        with contextlib.redirect_stdout(io.StringIO()):
            player, _output, _metric = build_set(
                "thf", "war", 50, {}, {}, enemy, "Rudra's Storm", "Stone",
                "spell cast", 1000, check_gear, gearset, 199, 199,
                "Damage dealt", False, 1, seed=20260809, n_iter=1,
                return_details=True, preserve_starting_gearset=True,
            )

        self.assertEqual(player.gearset["sub"]["Name"], "Empty")
        self.assertEqual(player.stats.get("TP Bonus"), 1300)

    def optimizer_inputs(self):
        base = {
            "main": Heishi, "sub": Crepuscular_Knife, "ranged": Empty, "ammo": Seki,
            "head": Malignance_Chapeau, "body": Tatenashi_Haramaki,
            "hands": Malignance_Gloves, "legs": Samnuha_Tights, "feet": Malignance_Boots,
            "neck": Ninja_Nodowa, "waist": Sailfi_Belt, "ear1": Dedition_Earring,
            "ear2": Telos_Earring, "ring1": Gere_Ring, "ring2": Epona_Ring,
            "back": next(item for item in capes if "nin" in item["Jobs"]
                         and "DEX Store TP" in item["Name2"] and "Ranged" not in item),
        }
        check_gear = {slot: [item] for slot, item in base.items()}
        for slot in ("head", "body", "hands", "legs", "feet"):
            check_gear[slot].extend(
                item for item in gear_dict[slot] if "nin" in item["Jobs"] and item != base[slot]
            )
            check_gear[slot] = check_gear[slot][:3]

        enemy = create_enemy(preset_enemies["Apex Toad"])
        enemy.stats["Base Defense"] = preset_enemies["Apex Toad"]["Defense"]
        enemy.stats["Magic Defense"] = max(-50, enemy.stats.get("Magic Defense", 0))
        return base, check_gear, enemy

    def test_fast_damage_taken_matches_full_player(self):
        gearset = empty_gearset()
        gearset["main"] = {
            "Name": "Bravura", "Name2": "Bravura", "Type": "Weapon",
            "Skill Type": "Great Axe", "DT": "-8", "Jobs": ["war"],
        }
        gearset["body"] = {**gearset["body"], "Name": "Test Body", "PDT": -12, "MDT": -7, "DT": -5}
        gearset["ring1"] = {**gearset["ring1"], "Name": "Test Ring", "PDT2": -4, "MDT2": -6, "DT2": -2}
        buffs = {"whm": {"MDT": -29}}
        abilities = {"Aftermath": 1}

        player = create_player("war", "nin", 50, gearset, buffs, abilities)
        expected_pdt = max(-50, player.stats.get("PDT", 0) + player.stats.get("DT", 0))
        expected_mdt = max(-50, player.stats.get("MDT", 0) + player.stats.get("DT", 0))
        expected_pdt += player.stats.get("PDT2", 0) + player.stats.get("DT2", 0)
        expected_mdt += player.stats.get("MDT2", 0) + player.stats.get("DT2", 0)

        self.assertEqual(calculate_damage_taken(gearset, buffs, abilities), (expected_pdt, expected_mdt))

    def test_dt_contributes_to_both_physical_and_magical_reduction(self):
        gearset = empty_gearset()
        gearset["body"] = {
            **gearset["body"], "Name": "DT test", "PDT": -20, "MDT": -5, "DT": -30,
        }

        # 30% DT + 20% PDT = 50% PDT; 30% DT + 5% MDT = 35% MDT.
        self.assertEqual(calculate_damage_taken(gearset), (-50, -35))

    def test_damage_taken_delta_matches_full_recalculation(self):
        gearset = empty_gearset()
        gearset["main"] = {**gearset["main"], "Name": "Bravura", "DT": -8}
        gearset["body"] = {**gearset["body"], "Name": "Base Body", "PDT": -8, "MDT": -4}
        replacement = {**gearset["body"], "Name": "Replacement Body", "PDT": -12, "MDT2": -3}
        buffs = {"whm": {"MDT": -29}}
        abilities = {"Aftermath": 1}
        item_cache = {}
        totals = damage_taken_totals(gearset, buffs, item_cache)
        delta_totals = totals.copy()
        for index, (old, new) in enumerate(zip(
                damage_taken_item_values(gearset["body"], item_cache),
                damage_taken_item_values(replacement, item_cache))):
            delta_totals[index] += new - old

        replacement_set = gearset.copy()
        replacement_set["body"] = replacement
        self.assertEqual(
            damage_taken_from_totals(delta_totals, replacement_set["main"], abilities),
            calculate_damage_taken(replacement_set, buffs, abilities, item_cache),
        )

    def test_forced_empty_candidate_does_not_leak_or_prune_normal_armor(self):
        baseline = empty_gearset()
        for slot in ("hands", "legs", "feet"):
            baseline[slot] = {
                **baseline[slot], "Name": f"Test {slot}", "Name2": f"Test {slot}",
                "Type": "Armor", "Attack": 10,
            }
        normal_body = {
            **baseline["body"], "Name": "Normal Body", "Name2": "Normal Body",
            "Type": "Armor", "Attack": 1,
        }
        onca = {
            **normal_body, "Name": "Onca Suit", "Name2": "Onca Suit", "Attack": 100,
        }

        onca_candidate = baseline.copy()
        onca_candidate["body"] = onca
        self.assertEqual(apply_forced_empty_slots(onca_candidate), {"hands", "legs", "feet"})

        normal_candidate = baseline.copy()
        normal_candidate["body"] = normal_body
        self.assertEqual(apply_forced_empty_slots(normal_candidate), set())
        self.assertTrue(all(normal_candidate[slot]["Name"] != "Empty" for slot in ("hands", "legs", "feet")))

        pruned, removed = prune_dominated_candidates({"body": [normal_body, onca]})
        self.assertEqual(removed, 0)
        self.assertEqual({item["Name"] for item in pruned["body"]}, {"Normal Body", "Onca Suit"})
        self.assertEqual(starting_item_candidates([onca, normal_body]), [normal_body])

    def test_optimizer_scores_onca_with_its_empty_slot_cost(self):
        base, _check_gear, enemy = self.optimizer_inputs()
        onca = dict(base["body"])
        onca.update({"Name": "Onca Suit", "Name2": "Onca Suit"})
        check_gear = {slot: [item] for slot, item in base.items()}
        check_gear["body"] = [onca, base["body"]]

        with contextlib.redirect_stdout(io.StringIO()):
            player, _output, _metric = build_set(
                "nin", "war", 50, {}, {}, enemy, "Blade: Shun", "Waterja", "weapon skill",
                1000, check_gear, base.copy(), 199, 199, "Damage Dealt", False, 1,
                dt_requirement=199, seed=0, n_iter=2, return_details=True,
            )

        self.assertNotEqual(player.gearset["body"]["Name"], "Onca Suit")
        self.assertNotEqual(player.gearset["hands"]["Name"], "Empty")
        self.assertNotEqual(player.gearset["legs"]["Name"], "Empty")
        self.assertNotEqual(player.gearset["feet"]["Name"], "Empty")

    def test_footwork_optimizer_prefers_nonempty_tied_armor_slot(self):
        """A neutral gear slot must not remain randomly Empty under Footwork."""
        gearset = empty_gearset()
        gearset["main"] = Spharai
        check_gear = {slot: [item] for slot, item in gearset.items()}
        check_gear["waist"] = [Empty, Null_Belt]
        enemy = create_enemy(preset_enemies["Apex Toad"])
        enemy.stats["Base Defense"] = preset_enemies["Apex Toad"]["Defense"]
        enemy.stats["Magic Defense"] = max(-50, enemy.stats.get("Magic Defense", 0))

        with contextlib.redirect_stdout(io.StringIO()):
            player, _output, _metric = build_set(
                "mnk", "war", 50, {}, {"Footwork": True}, enemy,
                "Victory Smite", "", "attack round", 1000, check_gear,
                gearset, 199, 199, "Damage dealt", False, 1,
                seed=20260809, n_iter=1, return_details=True,
                preserve_starting_gearset=True,
            )

        self.assertEqual(player.gearset["waist"]["Name"], "Null Belt")

    def test_footwork_kick_attack_modifier_affects_auto_attack_damage(self):
        gearset = empty_gearset()
        gearset["main"] = Spharai
        enemy = create_enemy(preset_enemies["Apex Toad"])

        without_footwork = create_player("mnk", "war", 50, gearset, {}, {})
        with_footwork = create_player(
            "mnk", "war", 50, gearset, {}, {"Footwork": True}
        )

        with contextlib.redirect_stdout(io.StringIO()):
            normal = actions.average_attack_round(
                without_footwork, enemy, 0, 1000, "Damage dealt"
            )
            footwork = actions.average_attack_round(
                with_footwork, enemy, 0, 1000, "Damage dealt"
            )
            normal_time = actions.average_attack_round(
                without_footwork, enemy, 0, 1000, "Time to WS"
            )
            footwork_time = actions.average_attack_round(
                with_footwork, enemy, 0, 1000, "Time to WS"
            )

        self.assertGreater(footwork[0], normal[0])
        self.assertGreater(footwork[1][1], normal[1][1])
        self.assertLess(footwork_time[0], normal_time[0])

    def test_monk_kick_attack_rate_includes_trait_and_full_merits(self):
        gearset = empty_gearset()
        gearset["main"] = Spharai

        normal = create_player("mnk", "war", 50, gearset, {}, {})
        footwork = create_player(
            "mnk", "war", 50, gearset, {}, {"Footwork": True}
        )

        self.assertEqual(normal.stats["Kick Attacks"], 14 + 5)
        self.assertEqual(footwork.stats["Kick Attacks"], 14 + 5 + 20)

    def test_footwork_attack_modifier_requires_actual_enhancing_feet(self):
        empty_feet_set = empty_gearset()
        empty_feet_set["main"] = Spharai
        bhikku_set = {slot: dict(item) for slot, item in empty_feet_set.items()}
        bhikku_set["feet"] = Bhikku_Gaiters

        empty_feet = create_player(
            "mnk", "war", 50, empty_feet_set, {}, {"Footwork": True}
        )
        bhikku = create_player(
            "mnk", "war", 50, bhikku_set, {}, {"Footwork": True}
        )

        self.assertAlmostEqual(
            empty_feet.stats["Kick Attacks Attack%"], 25 / 256
        )
        self.assertAlmostEqual(
            bhikku.stats["Kick Attacks Attack%"], 25 / 256 + 0.16
        )

    def test_footwork_optimizer_keeps_neutral_armor_equipped_on_tie(self):
        gearset = empty_gearset()
        gearset["main"] = Spharai
        neutral_cap = {
            "Name": "Neutral Cap",
            "Name2": "Neutral Cap",
            "Defense": 120,
            "Evasion": 80,
            "Magic Evasion": 100,
            "Magic Defense": 8,
            "Jobs": all_jobs,
        }
        check_gear = {slot: [item] for slot, item in gearset.items()}
        check_gear["head"] = [Empty, neutral_cap]
        enemy = create_enemy(preset_enemies["Apex Toad"])

        with contextlib.redirect_stdout(io.StringIO()):
            player, _output, _metric = build_set(
                "mnk", "war", 50, {}, {"Footwork": True}, enemy,
                "Victory Smite", "", "attack round", 1000, check_gear,
                gearset, 199, 199, "Time to WS", False, 1,
                seed=20260809, n_iter=1, return_details=True,
                preserve_starting_gearset=True,
            )

        self.assertEqual(player.gearset["head"]["Name"], "Neutral Cap")

    def test_time_to_ws_optimizer_compares_baseline_and_candidates_on_same_scale(self):
        defensive_cap = {
            "Name": "Defensive Cap",
            "Name2": "Defensive Cap",
            "Defense": 150,
            "Magic Evasion": 150,
            "Jobs": all_jobs,
        }
        multiattack_cap = {
            "Name": "Multiattack Cap",
            "Name2": "Multiattack Cap",
            "DA": 20,
            "Jobs": all_jobs,
        }
        enemy = create_enemy(preset_enemies["Apex Toad"])
        cases = (
            ("mnk", "war", Spharai, Empty),
            ("brd", "war", Centovente, Empty),
        )
        for main_job, sub_job, weapon, sub_weapon in cases:
            with self.subTest(main_job=main_job):
                gearset = empty_gearset()
                gearset["main"] = weapon
                gearset["sub"] = sub_weapon
                gearset["head"] = defensive_cap
                check_gear = {slot: [item] for slot, item in gearset.items()}
                check_gear["head"] = [defensive_cap, multiattack_cap]

                with contextlib.redirect_stdout(io.StringIO()):
                    player, output, normalized_metric = build_set(
                        main_job, sub_job, 50, {}, {}, enemy,
                        "", "", "attack round", 1000, check_gear,
                        gearset, 199, 199, "Time to WS", False, 1,
                        seed=20260810, n_iter=1, return_details=True,
                        preserve_starting_gearset=True,
                    )

                self.assertEqual(player.gearset["head"]["Name"], "Multiattack Cap")
                self.assertEqual(output[-1], -1)
                expected_time = output[2] * 1000 / output[1]
                self.assertAlmostEqual(normalized_metric, 1 / expected_time)

    def test_base_stats_cache_is_isolated_and_configuration_sensitive(self):
        player_module._BASE_STATS_CACHE.clear()
        gearset = empty_gearset()
        first = create_player("sam", "war", 50, gearset, {}, {"Overwhelm": False})
        second = create_player("sam", "war", 50, gearset, {}, {"Overwhelm": False})
        self.assertEqual(first.stats, second.stats)

        first.stats["STR"] = -999
        third = create_player("sam", "war", 50, gearset, {}, {"Overwhelm": False})
        self.assertNotEqual(first.stats["STR"], third.stats["STR"])

        overwhelm = create_player("sam", "war", 50, gearset, {}, {"Overwhelm": True})
        self.assertEqual(
            overwhelm.stats["Weapon Skill Damage"] - third.stats["Weapon Skill Damage"],
            19,
        )

    def test_optimizer_fixed_seed_golden_result(self):
        base, check_gear, enemy = self.optimizer_inputs()
        with contextlib.redirect_stdout(io.StringIO()):
            player, output = build_set(
                "nin", "war", 50, {}, {}, enemy, "Blade: Shun", "Waterja", "weapon skill",
                1000, check_gear, base.copy(), 0, 0, "Damage Dealt", False, 1,
                seed=20260807,
            )

        payload = {
            "gear": {slot: item.get("Name2", item.get("Name")) for slot, item in player.gearset.items()},
            "output": output,
            "stats": player.stats,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(
            hashlib.sha256(encoded).hexdigest(),
            "86fc56408b8b263ce9bbd03c1082e20618524bbb9ff234eea577d7683448dee0",
        )

    def test_parallel_restarts_match_serial_restarts(self):
        def run(workers):
            base, check_gear, enemy = self.optimizer_inputs()
            with contextlib.redirect_stdout(io.StringIO()):
                return optimize_set(
                    "nin", "war", 50, {}, {}, enemy, "Blade: Shun", "Waterja", "weapon skill",
                    1000, check_gear, base.copy(), 0, 0, "Damage Dealt", False, 1,
                    restarts=2, workers=workers, seed=20260807, n_iter=3, return_details=True,
                )

        serial = run(workers=1)
        parallel = run(workers=2)
        self.assertEqual(serial[2:], parallel[2:])
        self.assertEqual(
            {slot: item.get("Name2", item.get("Name")) for slot, item in serial[0].gearset.items()},
            {slot: item.get("Name2", item.get("Name")) for slot, item in parallel[0].gearset.items()},
        )

    def test_split_single_run_returns_ranked_results(self):
        base, check_gear, enemy = self.optimizer_inputs()
        with contextlib.redirect_stdout(io.StringIO()):
            result = optimize_set(
                "nin", "war", 50, {}, {}, enemy, "Blade: Shun", "Waterja", "weapon skill",
                1000, check_gear, base.copy(), 0, 0, "Damage Dealt", False, 1,
                workers=2, seed=20260807, n_iter=2, return_details=True,
                return_top_results=True, parallel_mode="single_run",
            )
        player, _output, metric, _seed, ranked = result
        self.assertGreater(metric, 0)
        self.assertTrue(ranked)
        self.assertEqual(player.gearset, ranked[0]["player"].gearset)

    def test_split_worker_falls_back_when_defensive_set_needs_multiple_passes(self):
        """A valid three-slot defensive set must not become a split-worker false negative."""
        base, _check_gear, enemy = self.optimizer_inputs()
        check_gear = {slot: [item] for slot, item in base.items()}
        for slot in ("body", "legs", "feet"):
            defensive_item = dict(base[slot])
            defensive_item["Name"] = f"Defensive {slot}"
            defensive_item["Name2"] = defensive_item["Name"]
            defensive_item["DT"] = defensive_item.get("DT", 0) - 5
            # Let the normal optimizer prefer the defensive swap while it
            # makes the successive passes needed to meet both totals.
            defensive_item["Attack"] = defensive_item.get("Attack", 0) + 5
            check_gear[slot].append(defensive_item)

        messages = []
        with contextlib.redirect_stdout(io.StringIO()):
            player, _output, _metric, _seed, _ranked = optimize_set(
                "nin", "war", 50, {}, {}, enemy, "Blade: Shun", "Waterja", "attack round",
                1000, check_gear, base.copy(), -40, -30, "DPS", False, 1,
                dt_requirement=199, workers=2, seed=20260807, n_iter=3,
                return_details=True, return_top_results=True, parallel_mode="single_run",
                progress_callback=messages.append,
            )

        self.assertEqual(calculate_damage_taken(player.gearset), (-40, -30))
        self.assertTrue(any("full-search fallback" in message for message in messages))

    def test_independent_restarts_find_feasible_defensive_set(self):
        """Independent restart workers must retain feasible PDT/MDT candidates."""
        base, _check_gear, enemy = self.optimizer_inputs()
        check_gear = {slot: [item] for slot, item in base.items()}
        for slot in ("body", "legs", "feet"):
            defensive_item = dict(base[slot])
            defensive_item["Name"] = f"Independent defensive {slot}"
            defensive_item["Name2"] = defensive_item["Name"]
            defensive_item["DT"] = defensive_item.get("DT", 0) - 5
            defensive_item["Attack"] = defensive_item.get("Attack", 0) + 5
            check_gear[slot].append(defensive_item)

        with contextlib.redirect_stdout(io.StringIO()):
            player, _output, _metric, _seed, _ranked = optimize_set(
                "nin", "war", 50, {}, {}, enemy, "Blade: Shun", "Waterja", "attack round",
                1000, check_gear, base.copy(), -40, -30, "DPS", False, 1,
                dt_requirement=199, restarts=4, workers=4, seed=20260807, n_iter=1,
                return_details=True, return_top_results=True, parallel_mode="search_runs",
            )

        self.assertEqual(calculate_damage_taken(player.gearset), (-40, -30))

    def test_defensive_phase_filters_before_damage_simulation(self):
        """Damage simulation begins only after PDT/MDT/DT targets are satisfied."""
        base, _check_gear, enemy = self.optimizer_inputs()
        check_gear = {slot: [item] for slot, item in base.items()}
        for slot in ("body", "legs", "feet"):
            defensive_item = dict(base[slot])
            defensive_item["Name"] = f"Phase defensive {slot}"
            defensive_item["Name2"] = defensive_item["Name"]
            defensive_item["DT"] = defensive_item.get("DT", 0) - 5
            defensive_item["Attack"] = defensive_item.get("Attack", 0) + 5
            check_gear[slot].append(defensive_item)

        calls = []
        original_attack_round = wsdist_module.average_attack_round

        def checked_attack_round(player, *args, **kwargs):
            calls.append(calculate_damage_taken(player.gearset))
            self.assertLessEqual(calls[-1][0], -40)
            self.assertLessEqual(calls[-1][1], -30)
            return original_attack_round(player, *args, **kwargs)

        with patch.object(wsdist_module, "average_attack_round", side_effect=checked_attack_round):
            with contextlib.redirect_stdout(io.StringIO()):
                build_set(
                    "nin", "war", 50, {}, {}, enemy, "Blade: Shun", "Waterja", "attack round",
                    1000, check_gear, base.copy(), -40, -30, "DPS", False, 1,
                    dt_requirement=199, seed=20260807, n_iter=1, return_details=True,
                    preserve_starting_gearset=True,
                )

        self.assertTrue(calls)

    def test_optimizer_does_not_restore_unselected_weapon(self):
        empty = {"Name": "Empty", "Name2": "Empty", "Type": "None", "Skill Type": "None"}
        allowed = {"Name": "Allowed weapon", "Name2": "Allowed weapon", "Type": "Weapon", "Skill Type": "Katana"}
        unselected = {"Name": "Unselected weapon", "Name2": "Unselected weapon", "Type": "Weapon", "Skill Type": "Katana"}
        items = {slot: [empty.copy()] for slot in GUI_SLOTS}
        items["main"] = [empty, allowed, unselected]
        quick = {slot: empty.copy() for slot in GUI_SLOTS}
        quick["main"] = unselected

        check_gear, missing = _optimizer_check_gear(
            {slot: {"Empty"} for slot in GUI_SLOTS} | {"main": {"Allowed weapon"}},
            items,
            quick,
        )
        self.assertEqual([item["Name"] for item in check_gear["main"]], ["Allowed weapon"])
        self.assertNotIn("Unselected weapon", [item["Name"] for item in check_gear["main"]])
        self.assertNotIn("main", missing)

        check_gear, missing = _optimizer_check_gear(
            {slot: {"Empty"} for slot in GUI_SLOTS} | {"main": set()}, items, quick
        )
        self.assertEqual(check_gear["main"], [])
        self.assertIn("main", missing)

    def test_optimizer_selected_sub_replaces_quick_look_starting_sub(self):
        base, _check_gear, enemy = self.optimizer_inputs()
        selected_sub = dict(base["sub"])
        selected_sub["Name"] = "Optimizer selected sub"
        selected_sub["Name2"] = selected_sub["Name"]
        selected_sub["Jobs"] = ["NIN"]  # Bridge exports may use uppercase job names.
        check_gear = {slot: [item] for slot, item in base.items()}
        check_gear["sub"] = [selected_sub]

        with contextlib.redirect_stdout(io.StringIO()):
            player, _output, _metric = build_set(
                "nin", "war", 50, {}, {}, enemy, "Blade: Shun", "Waterja", "attack round",
                1000, check_gear, base.copy(), 199, 199, "DPS", False, 1,
                seed=20260807, n_iter=1, return_details=True, preserve_starting_gearset=True,
            )

        self.assertEqual(player.gearset["sub"]["Name"], "Optimizer selected sub")

    def test_independent_all_failure_retries_selected_starting_set(self):
        """All randomized failures should get one deterministic full-search retry."""
        base, check_gear, enemy = self.optimizer_inputs()
        failed_runs = [
            {"index": index, "seed": index, "error": "No valid gear set satisfies the current PDT/MDT/DT requirements."}
            for index in range(1, 5)
        ]
        fallback = {
            "index": 0, "seed": 1, "player": create_player("nin", "war", 50, base.copy()),
            "output": (1.0, (1.0, 1.0, 1.0), 1.0), "metric": 1.0, "log": "",
        }
        messages = []
        with patch.object(wsdist_module, "_build_set_restart_worker", side_effect=failed_runs + [fallback]):
            result = optimize_set(
                "nin", "war", 50, {}, {}, enemy, "Blade: Shun", "Waterja", "attack round",
                1000, check_gear, base.copy(), -40, -25, "DPS", False, 1,
                dt_requirement=199, restarts=4, workers=1, seed=1, n_iter=1,
                return_details=True, return_top_results=True, progress_callback=messages.append,
            )

        self.assertEqual(result[0].gearset, base)
        self.assertTrue(any("full search from the selected set" in message for message in messages))
        self.assertTrue(any("fallback completed" in message.lower() for message in messages))

    def test_combined_optimizer_returns_separate_tp_ws_sets(self):
        """Combined mode must retain the independently optimized TP and WS sets."""
        base, check_gear, enemy = self.optimizer_inputs()
        with contextlib.redirect_stdout(io.StringIO()):
            result = optimize_set(
                "nin", "war", 50, {}, {}, enemy, "Blade: Shun", "None", "combined tp/ws",
                1000, check_gear, base.copy(), 199, 199, "Combined DPS", False, 1,
                dt_requirement=199,
                tp_starting_gearset=base.copy(),
                ws_starting_gearset=base.copy(),
                restarts=1, workers=2, seed=20260807, n_iter=1,
                return_details=True, return_top_results=True,
            )

        combined_player, _output, metric, _seed, ranked = result
        self.assertIsInstance(combined_player, CombinedSetResult)
        self.assertIsNot(combined_player.tp_player, combined_player.ws_player)
        self.assertGreater(metric, 0)
        self.assertTrue(ranked)
        self.assertIn("tp_player", ranked[0])
        self.assertIn("ws_player", ranked[0])
        self.assertEqual(
            combined_player.tp_player.gearset["main"],
            combined_player.ws_player.gearset["main"],
        )
        self.assertEqual(
            combined_player.tp_player.gearset["sub"],
            combined_player.ws_player.gearset["sub"],
        )

    def test_combined_defense_scope_can_apply_only_to_tp(self):
        base, _check_gear, enemy = self.optimizer_inputs()
        check_gear = {slot: [item] for slot, item in base.items()}
        for slot in ("body", "legs", "feet"):
            candidate = dict(base[slot])
            candidate["Name"] = f"Combined defense {slot}"
            candidate["Name2"] = candidate["Name"]
            candidate["DT"] = candidate.get("DT", 0) - 5
            candidate["Attack"] = candidate.get("Attack", 0) + 5
            check_gear[slot].append(candidate)

        with contextlib.redirect_stdout(io.StringIO()):
            result = optimize_set(
                "nin", "war", 50, {}, {}, enemy, "Blade: Shun", "None", "combined tp/ws",
                1000, check_gear, base.copy(), -40, -30, "Combined DPS", False, 1,
                dt_requirement=199, tp_starting_gearset=base.copy(), ws_starting_gearset=base.copy(),
                restarts=1, workers=1, seed=20260807, n_iter=1,
                return_details=True, return_top_results=True, combined_defense_both=False,
            )

        self.assertEqual(calculate_damage_taken(result[0].tp_player.gearset), (-40, -30))
        self.assertGreaterEqual(calculate_damage_taken(result[0].ws_player.gearset)[0], -40)

    def test_combined_tp_weapon_can_differ_from_ws_weapon(self):
        base, _check_gear, enemy = self.optimizer_inputs()
        tp_start = base.copy()
        dagger_tp = dict(Ternion)
        dagger_tp["Attack"] = dagger_tp.get("Attack", 0) + 100
        check_gear = {slot: [item] for slot, item in tp_start.items()}
        check_gear["main"] = [base["main"], dagger_tp]
        ws_player = create_player("nin", "war", 50, base.copy(), {}, {})

        with contextlib.redirect_stdout(io.StringIO()):
            player, _output, _metric = build_set(
                "nin", "war", 50, {}, {}, enemy, "Blade: Shun", "None", "combined tp/ws",
                1000, check_gear, tp_start, 199, 199, "Combined DPS", False, 1,
                dt_requirement=199, seed=20260807, n_iter=1, return_details=True,
                preserve_starting_gearset=True, combined_ws_player=ws_player,
            )

        self.assertEqual(player.gearset["main"]["Name2"], dagger_tp["Name2"])


if __name__ == "__main__":
    unittest.main()
