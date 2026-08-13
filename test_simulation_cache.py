import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

import create_player
import gear
from qt_gui_main import MainWindow
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
        self.assertEqual(changed_engine.summary()["entries"], 0)

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
        summary = self.cache.summary()
        self.assertEqual(summary["entries"], 0)
        self.assertEqual(summary["bytes"], 0)
        self.assertGreater(summary["disk_bytes"], 0)
        self.assertTrue(self.cache.path.exists())

    def test_lru_pruning_keeps_payload_within_configured_budget(self):
        cache = SimulationCache(
            self.directory, max_bytes=160, source_hash="engine-a"
        )
        for index in range(5):
            key = cache.key_for("quick-look", {"index": index})
            self.assertTrue(cache.put(key, "quick-look", {"value": "x" * 80}, 1))
        summary = cache.summary()
        self.assertLessEqual(summary["bytes"], 160)
        self.assertLess(summary["entries"], 5)

    def test_corrupt_database_is_quarantined_and_recreated(self):
        self.temp_dir.cleanup()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp_dir.name)
        path = self.directory / "simulation-results.sqlite3"
        path.write_bytes(b"not a sqlite database")
        cache = SimulationCache(self.directory, source_hash="engine-a")

        self.assertEqual(cache.summary()["entries"], 0)
        self.assertTrue(path.exists())
        self.assertTrue(list(self.directory.glob("*.corrupt-*")))

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
