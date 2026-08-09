import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("NUMBA_DISABLE_JIT", "1")

import actions
from create_player import create_enemy, create_player
from enemies import preset_enemies
from gear import Empty, Sagitta, Tauret, all_jobs
from get_delay_timing import get_delay_timing
from get_hit_rate import get_hit_rate


SLOTS = (
    "main", "sub", "ranged", "ammo", "head", "body", "hands", "legs",
    "feet", "neck", "waist", "ear1", "ear2", "ring1", "ring2", "back",
)


def empty_gearset():
    return {slot: dict(Empty, Name2="Empty", Jobs=all_jobs) for slot in SLOTS}


class CombatRegressionTests(unittest.TestCase):
    @staticmethod
    def _test_weapon(name, weapon_type, skill):
        item = dict(Empty)
        item.update(
            Name=name, Name2=name, Type=weapon_type, **{"Skill Type": skill},
            Jobs=all_jobs, Delay=240, DMG=100,
        )
        return item

    def test_fencer_only_applies_to_single_handed_weapon_with_shield(self):
        sword = self._test_weapon("Test Sword", "Weapon", "Sword")
        shield = self._test_weapon("Test Shield", "Shield", "None")
        dagger = self._test_weapon("Test Dagger", "Weapon", "Dagger")
        h2h = self._test_weapon("Test H2H", "Weapon", "Hand-to-Hand")

        shield_set = empty_gearset()
        shield_set["main"], shield_set["sub"] = sword, shield
        dual_set = empty_gearset()
        dual_set["main"], dual_set["sub"] = sword, dagger
        h2h_set = empty_gearset()
        h2h_set["main"] = h2h

        shield_player = create_player("war", "nin", 50, shield_set, {}, {})
        dual_player = create_player("war", "nin", 50, dual_set, {}, {})
        h2h_player = create_player("mnk", "war", 50, h2h_set, {}, {})

        self.assertGreater(shield_player.stats.get("TP Bonus", 0), 0)
        self.assertEqual(dual_player.stats.get("TP Bonus", 0), 0)
        self.assertEqual(h2h_player.stats.get("TP Bonus", 0), 0)

    def test_player_construction_does_not_mutate_input_gear(self):
        gearset = empty_gearset()
        gearset["main"] = Sagitta
        original_sub = dict(gearset["sub"])

        player = create_player("mnk", "war", 50, gearset, {}, {})

        self.assertEqual(gearset["sub"], original_sub)
        self.assertIsNot(player.gearset["sub"], gearset["sub"])
        self.assertEqual(player.gearset["sub"]["Skill Type"], "None")

    def test_dual_wield_timing_uses_average_weapon_delay(self):
        self.assertAlmostEqual(
            get_delay_timing(240, 180, 0, 0, 0, 0, 0),
            3.5,
        )
        self.assertAlmostEqual(
            get_delay_timing(240, 0, 0, 0, 0, 0, 0),
            4.0,
        )

    def test_hit_rate_floors_half_accuracy_steps(self):
        self.assertEqual(get_hit_rate(501, 500, 0.99), 0.75)
        self.assertEqual(get_hit_rate(502, 500, 0.99), 0.76)

    @staticmethod
    def quick_draw_player(store_tp=25):
        return SimpleNamespace(
            stats={
                "INT": 100, "MND": 100, "AGI": 100,
                "Magic Damage": 0, "Magic Attack": 0,
                "Magic Accuracy": 500, "Magic Crit Rate II": 0,
                "Ranged DMG": 100, "Ammo DMG": 50,
                "Ranged Delay": 600, "Ammo Delay": 120,
                "Store TP": store_tp,
            },
            abilities={"Enemy Resist Rank": "100%", "Storm spell": False},
            gearset={
                "waist": {"Name": "Empty"},
                "ranged": {"Name": "Test Gun"},
                "ammo": {"Name": "Test Bullet"},
            },
            main_job="cor",
        )

    @staticmethod
    def magic_enemy(mdt=0):
        return SimpleNamespace(stats={
            "INT": 100, "Magic Defense": 0, "Magic Evasion": 0,
            "Magic Damage Taken": mdt,
        })

    def test_quick_draw_uses_fractional_store_tp_and_signed_mdt(self):
        player = self.quick_draw_player()
        with patch.object(actions, "get_tp", side_effect=lambda _hits, _delay, stp, *args: stp):
            full = actions.cast_spell(
                player, self.magic_enemy(0), "Fire Shot", "Quick Draw", "Damage dealt"
            )
            reduced = actions.cast_spell(
                player, self.magic_enemy(-50), "Fire Shot", "Quick Draw", "Damage dealt"
            )

        self.assertEqual(full[1][1], 0.25)
        self.assertAlmostEqual(reduced[0], full[0] * 0.5)

    def test_magical_ws_tp_is_not_reduced_by_resist_rate(self):
        gearset = empty_gearset()
        gearset["main"] = Tauret
        player = create_player("thf", "war", 50, gearset, {}, {})
        enemy = create_enemy(preset_enemies["Apex Toad"])
        enemy.stats["Base Defense"] = preset_enemies["Apex Toad"]["Defense"]
        enemy.stats["Magic Damage Taken"] = enemy.stats.pop("Magic DT%")
        calls = []
        original_get_tp = actions.get_tp

        def capture_get_tp(hits, delay, stp, *args):
            calls.append((hits, delay, stp))
            return original_get_tp(hits, delay, stp, *args)

        with patch.object(actions, "get_tp", side_effect=capture_get_tp):
            actions.average_ws(
                player, enemy, "Aeolian Edge", 1000, "melee", "Damage dealt"
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 1.0)
        self.assertEqual(calls[0][1], player.stats["Delay1"])

    def test_cycle_starts_with_ws_return_and_forced_delay_regain(self):
        player = SimpleNamespace(
            stats={"Dual Wield": 0, "Regain": 30},
            gearset={"main": {"Name": "Test Weapon"}},
        )
        enemy = SimpleNamespace(stats={})
        with (
            patch.object(actions, "average_ws", return_value=(1000, [1000, 200, 1])),
            patch.object(
                actions, "average_attack_round",
                return_value=(7.8, [100, 100, 1, -1], 0),
            ) as attack,
        ):
            dps, details = actions.average_tp_ws_cycle(
                player, player, enemy, "Test WS", 1000, "melee"
            )

        attack.assert_called_once_with(player, enemy, 220.0, 1000.0, "Time to WS")
        self.assertEqual(details[6], 220.0)
        self.assertAlmostEqual(dps, 1780 / 9.8)

    def test_hybrid_magic_uses_only_the_first_physical_hit(self):
        player = SimpleNamespace(
            main_job="war", sub_job="nin",
            stats={
                "TP Bonus": 0, "WSC": [], "Delay1": 240, "Delay2": 240,
                "DMG1": 100, "DMG2": 0, "STR": 100, "DEX": 100,
                "AGI": 100, "INT": 100, "CHR": 100,
                "Store TP": 0, "Weapon Skill Damage": 0,
            },
            abilities={},
            gearset={
                "main": {"Name": "Test Sword", "Name2": "Test Sword", "Skill Type": "Sword"},
                "sub": {"Name": "Empty", "Name2": "Empty", "Type": "None"},
                "ranged": {"Name": "Empty", "Name2": "Empty"},
                "neck": {"Name": "Empty"}, "waist": {"Name": "Empty"},
            },
        )
        enemy = SimpleNamespace(stats={
            "VIT": 100, "INT": 100, "Evasion": 1, "Defense": 100,
            "Magic Defense": 0, "Magic Evasion": 0, "Magic Damage Taken": 0,
        })
        ws_info = {
            "nhits": 2, "wsc": 0, "ftp": 2.0, "ftp_rep": False,
            "ftp_hybrid": 1.0, "element": "Fire", "hybrid": True,
            "magical": False, "dSTAT": 0, "crit_rate": 0,
            "enemy_def": 100, "player_attack1": 1000, "player_attack2": 0,
            "player_accuracy1": 1000, "player_accuracy2": 0,
        }
        with (
            patch.object(actions, "weaponskill_info", return_value=ws_info),
            patch.object(actions, "get_ma_rate3", return_value=(2, 0, 0, 0, 0)),
            patch.object(actions, "get_avg_pdif_melee", return_value=1.0),
            patch.object(
                actions, "get_avg_phys_damage",
                side_effect=lambda _dmg, _fstr, _wsc, _pdif, ftp, *args: 100*ftp,
            ),
            patch.object(actions, "get_weapon_bonus", return_value=0),
        ):
            damage = actions.average_ws(
                player, enemy, "Test Hybrid", 1000, "melee", "Damage dealt"
            )[0]

        # At the 99% hit-rate cap, physical damage is 299 and the successful
        # first-hit expectation is 198. Hybrid magic uses that 198, not all
        # accumulated physical damage (which would produce 598 total here).
        self.assertEqual(damage, 497)


if __name__ == "__main__":
    unittest.main()
