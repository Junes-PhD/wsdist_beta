import unittest

from new_gui_main import _profile_ws_name, _with_weapon_overlays


class ProfileReportHelperTests(unittest.TestCase):
    def test_profile_ws_name_matches_compact_and_short_names(self):
        self.assertEqual(_profile_ws_name("Laststand_Default"), "Last Stand")
        self.assertEqual(_profile_ws_name("Savage_Acc"), "Savage Blade")
        self.assertEqual(_profile_ws_name("Aedge_Hybrid"), "Aeolian Edge")

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
        self.assertEqual(combined["weapon_setup"], "Weapon_DW + Gun_TP")


if __name__ == "__main__":
    unittest.main()
