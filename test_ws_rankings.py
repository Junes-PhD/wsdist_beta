import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import gear
import wsdist
from new_gui_main import _lock_ranking_weapon_slots, _ranking_weapon_types


class WeaponSkillRankingTests(unittest.TestCase):
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
