import unittest

import gear
from new_gui_main import (
    REMA_WEAPON_NAMES, _aspirational_catalog, _compose_profile_payloads,
    _is_r15_variant, _profile_category, _profile_set_descriptor, _profile_ws_name,
    _with_weapon_overlays,
)


class ProfileReportHelperTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
