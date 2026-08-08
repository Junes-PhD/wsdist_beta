import contextlib
import hashlib
import io
import json
import os
import unittest

# Formula-parity tests do not need machine-code compilation.
os.environ.setdefault("NUMBA_DISABLE_JIT", "1")

import numpy as np

import create_player as player_module
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
from wsdist import build_set, optimize_set


def empty_gearset():
    empty = {"Name": "Empty", "Name2": "Empty", "Type": "None", "Skill Type": "None", "Jobs": all_jobs}
    return {slot: empty.copy() for slot in (
        "main", "sub", "ranged", "ammo", "head", "body", "hands", "legs", "feet",
        "neck", "waist", "ear1", "ear2", "ring1", "ring2", "back",
    )}


class PerformanceParityTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
