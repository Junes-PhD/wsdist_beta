import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

import create_player
import gear
from new_gui_main import MainWindow
from simulation_cache import SimulationCache


class SimulationCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp_dir.name)
        self.cache = SimulationCache(self.directory, source_hash="engine-a")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_same_request_restores_payload_and_runtime(self):
        key = self.cache.key_for("optimizer", {"seed": 17, "gear": {"main": "Test"}})
        self.assertTrue(self.cache.put(key, "optimizer", {"metric": 12.5}, 3.25))
        cached = self.cache.get(key, "optimizer")
        self.assertEqual(cached["payload"], {"metric": 12.5})
        self.assertEqual(cached["runtime_seconds"], 3.25)

    def test_material_request_or_source_change_misses(self):
        key = self.cache.key_for("optimizer", {"seed": 17, "buff": 1})
        self.cache.put(key, "optimizer", {"ok": True}, 1)
        self.assertIsNone(self.cache.get(self.cache.key_for("optimizer", {"seed": 18, "buff": 1}), "optimizer"))
        changed_engine = SimulationCache(self.directory, source_hash="engine-b")
        self.assertIsNone(changed_engine.get(key, "optimizer"))

    def test_corrupt_and_expired_entries_are_discarded(self):
        key = self.cache.key_for("quick-look", {"action": "ws"})
        self.cache.put(key, "quick-look", {"text": "ok"}, 1)
        connection = sqlite3.connect(self.cache.path)
        try:
            connection.execute("UPDATE simulation_results SET payload = ? WHERE cache_key = ?", ("not json", key))
            connection.commit()
        finally:
            connection.close()
        self.assertIsNone(self.cache.get(key, "quick-look"))

        expiring = SimulationCache(self.directory, max_age_seconds=1, source_hash="engine-a")
        old_key = expiring.key_for("quick-look", {"action": "spell"})
        expiring.put(old_key, "quick-look", {"text": "old"}, 1)
        connection = sqlite3.connect(expiring.path)
        try:
            connection.execute(
                "UPDATE simulation_results SET created_at = ? WHERE cache_key = ?", (time.time() - 5, old_key)
            )
            connection.commit()
        finally:
            connection.close()
        self.assertIsNone(expiring.get(old_key, "quick-look"))

    def test_clear_does_not_require_removing_cache_directory(self):
        key = self.cache.key_for("optimizer", {"seed": 1})
        self.cache.put(key, "optimizer", {"metric": 1}, 1)
        self.assertTrue(self.cache.clear())
        self.assertEqual(self.cache.summary(), {"entries": 0, "bytes": 0})
        self.assertTrue(self.cache.path.exists())

    def test_optimizer_payload_restores_player_and_top_set(self):
        gearset = {slot: dict(gear.Empty) for slot in (
            "main", "sub", "ranged", "ammo", "head", "neck", "ear1", "ear2",
            "body", "hands", "ring1", "ring2", "back", "waist", "legs", "feet",
        )}
        player = create_player.create_player("nin", "war", 50, gearset, {}, {})
        result = (player, [123.0, 45.0, 1.0], 123.0, 17, [{
            "rank": 1, "player": player, "metric": 123.0, "seed": 17,
        }])
        saved = MainWindow._serialize_optimizer_payload(None, result)
        restored = MainWindow._restore_optimizer_payload(None, saved, {
            "main_job": "nin", "sub_job": "war", "master_level": 50,
            "buffs": {}, "abilities": {},
        })
        self.assertEqual(restored[2:4], (123.0, 17))
        self.assertEqual(restored[0].gearset["main"]["Name"], "Empty")
        self.assertEqual(restored[4][0]["player"].gearset["main"]["Name"], "Empty")


if __name__ == "__main__":
    unittest.main()
