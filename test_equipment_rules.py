import unittest

from equipment_rules import apply_weapon_slot_rules, ranged_attack_ready
from gear import Empty


def item(name, item_type="Weapon", skill_type="None"):
    return {"Name": name, "Type": item_type, "Skill Type": skill_type}


def gearset(**slots):
    result = {slot: dict(Empty) for slot in ("main", "sub", "ranged", "ammo")}
    result.update(slots)
    return result


class EquipmentRuleTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
