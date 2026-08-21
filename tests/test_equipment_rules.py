import unittest

from engine.equipment_rules import (
    apply_ear_slot_rules, apply_weapon_slot_rules, conditional_gear_bonuses,
    has_conditional_set_effect, is_right_ear_only,
    ranged_attack_ready,
)
from data.gear import Empty, Masamune, Masamune0, Gleti_Knife15, RostamA


def item(name, item_type="Weapon", skill_type="None"):
    return {"Name": name, "Type": item_type, "Skill Type": skill_type}


def gearset(**slots):
    result = {slot: dict(Empty) for slot in ("main", "sub", "ranged", "ammo")}
    result.update(slots)
    return result


class EquipmentRuleTests(unittest.TestCase):
    def test_all_sortie_jse_earrings_are_right_ear_only(self):
        selected = {
            "ear1": item("Lethargy Earring +2", "Armor"),
            "ear2": item("Sherida Earring", "Armor"),
        }
        self.assertTrue(is_right_ear_only(selected["ear1"]))
        apply_ear_slot_rules(selected)
        self.assertEqual(selected["ear2"]["Name"], "Lethargy Earring +2")
        self.assertEqual(selected["ear1"]["Name"], "Sherida Earring")

    def test_sortie_jse_detection_covers_other_job_earrings(self):
        for name in ("Boii Earring +2", "Hattori Earring +1", "Arbatel Earring +2"):
            self.assertTrue(is_right_ear_only({"Name": name}))
        self.assertFalse(is_right_ear_only({"Name": "Sherida Earring"}))

    def test_hand_to_hand_clears_all_sub_items(self):
        selected = gearset(
            main=item("Knuckles", "Weapon", "Hand-to-Hand"),
            sub=item("Grip", "Grip"),
        )
        changed = apply_weapon_slot_rules(selected, "mnk", "war", 50)
        self.assertEqual(selected["sub"]["Name"], "Empty")
        self.assertIn("sub", changed)

    def test_two_handed_keeps_grip_but_clears_shield(self):
        shield_set = gearset(
            main=item("Great Axe", "Weapon", "Great Axe"),
            sub=item("Shield", "Shield", "Shield"),
        )
        apply_weapon_slot_rules(shield_set, "war", "sam", 50)
        self.assertEqual(shield_set["sub"]["Name"], "Empty")

        grip_set = gearset(
            main=item("Great Axe", "Weapon", "Great Axe"),
            sub=item("Grip", "Grip"),
        )
        apply_weapon_slot_rules(grip_set, "war", "sam", 50)
        self.assertEqual(grip_set["sub"]["Name"], "Grip")

    def test_one_handed_requires_modeled_dual_wield_for_offhand_weapon(self):
        war = gearset(
            main=item("Sword", "Weapon", "Sword"),
            sub=item("Dagger", "Weapon", "Dagger"),
        )
        apply_weapon_slot_rules(war, "war", "sam", 50)
        self.assertEqual(war["sub"]["Name"], "Empty")

        nin = gearset(
            main=item("Katana", "Weapon", "Katana"),
            sub=item("Katana 2", "Weapon", "Katana"),
        )
        apply_weapon_slot_rules(nin, "nin", "war", 50)
        self.assertEqual(nin["sub"]["Name"], "Katana 2")

    def test_dynamis_rema_augment_is_main_hand_only(self):
        selected = gearset(
            main={**item("Naegling", "Weapon", "Sword")},
            sub=dict(Masamune),
        )
        changed = apply_weapon_slot_rules(selected, "sam", "nin", 50)
        self.assertEqual(selected["sub"], Masamune0)
        self.assertIn("Dynamis-Divergence", changed["sub"])

    def test_ranked_odysssey_weapon_is_not_stripped(self):
        selected = gearset(
            main=item("Naegling", "Weapon", "Sword"),
            sub=dict(Gleti_Knife15),
        )
        apply_weapon_slot_rules(selected, "nin", "war", 50)
        self.assertEqual(selected["sub"]["Name2"], "Gleti's Knife R15")

    def test_divergence_su5_path_augments_are_main_hand_only(self):
        selected = gearset(
            main=item("Naegling", "Weapon", "Sword"),
            sub=dict(RostamA),
        )
        changed = apply_weapon_slot_rules(selected, "cor", "nin", 50)
        self.assertEqual(selected["sub"]["DMG"], 132)
        self.assertNotIn("Double Damage", selected["sub"])
        self.assertNotIn("Store TP", selected["sub"])
        self.assertIn("Dynamis-Divergence", changed["sub"])

    def test_only_one_plus_1000_tp_bonus_weapon_is_allowed(self):
        selected = gearset(
            main={**item("Centovente", "Weapon", "Dagger"), "TP Bonus": 1000},
            sub={**item("Hitaki", "Weapon", "Katana"), "TP Bonus": 1000},
        )
        changed = apply_weapon_slot_rules(selected, "nin", "war", 50)
        self.assertEqual(selected["sub"]["Name"], "Empty")
        self.assertIn("TP Bonus", changed["sub"])

    def test_ranged_projectile_pairs_are_normalized_and_validated(self):
        selected = gearset(
            ranged=item("Gun", "Gun", "Marksmanship"),
            ammo=item("Arrow", "Arrow"),
        )
        apply_weapon_slot_rules(selected, "cor", "nin", 50)
        self.assertEqual(selected["ammo"]["Name"], "Empty")
        self.assertFalse(ranged_attack_ready(selected))

        selected["ammo"] = item("Bullet", "Bullet")
        self.assertTrue(ranged_attack_ready(selected))

    def test_instrument_clears_ammo(self):
        selected = gearset(
            ranged=item("Harp", "Instrument", "Singing"),
            ammo=item("Tathlum", "Equipment"),
        )
        apply_weapon_slot_rules(selected, "brd", "whm", 50)
        self.assertEqual(selected["ammo"]["Name"], "Empty")

    def test_steelflash_set_bonus_requires_bladeborn_in_other_ear(self):
        selected = {
            "ear1": item("Steelflash Earring", "Armor"),
            "ear2": item("Other Earring", "Armor"),
        }
        self.assertEqual(conditional_gear_bonuses(selected), {})
        self.assertTrue(has_conditional_set_effect(selected["ear1"]))

        selected["ear2"] = item("Bladeborn Earring", "Armor")
        self.assertEqual(conditional_gear_bonuses(selected), {"DA": 7.0})


if __name__ == "__main__":
    unittest.main()
