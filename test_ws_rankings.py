import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import gear
import wsdist
from new_gui_main import _lock_ranking_weapon_slots, _ranking_weapon_types


class WeaponSkillRankingTests(unittest.TestCase):
    def test_substat_optimization_builds_a_pareto_frontier_with_damage_floor(self):
        baseline = SimpleNamespace(
            gearset={"head": {"Name": "Damage"}},
            stats={"Magic Evasion": 20, "Evasion": 30, "Defense": 40},
        )
        phases = []

        def fake_phase(*args, **kwargs):
            spec = kwargs["substat_spec"]
            phases.append(spec)
            stat = spec["target"]
            value = {"Magic Evasion": 90, "Evasion": 80, "Defense": 70}[stat]
            player = SimpleNamespace(
                gearset={"head": {"Name": stat}},
                stats={"Magic Evasion": 90, "Evasion": 80, "Defense": 70},
            )
            return player, [value, 0], 950.0

        with patch.object(
            wsdist, "optimize_set",
            return_value=(baseline, [1000, 0], 1000.0, 7, []),
        ), patch.object(wsdist, "build_set", side_effect=fake_phase):
            result = wsdist.optimize_substats(
                "mnk", "war", 50, {}, {}, object(), "Howling Fist", "None",
                "weapon skill", 1000, {}, baseline.gearset, 199, 199,
                "Damage dealt", False, 2,
                [
                    {"target": "Magic Evasion", "loss_percent": 15},
                    {"target": "Evasion", "loss_percent": 15},
                    {"target": "Defense", "loss_percent": 15},
                ],
                return_details=True, return_top_results=True,
            )

        self.assertEqual([phase["target"] for phase in phases], [
            "Magic Evasion", "Evasion", "Defense",
        ])
        self.assertEqual(phases[0]["constraints"], [])
        self.assertTrue(all(phase["constraints"] == [] for phase in phases))
        self.assertTrue(all(phase["primary_floor"] == 850.0 for phase in phases))
        self.assertEqual(result[5]["mode"], "tradeoff")
        self.assertEqual(result[5]["targets"], ["Magic Evasion", "Evasion", "Defense"])
        self.assertGreaterEqual(result[5]["frontier_count"], 2)
        self.assertIn("Balanced recommendation", [entry["label"] for entry in result[4]])

    def test_ranks_each_tp_tier_independently(self):
        damages = {
            ("Asuran Fists", 1000): 1200,
            ("Victory Smite", 1000): 1800,
            ("Asuran Fists", 2000): 2100,
            ("Victory Smite", 2000): 1900,
            ("Asuran Fists", 3000): 2400,
            ("Victory Smite", 3000): 2800,
        }

        def fake_optimize(*args, **kwargs):
            ws_name, tp_value = args[6], args[9]
            damage = damages[(ws_name, tp_value)]
            return SimpleNamespace(gearset={}), [damage, 0], damage, kwargs["seed"]

        with patch.object(wsdist, "optimize_set", side_effect=fake_optimize):
            result = wsdist.rank_weapon_skills(
                "mnk", "war", 50, {}, {}, object(),
                ["Asuran Fists", "Victory Smite"], "melee", {}, {},
                199, 199, seed=100,
            )

        self.assertEqual(
            [row["ws_name"] for row in result["rankings"][1000]],
            ["Victory Smite", "Asuran Fists"],
        )
        self.assertEqual(result["rankings"][2000][0]["ws_name"], "Asuran Fists")
        self.assertEqual(result["rankings"][3000][0]["ws_name"], "Victory Smite")
        self.assertEqual(result["rankings"][3000][0]["tp"], 3000)
        self.assertEqual(result["errors"], [])

    def test_skips_unsupported_ws_without_losing_other_results(self):
        def fake_optimize(*args, **kwargs):
            if args[6] == "Final Heaven":
                raise ValueError("requires Spharai")
            return SimpleNamespace(gearset={}), [1000, 0], 1000, 1

        with patch.object(wsdist, "optimize_set", side_effect=fake_optimize):
            result = wsdist.rank_weapon_skills(
                "mnk", "war", 50, {}, {}, object(),
                ["Final Heaven", "Howling Fist"], "melee", {}, {},
                199, 199,
            )

        self.assertEqual(len(result["errors"]), 3)
        self.assertTrue(all(
            rows[0]["ws_name"] == "Howling Fist"
            for rows in result["rankings"].values()
        ))

    def test_stop_is_checked_between_ranked_weapon_skills(self):
        stopped = threading.Event()
        stopped.set()
        with self.assertRaises(wsdist.OptimizerStopped):
            wsdist.rank_weapon_skills(
                "mnk", "war", 50, {}, {}, object(), ["Combo"], "melee",
                {}, {}, 199, 199, stop_event=stopped,
            )

    def test_weapon_type_detection_and_locking(self):
        hand = {"Name": "Verethragna", "Skill Type": "Hand-to-Hand"}
        selected = {
            "main": hand, "sub": gear.Empty, "ranged": gear.Empty,
            "ammo": {"Name": "Coiste Bodhar", "Skill Type": "None"},
        }
        self.assertEqual(_ranking_weapon_types(selected), ["Hand-to-Hand"])
        candidates = {"main": [{"Name": "Other"}], "head": [{"Name": "Hat"}]}
        locked = _lock_ranking_weapon_slots(candidates, selected)
        self.assertIs(locked["main"][0], hand)
        self.assertEqual(locked["head"], candidates["head"])


if __name__ == "__main__":
    unittest.main()
